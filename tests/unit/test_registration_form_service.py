"""Personalized links identify responses without exposing other account data."""

import json
from types import SimpleNamespace
from urllib.parse import parse_qs, unquote, urlsplit

import pytest
import requests
from google.auth.exceptions import RefreshError

from app.error_values import Ok
from app.services import registration_form_service as forms
from app.services.registration_form_service import RegistrationFormService as Service


class FakeCredentials:
    valid = True
    token = "private-test-token"
    service_account_email = "forms@example.iam.gserviceaccount.com"


class Response:
    def __init__(self, payload=None, *, status=200, body=None):
        self.status_code = status
        self.body = json.dumps(payload).encode() if body is None else body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def iter_content(self, chunk_size):
        for index in range(0, len(self.body), chunk_size):
            yield self.body[index : index + chunk_size]


@pytest.fixture(autouse=True)
def clear_cache():
    forms._response_cache.clear()
    forms._mapping_cache.clear()
    yield
    forms._response_cache.clear()
    forms._mapping_cache.clear()


@pytest.fixture
def config():
    return SimpleNamespace(
        registration_form_url="https://docs.google.com/forms/d/e/Form123/viewform",
        registration_form_entry_id="123",
        registration_form_signing_key="12" * 32,
        registration_form_sheet_id="Sheet_123",
        registration_form_sheet_gid=0,
        registration_form_code_column="Arctos registration code",
    )


@pytest.fixture
def google(monkeypatch):
    credentials = FakeCredentials()
    monkeypatch.setattr(forms, "_credentials", lambda: Ok(credentials))
    queued, calls = [], []

    class Session:
        def __init__(self):
            self.headers = {}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def close(self):
            pass

        def get(self, url, **kwargs):
            calls.append((url, kwargs, dict(self.headers)))
            response = queued.pop(0)
            if isinstance(response, Exception):
                raise response
            return response

    monkeypatch.setattr(forms.requests, "Session", Session)

    def sheet(codes=(), *, title="Form Responses 1", gid=0, headers=None, include_responses=True):
        queued.extend(
            [
                Response({"sheets": [{"properties": {"sheetId": gid, "title": title}}]}),
                Response({"values": [headers if headers is not None else ["Timestamp", "Arctos registration code"]]}),
            ]
        )
        if include_responses:
            queued.append(Response({"values": [["Arctos registration code"], *[[value] for value in codes]]}))

    return SimpleNamespace(queued=queued, calls=calls, sheet=sheet, credentials=credentials)


def error(result, status):
    assert result.is_err()
    value = result.unwrap_err()
    assert value.status_code == status
    return value.message


def code(config, player_id="Alice"):
    url = Service.personal_url(config, player_id).unwrap()
    return parse_qs(urlsplit(url).query)[f"entry.{config.registration_form_entry_id}"][0]


def test_disabled_requirement_does_not_load_credentials(monkeypatch):
    def unexpected():
        pytest.fail("Disabled requirements must not access Google credentials")

    monkeypatch.setattr(forms, "_credentials", unexpected)
    assert Service.require_response(None, "Alice").is_ok()
    assert Service.require_response(SimpleNamespace(registration_form_url=None), "Alice").is_ok()
    assert Service.validate_settings(enabled=False).unwrap() == dict.fromkeys(forms.CONFIG_FIELDS)
    error(Service.personal_url(None, "Alice"), 400)


@pytest.mark.parametrize(
    "url",
    [
        "http://docs.google.com/forms/d/e/Form/viewform",
        "https://docs.google.com.evil.example/forms/d/e/Form/viewform",
        "https://docs.google.com@evil.example/forms/d/e/Form/viewform",
        "https://user:password@docs.google.com/forms/d/e/Form/viewform",  # pragma: allowlist secret
        "https://docs.google.com:443/forms/d/e/Form/viewform",
        "https://docs.google.com/forms/d/e/Form/edit",
        "https://docs.google.com/forms/d/e/Form/viewform",
        "https://forms.gle/ABC",
        "https://forms.gle/path/extra",
        "https://forms.gle/a\nb",
        None,
        12,
    ],
)
def test_invalid_form_links_are_rejected_before_network(google, url):
    error(Service.validate_settings(enabled=True, form_url=url), 400)
    assert google.calls == []


