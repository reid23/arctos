"""Connect personalized form links to a privately shared Google response sheet.

Reads the first-row headings and selected registration-code column. Response values
stay in a bounded process cache and are never written to the database or logs.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import hashlib
import hmac
import json
from pathlib import Path
import re
import secrets
from threading import Lock
from time import monotonic
from types import SimpleNamespace
from urllib.parse import parse_qs, quote, urlencode, urlsplit

from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import Request
from google.oauth2.service_account import Credentials
import requests

from app.error_values import Err, Ok, Result, allow_Q
from app.exceptions import ArctosError, ValidationError
from app.models.constants import LONG_NAME_LEN, LONG_URL_LEN

SHEETS_URL = "https://sheets.googleapis.com/v4/spreadsheets"
TOKEN_URL = "https://oauth2.googleapis.com/token"
READONLY_SCOPE = "https://www.googleapis.com/auth/spreadsheets.readonly"
REQUEST_SECONDS = 12
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
CACHE_SECONDS = 10
MAPPING_CACHE_SECONDS = 60
MAX_CACHE_ENTRIES = 32
CODE_PLACEHOLDER = "ARCTOS_CODE"
CODE_PURPOSE = "arctos-registration-form:v1:"
CONFIG_FIELDS = (
    "registration_form_url",
    "registration_form_entry_id",
    "registration_form_sheet_id",
    "registration_form_sheet_gid",
    "registration_form_code_column",
    "registration_form_signing_key",
)
_response_cache: OrderedDict[tuple, tuple[float, frozenset[str]]] = OrderedDict()
_mapping_cache: OrderedDict[tuple, tuple[float, str, str]] = OrderedDict()
_cache_lock = Lock()
_credential_lock = Lock()


def _unavailable() -> ArctosError:
    return ArctosError(
        "The registration form could not be checked. Please try again shortly or contact an organizer.",
        status_code=503,
    )


def _google_url(value: object, hosts: tuple[str, ...]):
    """Validate the URL itself before extracting a Google resource identifier."""
    if not isinstance(value, str) or len(value.strip()) > LONG_URL_LEN:
        return None
    value = value.strip()
    if any(character.isspace() or ord(character) < 32 for character in value):
        return None
    try:
        parsed = urlsplit(value)
        if parsed.scheme != "https" or parsed.netloc not in hosts:
            return None
        return parsed
    except ValueError:
        return None


def _form_location(value: object) -> Result[tuple[str, str], ArctosError]:
    """Extract one code question from Google's generated prefilled link."""
    parsed = _google_url(value, ("docs.google.com",))
    if parsed is None or re.fullmatch(r"/forms/d/(?:e/)?[A-Za-z0-9_-]+/viewform/?", parsed.path) is None:
        return Err(ValidationError("Paste the full prefilled Google Form link from docs.google.com/forms."))
    parameters = parse_qs(parsed.query, keep_blank_values=True)
    entries = [
        name
        for name, values in parameters.items()
        if re.fullmatch(r"entry\.[0-9]{1,20}", name) is not None and CODE_PLACEHOLDER in values
    ]
    if len(entries) != 1 or len(parameters[entries[0]]) != 1:
        return Err(
            ValidationError("Create a prefilled link with ARCTOS_CODE in exactly one registration-code question.")
        )
    # Keep only the form and code question; unrelated prefilled answers must
    # never be copied into another player's personalized link.
    return Ok((f"https://docs.google.com{parsed.path.rstrip('/')}", entries[0].removeprefix("entry.")))


def _sheet_location(value: object) -> Result[tuple[str, int | None], ArctosError]:
    parsed = _google_url(value, ("docs.google.com",))
    if parsed is None:
        return Err(ValidationError("Enter a Google Sheets link from docs.google.com/spreadsheets."))
    match = re.fullmatch(r"/spreadsheets/d/([A-Za-z0-9_-]{1,255})(?:/edit)?/?", parsed.path)
    if match is None:
        return Err(ValidationError("Enter a Google Sheets link from docs.google.com/spreadsheets."))
    tab_ids = parse_qs(parsed.query, keep_blank_values=True).get("gid", [])
    tab_ids += parse_qs(parsed.fragment, keep_blank_values=True).get("gid", [])
    if len(tab_ids) == 0:
        return Ok((match[1], None))
    if any(re.fullmatch(r"[0-9]{1,10}", value) is None for value in tab_ids):
        return Err(ValidationError("The response sheet's tab ID must be a nonnegative number."))
    numbers = {int(value) for value in tab_ids}
    if len(numbers) != 1 or next(iter(numbers)) > 2**31 - 1:
        return Err(ValidationError("Copy the link for one response sheet tab."))
    return Ok((match[1], next(iter(numbers))))


