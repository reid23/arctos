"""Validate settings and publish a selected waiver in one database commit.

Waivers are immutable files. Previous documents are retained, including when
requirements are disabled, because registration signatures identify their hash.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from uuid import uuid4

from flask import current_app, request
from werkzeug.exceptions import BadRequest, RequestEntityTooLarge

from app.services.dual_write import set_head_ref_allowlist_from_csv
from models import RegistrableConfig, db

DEFAULT_REQUEST_LIMIT = 100 * 1024 * 1024
CAP_FIELDS = ("n_max_teams", "max_team_size_roster", "max_team_size_field")


class SettingsSaveError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _bound_request():
    limit = request.max_content_length or DEFAULT_REQUEST_LIMIT
    request.max_content_length = limit
    if request.content_length is not None and request.content_length > limit:
        raise SettingsSaveError("The settings upload exceeds the server's upload limit.", 413)
    return limit


def read_waiver(*, required=False):
    """Read one nonempty file within the configured whole-request limit."""
    limit = _bound_request()
    try:
        files = request.files
        if set(files) - {"waiver"} or len(files.getlist("waiver")) > 1:
            raise SettingsSaveError("Choose one waiver file.")
        upload = files.get("waiver")
        if upload is None:
            if required:
                raise SettingsSaveError("Choose a waiver file.")
            return None
        if not upload.filename:
            raise SettingsSaveError("Choose a waiver file with a filename.")
        payload = upload.stream.read(limit + 1)
        if len(payload) > limit:
            raise SettingsSaveError("The waiver exceeds the server's upload limit.", 413)
        if not payload:
            raise SettingsSaveError("The waiver file is empty. Choose a nonempty file.")
        # Keep the existing accepted document formats. The original basename
        # is display metadata only, never a directory or overwrite target.
        filename = re.sub(r"[^a-zA-Z0-9._-]+", "_", upload.filename.replace("\\", "/").split("/")[-1])[:120]
        return payload, filename or "waiver"
    except RequestEntityTooLarge as error:
        raise SettingsSaveError("The settings upload exceeds the server's upload limit.", 413) from error
    except BadRequest as error:
        raise SettingsSaveError("The upload could not be read. Please choose the file again.") from error


def _read_settings(kind):
    _bound_request()
    combined = request.mimetype == "multipart/form-data"
    try:
        if kind == "tournament":
            data = request.form
            if any(len(data.getlist(key)) != 1 for key in data):
                raise SettingsSaveError("Provide one value for each settings field.")
            data = data.to_dict()
        elif combined:
            if set(request.form) != {"settings"} or len(request.form.getlist("settings")) != 1:
                raise SettingsSaveError("Provide one settings JSON object.")
            try:
                data = json.loads(request.form["settings"])
            except (TypeError, ValueError) as error:
                raise SettingsSaveError("Provide a valid settings JSON object.") from error
        else:
            data = request.get_json(silent=True)
        if not isinstance(data, dict):
            raise SettingsSaveError("Provide a settings object.")
        upload = read_waiver() if combined else None
        return data, upload, combined
    except RequestEntityTooLarge as error:
        raise SettingsSaveError("The settings upload exceeds the server's upload limit.", 413) from error
    except BadRequest as error:
        raise SettingsSaveError("The settings could not be read. Please try again.") from error


def _text(data, key, *, default="", limit=None, required=False):
    value = data.get(key, default)
    if not isinstance(value, str) or (required and not value.strip()):
        raise SettingsSaveError(
            f"{key.replace('_', ' ').capitalize()} must be {'nonempty text' if required else 'text'}."
        )
    if limit is not None and len(value) > limit:
        raise SettingsSaveError(f"{key.replace('_', ' ').capitalize()} must be {limit} characters or fewer.")
    return value


def _fee(value, key):
    try:
        if isinstance(value, bool):
            raise ValueError
        fee = Decimal(str(value if value not in (None, "") else 0))
        if not fee.is_finite() or fee < 0 or fee > Decimal("99999999.99"):
            raise ValueError
        return fee
    except (ValueError, InvalidOperation) as error:
        raise SettingsSaveError(f"{key.replace('_', ' ').capitalize()} must be a nonnegative amount.") from error


def _cap(value, key):
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, bool) or not re.fullmatch(r"[0-9]{1,10}", str(value).strip()):
        raise SettingsSaveError(f"{key.replace('_', ' ').capitalize()} must be a whole number or blank.")
    result = int(str(value).strip())
    if result > 2147483647:
        raise SettingsSaveError(f"{key.replace('_', ' ').capitalize()} is too large.")
    return result


def _boolean(data, key):
    value = data[key]
    if not isinstance(value, bool):
        raise SettingsSaveError(f"{key.replace('_', ' ').capitalize()} must be true or false.")
    return value


def _waiver_changes(config, require, upload, combined):
    if upload is not None and config is None:
        raise SettingsSaveError("Registration is not configured for this event.")
    if combined and require is False and upload is not None:
        raise SettingsSaveError("Enable the waiver requirement to save the selected file.")
    if combined and require is True and not upload and not (config and config.waiver_filepath):
        raise SettingsSaveError("Choose a waiver file before requiring signatures.")
    return {"waiver_filepath": None, "waiver_sha256": None} if require is False else {}


def persist_settings(entity, values, config, config_values, upload, kind, scope_id, *, head_refs=None):
    """Stage without overwriting, commit once, and remove only failed new files."""
    created = None
    relative = None
    commit_started = False
    try:
        if upload is not None:
            payload, filename = upload
            # Typed hashed folders also isolate legacy slugs from filesystem paths.
            folder = f"{kind}-{hashlib.sha256(scope_id.encode()).hexdigest()}"
            directory = Path(current_app.static_folder) / "uploads" / "waivers" / folder
            directory.mkdir(parents=True, exist_ok=True)
            destination = directory / f"{uuid4().hex}_{filename}"
            with destination.open("xb") as output:
                created = destination
                output.write(payload)
            relative = f"uploads/waivers/{folder}/{destination.name}"
            config_values = config_values | {
                "waiver_filepath": relative,
                "waiver_sha256": hashlib.sha256(payload).hexdigest(),
            }
        for key, value in values.items():
            setattr(entity, key, value)
        if config is not None:
            for key, value in config_values.items():
                setattr(config, key, value)
        if head_refs is not None:
            set_head_ref_allowlist_from_csv(entity, head_refs)
        commit_started = True
        db.session.commit()
    except Exception as error:
        try:
            db.session.rollback()
        except Exception:
            current_app.logger.exception("Could not roll back settings save")
        if created is not None:
            try:
                # A failed commit acknowledgement can have an uncertain outcome.
                # Retain the file if it is referenced, or the DB cannot establish
                # that it is unreferenced; an orphan is safer than a broken waiver.
                referenced = commit_started and RegistrableConfig.query.filter_by(waiver_filepath=relative).first()
                if not referenced:
                    created.unlink(missing_ok=True)
            except Exception:
                current_app.logger.warning("Could not safely remove a staged waiver", exc_info=True)
        current_app.logger.exception("Could not save settings")
        raise SettingsSaveError(
            "The settings save could not be confirmed. Check the saved settings before trying again; your draft is still available.",
            500,
        ) from error


def save_tournament_settings(tournament):
    data, upload, combined = _read_settings("tournament")
    values = {
        "name": _text(data, "name", required=True, limit=200),
        "location": _text(data, "location", limit=200),
        "about": _text(data, "about"),
    }
    for key in ("head_refs_allow_reffing_teams", "head_refs_allow_anyone", "published", "schedule_published"):
        values[key] = key in data
    head_refs = _text(data, "head_refs_allowed_list")
    for key in ("start_date", "end_date"):
        value = _text(data, key).strip()
        if not value:
            if key == "end_date":
                raise SettingsSaveError("End date is required.")
            continue
        try:
            values[key] = datetime.strptime(value, "%Y-%m-%d")
        except ValueError as error:
            raise SettingsSaveError(f"{key.replace('_', ' ').capitalize()} must be a valid date.") from error
    start = values.get("start_date", tournament.start_date)
    if start and values["end_date"].date() < start.date():
        raise SettingsSaveError("End date must be on or after the start date.")
    config = tournament.registrable_config if not tournament.league_id else None
    if tournament.league_id and upload is not None:
        raise SettingsSaveError("Save the shared waiver in league settings.")
    config_values = {}
    if config is not None:
        config_values = {key: _fee(data.get(key, 0), key) for key in ("team_reg_fee", "player_reg_fee")}
        config_values["terms_link"] = _text(data, "terms_link", limit=500) or None
        for key in ("team_registration_open", "player_registration_open"):
            config_values[key] = key in data
        config_values.update({key: _cap(data.get(key), key) for key in CAP_FIELDS})
        config_values.update(_waiver_changes(config, "require_waiver_signature" in data, upload, combined))
    elif upload is not None:
        raise SettingsSaveError("Registration is not configured for this event.")
    persist_settings(
        tournament, values, config, config_values, upload, "tournament", tournament.url, head_refs=head_refs
    )


def save_league_settings(league):
    data, upload, combined = _read_settings("league")
    values = {}
    if "about" in data:
        values["about"] = None if data["about"] is None else _text(data, "about")
    if "published" in data:
        values["published"] = _boolean(data, "published")
    config = league.registrable_config
    config_values = {}
    if config is not None:
        for key in ("team_reg_fee", "player_reg_fee"):
            if key in data:
                config_values[key] = _fee(data[key], key)
        for key in ("team_registration_open", "player_registration_open"):
            if key in data:
                config_values[key] = _boolean(data, key)
        for key in ("terms_link", "payment_info"):
            if key in data:
                config_values[key] = (
                    None
                    if data[key] is None
                    else (_text(data, key, limit=500 if key == "terms_link" else None) or None)
                )
        for key in CAP_FIELDS:
            if key in data:
                config_values[key] = _cap(data[key], key)
    require = _boolean(data, "require_waiver_signature") if "require_waiver_signature" in data else None
    config_values.update(_waiver_changes(config, require, upload, combined))
    persist_settings(league, values, config, config_values, upload, "league", league.url)