@pytest.mark.parametrize(
    "parameters",
    [
        "entry.123=Alice",
        "entry.other=ARCTOS_CODE",
        "entry.123=ARCTOS_CODE&entry.456=ARCTOS_CODE",
        "entry.123=ARCTOS_CODE&entry.123=ARCTOS_CODE",
        "entry.123=ARCTOS_CODE&entry.123=another-answer",
        "entry.123=",
        "usp=ARCTOS_CODE",
    ],
)
def test_ambiguous_or_missing_code_questions_are_rejected(google, parameters):
    url = "https://docs.google.com/forms/d/e/Form/viewform?" + parameters
    error(Service.validate_settings(enabled=True, form_url=url), 400)
    assert google.calls == []


def test_setup_discards_unrelated_prefilled_answers(google):
    google.sheet(include_responses=False)
    values = Service.validate_settings(
        enabled=True,
        form_url="https://docs.google.com/forms/d/e/Form/viewform?entry.123=ARCTOS_CODE&entry.456=private-answer&emailAddress=person%40example.com",
        sheet_url="https://docs.google.com/spreadsheets/d/Sheet/edit#gid=0",
        code_column="Arctos registration code",
    ).unwrap()
    saved = SimpleNamespace(**values)
    personal = Service.personal_url(saved, "Alice").unwrap()
    assert set(parse_qs(urlsplit(personal).query)) == {"usp", "entry.123"}
    assert "private-answer" not in personal and "example.com" not in personal
    setup = Service.organizer_config(saved)["registration_form_url"]
    assert setup.endswith("?usp=pp_url&entry.123=ARCTOS_CODE")


def test_personal_codes_are_stable_and_scoped_to_player_and_config(google, config):
    first = code(config)
    assert code(config) == first
    assert len(first) == 71 and first.startswith("arctos_")
    assert "Alice" not in first
    assert code(config, "alice") != first
    assert code(config, "Bob") != first
    other_config = SimpleNamespace(**vars(config))
    other_config.registration_form_signing_key = "34" * 32
    assert code(other_config) != first
    assert google.calls == []


def test_sheet_mapping_corrections_preserve_existing_player_codes(google, config):
    google.sheet(gid=8, headers=["New code heading"], include_responses=False)
    values = Service.validate_settings(
        enabled=True,
        form_url=Service.organizer_config(config)["registration_form_url"],
        sheet_url="https://docs.google.com/spreadsheets/d/CorrectedSheet/edit#gid=8",
        code_column="New code heading",
        current_config=config,
    ).unwrap()
    assert values["registration_form_signing_key"] == config.registration_form_signing_key
    assert code(SimpleNamespace(**values)) == code(config)


@pytest.mark.parametrize(
    "form_url",
    [
        "https://docs.google.com/forms/d/e/DifferentForm/viewform?entry.123=ARCTOS_CODE",
        "https://docs.google.com/forms/d/e/Form123/viewform?entry.456=ARCTOS_CODE",
    ],
)
def test_changing_form_or_question_rotates_player_codes(google, config, form_url):
    google.sheet(include_responses=False)
    before = vars(config).copy()
    values = Service.validate_settings(
        enabled=True,
        form_url=form_url,
        sheet_url="https://docs.google.com/spreadsheets/d/Sheet_123/edit#gid=0",
        code_column=config.registration_form_code_column,
        current_config=config,
    ).unwrap()
    assert values["registration_form_signing_key"] != config.registration_form_signing_key
    assert code(SimpleNamespace(**values)) != code(config)
    assert vars(config) == before


def test_invalid_setup_leaves_current_config_untouched(google, config):
    google.sheet(headers=[], include_responses=False)
    before = vars(config).copy()
    error(
        Service.validate_settings(
            enabled=True,
            form_url=Service.organizer_config(config)["registration_form_url"],
            sheet_url="https://docs.google.com/spreadsheets/d/Sheet_123/edit#gid=0",
            code_column=config.registration_form_code_column,
            current_config=config,
        ),
        400,
    )
    assert vars(config) == before
    assert Service.validate_settings(enabled=False, current_config=config).unwrap() == dict.fromkeys(
        forms.CONFIG_FIELDS
    )


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example/spreadsheets/d/Sheet/edit#gid=0",
        "https://docs.google.com/spreadsheets/d/../../other/edit",
        "https://docs.google.com/spreadsheets/d/Sheet/export",
        "https://docs.google.com/spreadsheets/d/Sheet/edit#gid=-1",
        "https://docs.google.com/spreadsheets/d/Sheet/edit#gid=2147483648",
        "https://docs.google.com/spreadsheets/d/Sheet/edit?gid=1#gid=2",
        "https://docs.google.com/spreadsheets/d/Sheet/edit?gid=",
    ],
)
def test_invalid_sheet_links_are_rejected_before_network(google, url):
    error(
        Service.validate_settings(
            enabled=True,
            form_url="https://docs.google.com/forms/d/e/Form123/viewform?entry.123=ARCTOS_CODE",
            sheet_url=url,
            code_column="Arctos registration code",
        ),
        400,
    )
    assert google.calls == []