def _registration_code(config, player_id: str) -> Result[str, ArctosError]:
    """Derive a stable, opaque code without storing a token for each player."""
    parsed = _google_url(getattr(config, "registration_form_url", None), ("docs.google.com",))
    entry_id = getattr(config, "registration_form_entry_id", None)
    signing_key = getattr(config, "registration_form_signing_key", None)
    if (
        parsed is None
        or re.fullmatch(r"/forms/d/(?:e/)?[A-Za-z0-9_-]+/viewform", parsed.path) is None
        or parsed.query != ""
        or parsed.fragment != ""
        or not isinstance(entry_id, str)
        or re.fullmatch(r"[0-9]{1,20}", entry_id) is None
        or not isinstance(signing_key, str)
        or re.fullmatch(r"[0-9a-f]{64}", signing_key) is None
    ):
        return Err(_unavailable())
    message = (CODE_PURPOSE + player_id).encode("utf-8")
    digest = hmac.new(bytes.fromhex(signing_key), message, hashlib.sha256).hexdigest()
    return Ok("arctos_" + digest)


def _prefilled_url(form_url: str, entry_id: str, value: str) -> str:
    return form_url + "?" + urlencode({"usp": "pp_url", f"entry.{entry_id}": value})


def _credentials() -> Result[Credentials, ArctosError]:
    """Load server-owned credentials; a changed file replaces the cached key."""
    from flask import current_app

    filename = current_app.config.get("GOOGLE_FORMS_SERVICE_ACCOUNT_FILE", "")
    if not isinstance(filename, str) or filename.strip() == "":
        return Err(_unavailable())
    try:
        path = Path(filename)
        stat = path.stat()
        key = (str(path), stat.st_mtime_ns, stat.st_size)
        with _credential_lock:
            cached = current_app.extensions.get("registration_form_credentials")
            if cached is not None and cached[0] == key:
                return Ok(cached[1])
            credentials = Credentials.from_service_account_file(str(path), scopes=[READONLY_SCOPE])
            credentials = credentials.with_token_uri(TOKEN_URL)
            current_app.extensions["registration_form_credentials"] = (key, credentials)
        return Ok(credentials)
    except (OSError, ValueError, GoogleAuthError):
        return Err(_unavailable())


class _TokenRequest(Request):
    """Apply the same deadline to every token-refresh attempt."""

    def __init__(self, session, deadline: float):
        super().__init__(session=session)
        self.deadline = deadline

    def __call__(self, url, method="GET", body=None, headers=None, **kwargs):
        remaining = self.deadline - monotonic()
        if remaining <= 0 or url != TOKEN_URL:
            raise requests.Timeout("Token refresh deadline exceeded")
        kwargs.pop("timeout", None)
        return super().__call__(
            url,
            method=method,
            body=body,
            headers=headers,
            timeout=(min(3, remaining), remaining),
            allow_redirects=False,
            **kwargs,
        )


def _get_json(session, url: str, params: dict, deadline: float) -> Result[dict, ArctosError]:
    """Read a bounded response without including upstream bodies in errors."""
    remaining = deadline - monotonic()
    if remaining <= 0:
        return Err(_unavailable())
    try:
        with session.get(
            url,
            params=params,
            timeout=(min(3, remaining), remaining),
            allow_redirects=False,
            stream=True,
        ) as response:
            if response.status_code in (403, 404):
                return Err(
                    ArctosError(
                        "The response sheet is unavailable. Check its link and share it with the Arctos service account as a Viewer.",
                        status_code=503,
                    )
                )
            if response.status_code != 200:
                return Err(_unavailable())
            body = bytearray()
            for chunk in response.iter_content(chunk_size=65536):
                body.extend(chunk)
                if len(body) > MAX_RESPONSE_BYTES or monotonic() > deadline:
                    return Err(_unavailable())
            data = json.loads(body)
            if not isinstance(data, dict):
                return Err(_unavailable())
            return Ok(data)
    except (requests.RequestException, ValueError, UnicodeError):
        return Err(_unavailable())


def _column_letter(index: int) -> str:
    """Convert a zero-based column index to Sheets' A, B, ..., AA notation."""
    result = ""
    index += 1
    while index > 0:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result