@pytest.mark.parametrize(
    "sheet_url,gid",
    [
        ("https://docs.google.com/spreadsheets/d/Sheet_123/edit#gid=0", 0),
        ("https://docs.google.com/spreadsheets/d/Sheet_123/edit?gid=8#gid=8", 8),
        ("https://docs.google.com/spreadsheets/d/Sheet_123/edit?usp=sharing", 12),
    ],
)
def test_settings_resolve_tab_and_read_only_header(google, sheet_url, gid):
    google.sheet(gid=gid, include_responses=False)
    result = Service.validate_settings(
        enabled=True,
        form_url=" https://docs.google.com/forms/d/e/FormABC/viewform?usp=pp_url&entry.123=ARCTOS_CODE ",
        sheet_url=sheet_url,
        code_column=" Arctos registration code ",
    ).unwrap()
    signing_key = result.pop("registration_form_signing_key")
    assert len(bytes.fromhex(signing_key)) == 32
    assert result == {
        "registration_form_url": "https://docs.google.com/forms/d/e/FormABC/viewform",
        "registration_form_entry_id": "123",
        "registration_form_sheet_id": "Sheet_123",
        "registration_form_sheet_gid": gid,
        "registration_form_code_column": "Arctos registration code",
    }
    assert len(google.calls) == 2


@pytest.mark.parametrize("header", [["Email"], ["Arctos registration code", " Arctos registration code "], []])
def test_heading_must_be_unique_in_first_row(google, header):
    google.sheet(headers=header, include_responses=False)
    error(
        Service.validate_settings(
            enabled=True,
            form_url="https://docs.google.com/forms/d/e/Form123/viewform?entry.123=ARCTOS_CODE",
            sheet_url="https://docs.google.com/spreadsheets/d/Sheet/edit#gid=0",
            code_column="Arctos registration code",
        ),
        400,
    )


def test_code_only_read_escapes_tab_title_and_bounds_transport(google, config):
    google.sheet(
        [" " + code(config) + " ", code(config, "bob")],
        title="Players' answers",
        headers=["Private"] * 26 + ["Arctos registration code"],
    )
    assert Service.require_response(config, "Alice").is_ok()
    urls = [unquote(call[0]) for call in google.calls]
    assert urls == [
        "https://sheets.googleapis.com/v4/spreadsheets/Sheet_123",
        "https://sheets.googleapis.com/v4/spreadsheets/Sheet_123/values/'Players'' answers'!1:1",
        "https://sheets.googleapis.com/v4/spreadsheets/Sheet_123/values/'Players'' answers'!AA1:AA",
    ]
    for _, options, headers in google.calls:
        assert options["allow_redirects"] is False and options["stream"] is True
        assert 0 < options["timeout"][0] <= 3
        assert 0 < options["timeout"][1] <= forms.REQUEST_SECONDS
        assert headers["Authorization"] == "Bearer private-test-token"


@pytest.mark.parametrize("value", ["Alice", "alice", "@Alice", "Alice@example.com", "ARCTOS_CODE", ""])
def test_username_and_placeholder_values_cannot_confirm_registration(google, config, value):
    google.sheet([value])
    assert "No form response" in error(Service.require_response(config, "Alice"), 400)


@pytest.mark.parametrize("kind", ["other_player", "other_config", "uppercase", "substring"])
def test_only_the_complete_target_player_code_can_confirm_registration(google, config, kind):
    value = code(config)
    if kind == "other_player":
        value = code(config, "Bob")
    elif kind == "other_config":
        other_config = SimpleNamespace(**vars(config))
        other_config.registration_form_signing_key = "34" * 32
        value = code(other_config)
    elif kind == "uppercase":
        value = value.upper()
    else:
        value = "prefix" + value
    google.sheet([value])
    error(Service.require_response(config, "Alice"), 400)


def test_rotated_key_does_not_accept_old_cached_response(google, config):
    google.sheet([code(config)])
    assert Service.require_response(config, "Alice").is_ok()
    config.registration_form_signing_key = "34" * 32
    error(Service.require_response(config, "Alice"), 400)
    assert len(google.calls) == 3


def test_missing_response_cache_expires_and_a_new_response_is_found(google, config, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(forms, "monotonic", lambda: clock[0])
    google.sheet([])
    error(Service.require_response(config, "Alice"), 400)
    error(Service.require_response(config, "Alice"), 400)
    assert len(google.calls) == 3
    clock[0] = forms.CACHE_SECONDS
    google.queued.append(Response({"values": [["Arctos registration code"], [code(config)]]}))
    assert Service.require_response(config, "Alice").is_ok()
    assert len(google.calls) == 4


def test_cached_responses_are_scoped_and_bounded(google, config, monkeypatch):
    monkeypatch.setattr(forms, "MAX_CACHE_ENTRIES", 2)
    for sheet_id in ("one", "two", "three"):
        config.registration_form_sheet_id = sheet_id
        google.sheet([code(config)] if sheet_id == "one" else [])
        result = Service.require_response(config, "Alice")
        assert result.is_ok() == (sheet_id == "one")
    assert len(forms._response_cache) == 2
    assert len(forms._mapping_cache) == 2


def test_moved_column_cannot_confirm_from_cached_mapping(google, config, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(forms, "monotonic", lambda: clock[0])
    google.sheet([code(config)])
    assert Service.require_response(config, "Alice").is_ok()
    clock[0] = forms.CACHE_SECONDS
    google.queued.append(Response({"values": [["Email"], [code(config)]]}))
    error(Service.require_response(config, "Alice"), 503)
    assert len(forms._mapping_cache) == 0
    assert len(forms._response_cache) == 0
    google.sheet([code(config)], headers=["Timestamp", "Email", "Arctos registration code"])
    assert Service.require_response(config, "Alice").is_ok()
    assert unquote(google.calls[-1][0]).endswith("!C1:C")


def test_mapping_expires_and_settings_validation_always_reads_current_header(google, config, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(forms, "monotonic", lambda: clock[0])
    google.sheet([code(config)])
    assert Service.require_response(config, "Alice").is_ok()
    clock[0] = forms.MAPPING_CACHE_SECONDS
    google.sheet([code(config)])
    assert Service.require_response(config, "Alice").is_ok()
    assert len(google.calls) == 6
    google.sheet(headers=["Arctos registration code", "Arctos registration code"], include_responses=False)
    error(
        Service.validate_settings(
            enabled=True,
            form_url=Service.organizer_config(config)["registration_form_url"],
            sheet_url="https://docs.google.com/spreadsheets/d/Sheet_123/edit#gid=0",
            code_column=config.registration_form_code_column,
        ),
        400,
    )
    assert len(google.calls) == 8


def test_credential_change_cannot_reuse_previous_accounts_responses(google, config, monkeypatch):
    google.sheet([code(config)])
    assert Service.require_response(config, "Alice").is_ok()
    other_credentials = FakeCredentials()
    monkeypatch.setattr(forms, "_credentials", lambda: Ok(other_credentials))
    google.sheet([])
    error(Service.require_response(config, "Alice"), 400)
    assert len(google.calls) == 6


def test_heading_whitespace_is_consistent_during_mapping_and_values_read(google, config):
    google.sheet(headers=[" Arctos registration code "], include_responses=False)
    google.queued.append(Response({"values": [[" Arctos registration code "], [code(config)]]}))
    assert Service.require_response(config, "Alice").is_ok()


@pytest.mark.parametrize(
    "response",
    [
        Response({"error": "private sheet data"}, status=403),
        Response({"error": "private sheet data"}, status=404),
        Response({"error": "private sheet data"}, status=429),
        Response({}, status=302),
        Response({}, status=500),
        Response(body=b"not JSON private sheet data"),
        Response([]),
        Response(body=b" " * (forms.MAX_RESPONSE_BYTES + 1)),
        requests.Timeout("private sheet data"),
    ],
)
def test_upstream_errors_are_unavailable_private_and_not_cached(google, config, response):
    google.queued.append(response)
    message = error(Service.require_response(config, "Alice"), 503)
    assert "private sheet data" not in message
    assert len(forms._response_cache) == 0
    google.sheet([code(config)])
    assert Service.require_response(config, "Alice").is_ok()


@pytest.mark.parametrize("values", ["bad", [["Alice", "extra"]], [[12]], ["Alice"]])
def test_malformed_values_are_not_reported_as_missing_response(google, config, values):
    google.sheet(include_responses=False)
    google.queued.append(Response({"values": values}))
    error(Service.require_response(config, "Alice"), 503)


def test_unknown_tab_does_not_read_other_tabs(google, config):
    google.queued.append(Response({"sheets": [{"properties": {"sheetId": 10, "title": "Private"}}]}))
    error(Service.require_response(config, "Alice"), 503)
    assert len(google.calls) == 1


def test_serializer_helpers_separate_public_and_organizer_details(google, config):
    assert Service.public_config(config) == {
        "registration_form_required": True,
        "registration_form_url": "https://docs.google.com/forms/d/e/Form123/viewform",
    }
    assert Service.organizer_config(config) == {
        "registration_form_url": "https://docs.google.com/forms/d/e/Form123/viewform?usp=pp_url&entry.123=ARCTOS_CODE",
        "registration_form_sheet_url": "https://docs.google.com/spreadsheets/d/Sheet_123/edit#gid=0",
        "registration_form_code_column": "Arctos registration code",
        "registration_form_service_account_email": google.credentials.service_account_email,
        "registration_form_available": True,
    }
    assert google.calls == []


def test_missing_or_invalid_credentials_disable_connection(app, monkeypatch, tmp_path):
    for filename in ("", str(tmp_path / "missing.json"), str(tmp_path / "invalid.json")):
        (tmp_path / "invalid.json").write_text('{"type": "service_account"}')
        monkeypatch.setitem(app.config, "GOOGLE_FORMS_SERVICE_ACCOUNT_FILE", filename)
        with app.app_context():
            error(forms._credentials(), 503)
            assert Service.organizer_config(None)["registration_form_available"] is False


def test_token_refresh_uses_deadline_and_rejects_other_hosts(monkeypatch):
    monkeypatch.setattr(forms, "monotonic", lambda: 20)
    request = forms._TokenRequest(None, deadline=19)
    with pytest.raises(requests.Timeout):
        request(forms.TOKEN_URL)
    request.deadline = 30
    with pytest.raises(requests.Timeout):
        request("https://example.com/token")


def test_token_transport_bounds_timeout_and_prevents_redirects(monkeypatch):
    observed = []

    def transport(self, url, **kwargs):
        observed.append((url, kwargs))

    monkeypatch.setattr(forms.Request, "__call__", transport)
    monkeypatch.setattr(forms, "monotonic", lambda: 20)
    request = forms._TokenRequest(None, deadline=22)
    request(forms.TOKEN_URL, method="POST", body=b"private", timeout=120)
    assert observed[0][0] == forms.TOKEN_URL
    assert observed[0][1]["timeout"] == (2, 2)
    assert observed[0][1]["allow_redirects"] is False


def test_token_failure_is_private_and_does_not_read_sheets(google, config, monkeypatch):
    def refresh(_request):
        raise RefreshError("private credential error")

    google.credentials.valid = False
    monkeypatch.setattr(google.credentials, "refresh", refresh, raising=False)
    assert "private credential error" not in error(Service.require_response(config, "Alice"), 503)
    assert google.calls == []


def test_credential_file_reload_keeps_scope_readonly_and_token_host_fixed(app, monkeypatch, tmp_path):
    path = tmp_path / "service-account.json"
    path.write_text("first key")
    observed = []

    class Credentials:
        service_account_email = "service@example.iam.gserviceaccount.com"

        def with_token_uri(self, uri):
            assert uri == forms.TOKEN_URL
            return self

    def load(filename, *, scopes):
        assert filename == str(path)
        assert scopes == [forms.READONLY_SCOPE]
        credentials = Credentials()
        observed.append(credentials)
        return credentials

    monkeypatch.setitem(app.config, "GOOGLE_FORMS_SERVICE_ACCOUNT_FILE", str(path))
    monkeypatch.setattr(forms.Credentials, "from_service_account_file", load)
    with app.app_context():
        first = forms._credentials().unwrap()
        assert forms._credentials().unwrap() is first
        path.write_text("replacement key")
        assert forms._credentials().unwrap() is not first
        assert len(observed) == 2


@pytest.mark.parametrize(
    "field,value",
    [
        ("registration_form_sheet_id", "../different"),
        ("registration_form_sheet_gid", None),
        ("registration_form_sheet_gid", True),
        ("registration_form_code_column", None),
        ("registration_form_entry_id", None),
        ("registration_form_entry_id", "123&entry.456=other"),
        ("registration_form_signing_key", None),
        ("registration_form_signing_key", "not-a-key"),
        ("registration_form_url", "https://example.com/form"),
        ("registration_form_url", "https://docs.google.com/forms/d/e/Form/viewform?entry.1=other"),
    ],
)
def test_incomplete_stored_config_is_unavailable_without_remote_calls(google, config, field, value):
    setattr(config, field, value)
    error(Service.require_response(config, "Alice"), 503)
    assert google.calls == []