@allow_Q
def _sheet_column(session, config, deadline: float) -> Result[tuple[int, str, str], ArctosError]:
    """Find the configured tab and its unique registration-code heading."""
    base_url = f"{SHEETS_URL}/{config.registration_form_sheet_id}"
    metadata = _get_json(session, base_url, {"fields": "sheets(properties(sheetId,title))"}, deadline).Q()
    sheets = metadata.get("sheets")
    if not isinstance(sheets, list) or len(sheets) == 0:
        return Err(_unavailable())
    tab = None
    for sheet in sheets:
        properties = sheet.get("properties") if isinstance(sheet, dict) else None
        if not isinstance(properties, dict):
            return Err(_unavailable())
        if (
            config.registration_form_sheet_gid is None
            or properties.get("sheetId") == config.registration_form_sheet_gid
        ):
            tab = properties
            break
    if tab is None:
        return Err(ValidationError("The response sheet tab was not found. Copy its current link."))
    tab_id, title = tab.get("sheetId"), tab.get("title")
    if type(tab_id) is not int or tab_id < 0 or not isinstance(title, str) or title == "":
        return Err(_unavailable())
    # Quoted A1 sheet names escape apostrophes by doubling them.
    sheet_name = "'" + title.replace("'", "''") + "'"
    header_url = f"{base_url}/values/{quote(sheet_name + '!1:1', safe='')}"
    header = _get_json(session, header_url, {"fields": "values"}, deadline).Q()
    rows = header.get("values", [])
    if not isinstance(rows, list) or len(rows) > 1 or (len(rows) == 1 and not isinstance(rows[0], list)):
        return Err(_unavailable())
    headers = rows[0] if len(rows) == 1 else []
    matches = [
        index
        for index, value in enumerate(headers)
        if isinstance(value, str) and value.strip() == config.registration_form_code_column
    ]
    if len(matches) != 1:
        return Err(
            ValidationError(
                "The first row must contain exactly one column with the specified registration-code heading."
            )
        )
    return Ok((tab_id, sheet_name, _column_letter(matches[0])))


@allow_Q
def _read_sheet(config, *, include_responses: bool) -> Result[tuple[int, frozenset[str]], ArctosError]:
    credentials = _credentials().Q()
    deadline = monotonic() + REQUEST_SECONDS
    key = (
        credentials,
        config.registration_form_sheet_id,
        config.registration_form_sheet_gid,
        config.registration_form_code_column,
    )
    with requests.Session() as session:
        try:
            with _credential_lock:
                if not credentials.valid:
                    token_request = _TokenRequest(session, deadline)
                    credentials.refresh(token_request)
            session.headers.update({"Authorization": f"Bearer {credentials.token}"})
        except (GoogleAuthError, requests.RequestException, ValueError):
            return Err(_unavailable())
        with _cache_lock:
            for expired_key, (stored_at, _, _) in list(_mapping_cache.items()):
                if monotonic() - stored_at >= MAPPING_CACHE_SECONDS:
                    del _mapping_cache[expired_key]
            mapping = _mapping_cache.get(key) if include_responses else None
        if mapping is None:
            tab_id, sheet_name, column = _sheet_column(session, config, deadline).Q()
            if not include_responses:
                return Ok((tab_id, frozenset()))
        else:
            _, sheet_name, column = mapping
            tab_id = config.registration_form_sheet_gid

        base_url = f"{SHEETS_URL}/{config.registration_form_sheet_id}"
        values_url = f"{base_url}/values/{quote(sheet_name + '!' + column + '1:' + column, safe='')}"
        data_result = _get_json(
            session, values_url, {"fields": "values", "valueRenderOption": "FORMATTED_VALUE"}, deadline
        )
        # A deleted or renamed tab must be resolved again on the next attempt.
        match data_result:
            case Err(_) as error:
                with _cache_lock:
                    _mapping_cache.pop(key, None)
                return error
            case Ok(data):
                rows = data.get("values", [])
        if not isinstance(rows, list) or len(rows) == 0:
            return Err(_unavailable())
        # Recheck the heading with the values: a column can move while its
        # mapping is cached. Another column must never confirm a registration.
        heading = rows[0]
        if (
            not isinstance(heading, list)
            or len(heading) != 1
            or not isinstance(heading[0], str)
            or heading[0].strip() != config.registration_form_code_column
        ):
            with _cache_lock:
                _mapping_cache.pop(key, None)
            return Err(_unavailable())
        codes = set()
        for row in rows[1:]:
            if not isinstance(row, list) or len(row) > 1:
                return Err(_unavailable())
            if len(row) == 1:
                if not isinstance(row[0], str):
                    return Err(_unavailable())
                codes.add(row[0].strip())
        # Cache mappings only after a complete, successful response read.
        if mapping is None:
            with _cache_lock:
                _mapping_cache[key] = (monotonic(), sheet_name, column)
                _mapping_cache.move_to_end(key)
                while len(_mapping_cache) > MAX_CACHE_ENTRIES:
                    _mapping_cache.popitem(last=False)
        return Ok((tab_id, frozenset(codes)))


@dataclass(frozen=True)
class RegistrationFormService:
    """Configuration, private organizer details, and registration eligibility."""

    @staticmethod
    @allow_Q
    def validate_settings(
        *,
        enabled: bool,
        form_url: object = "",
        sheet_url: object = "",
        code_column: object = "",
        current_config=None,
    ) -> Result[dict[str, str | int | None], ArctosError]:
        """Validate a complete setup and return changes for one atomic save."""
        if not enabled:
            return Ok(dict.fromkeys(CONFIG_FIELDS))
        form, entry_id = _form_location(form_url).Q()
        sheet_id, gid = _sheet_location(sheet_url).Q()
        if not isinstance(code_column, str) or not 1 <= len(code_column.strip()) <= LONG_NAME_LEN:
            return Err(
                ValidationError(f"Enter the registration-code column's heading (up to {LONG_NAME_LEN} characters).")
            )
        signing_key = getattr(current_config, "registration_form_signing_key", None)
        if (
            getattr(current_config, "registration_form_url", None) != form
            or getattr(current_config, "registration_form_entry_id", None) != entry_id
            or not isinstance(signing_key, str)
            or re.fullmatch(r"[0-9a-f]{64}", signing_key) is None
        ):
            # A new form/question gets new codes. Sheet mapping corrections
            # preserve existing responses, independently of Flask session keys.
            signing_key = secrets.token_hex(32)
        values = dict(zip(CONFIG_FIELDS, (form, entry_id, sheet_id, gid, code_column.strip(), signing_key)))
        tab_id, _ = _read_sheet(SimpleNamespace(**values), include_responses=False).Q()
        values["registration_form_sheet_gid"] = tab_id
        return Ok(values)

    @staticmethod
    @allow_Q
    def personal_url(config, player_id: str) -> Result[str, ArctosError]:
        """Build a player's link; callers must authorize access to that player."""
        form_url = getattr(config, "registration_form_url", None)
        if form_url in (None, ""):
            return Err(ValidationError("A registration form is not required."))
        code = _registration_code(config, player_id).Q()
        return Ok(_prefilled_url(form_url, config.registration_form_entry_id, code))

    @staticmethod
    @allow_Q
    def require_response(config, player_id: str) -> Result[None, ArctosError]:
        """Require the target player's code before creating or confirming registration."""
        if config is None or getattr(config, "registration_form_url", None) in (None, ""):
            return Ok(None)
        code = _registration_code(config, player_id).Q()
        sheet_id = getattr(config, "registration_form_sheet_id", None)
        gid = getattr(config, "registration_form_sheet_gid", None)
        column = getattr(config, "registration_form_code_column", None)
        if (
            not isinstance(sheet_id, str)
            or re.fullmatch(r"[A-Za-z0-9_-]{1,255}", sheet_id) is None
            or type(gid) is not int
            or gid < 0
            or not isinstance(column, str)
            or column == ""
        ):
            return Err(_unavailable())
        credentials = _credentials()
        match credentials:
            case Err(_) as error:
                return error
            case Ok(identity):
                # A key replacement cannot reuse the previous account's cache.
                key = (identity, sheet_id, gid, column)
        with _cache_lock:
            for expired_key, (stored_at, _) in list(_response_cache.items()):
                if monotonic() - stored_at >= CACHE_SECONDS:
                    del _response_cache[expired_key]
            cached = _response_cache.get(key)
            codes = cached[1] if cached is not None else None
        if codes is None:
            match _read_sheet(config, include_responses=True):
                case Err(_):
                    return Err(_unavailable())
                case Ok((_, codes)):
                    with _cache_lock:
                        _response_cache[key] = (monotonic(), codes)
                        _response_cache.move_to_end(key)
                        while len(_response_cache) > MAX_CACHE_ENTRIES:
                            _response_cache.popitem(last=False)
        if code not in codes:
            return Err(
                ValidationError(
                    f"No form response was found for @{player_id}. The player must open their personal form on the "
                    "registration page and submit it before continuing. Recent responses can take a few seconds to appear."
                )
            )
        return Ok(None)

    @staticmethod
    def public_config(config) -> dict:
        url = getattr(config, "registration_form_url", None)
        return {"registration_form_required": url not in (None, ""), "registration_form_url": url}

    @staticmethod
    def organizer_config(config) -> dict:
        """Return editable setup and connection details without signing material."""
        sheet_id = getattr(config, "registration_form_sheet_id", None)
        gid = getattr(config, "registration_form_sheet_gid", None)
        sheet_url = (
            f"https://docs.google.com/spreadsheets/d/{sheet_id}/edit#gid={gid}"
            if sheet_id is not None and gid is not None
            else None
        )
        email = None
        match _credentials():
            case Ok(credentials):
                email = credentials.service_account_email
        form_url = getattr(config, "registration_form_url", None)
        entry_id = getattr(config, "registration_form_entry_id", None)
        setup_url = _prefilled_url(form_url, entry_id, CODE_PLACEHOLDER) if form_url and entry_id else None
        return {
            "registration_form_url": setup_url,
            "registration_form_sheet_url": sheet_url,
            "registration_form_code_column": getattr(config, "registration_form_code_column", None),
            "registration_form_service_account_email": email,
            "registration_form_available": email is not None,
        }
