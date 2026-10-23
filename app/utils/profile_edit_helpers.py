"""Save profile text and optional images as one database transaction."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from flask import current_app, request
from werkzeug.exceptions import BadRequest, RequestEntityTooLarge

from app.domain.enums import Pompfen
from app.utils.profile_banner_helpers import (
    MAX_BANNER_BYTES,
    OUTPUT_BANNER_SIZE,
    BannerUploadError as ProfileEditError,
    _banner_directory,
    _remove_owned_banner,
    normalize_image,
)
from app.utils.profile_photo_helpers import OUTPUT_PHOTO_SIZE, _profile_directory, _remove_owned_photo
from models import db

MAX_PROFILE_EDIT_BYTES = 17 * 1024 * 1024
_FIELDS = {
    "player": ("name", "phone", "location", "bio", "pronouns"),
    "team": ("name", "location", "email", "website", "about"),
}
_IMAGES = ("profile_photo", "banner_photo")


def _read_edit() -> tuple[dict, dict, set[str]]:
    """Bound the whole request before Flask parses multipart file streams."""
    limit = min(request.max_content_length or MAX_PROFILE_EDIT_BYTES, MAX_PROFILE_EDIT_BYTES)
    request.max_content_length = limit
    if request.content_length is not None and request.content_length > limit:
        raise ProfileEditError("The profile update is too large. Choose images no larger than 8 MiB each.", 413)
    try:
        if request.mimetype != "multipart/form-data":
            data = request.get_json(silent=True)
            if not isinstance(data, dict) or not data:
                raise ProfileEditError("Provide a profile JSON object.")
            return data, {}, set()
        form, files = request.form, request.files
        allowed = {"profile", "remove_profile_photo", "remove_banner_photo"}
        if set(form) - allowed or set(files) - set(_IMAGES):
            raise ProfileEditError("Unknown profile upload field.")
        if len(form.getlist("profile")) != 1 or any(len(form.getlist(key)) != 1 for key in form):
            raise ProfileEditError("Provide one profile JSON object and one value per field.")
        try:
            data = json.loads(form["profile"])
        except (TypeError, ValueError) as error:
            raise ProfileEditError("Provide a valid profile JSON object.") from error
        if not isinstance(data, dict):
            raise ProfileEditError("Provide a profile JSON object.")
        removals = set()
        uploads = {}
        for field in _IMAGES:
            flag = form.get("remove_" + field)
            if flag is not None:
                if flag != "true":
                    raise ProfileEditError("Image removal flags must be true when supplied.")
                removals.add(field)
            parts = files.getlist(field)
            if len(parts) > 1 or (parts and field in removals):
                raise ProfileEditError("Choose either a replacement image or removal, not both.")
            if parts:
                uploads[field] = parts[0].stream.read(MAX_BANNER_BYTES + 1)
                if len(uploads[field]) > MAX_BANNER_BYTES:
                    raise ProfileEditError("Choose images no larger than 8 MiB each.", 413)
        return data, uploads, removals
    except RequestEntityTooLarge as error:
        raise ProfileEditError(
            "The profile update is too large. Choose images no larger than 8 MiB each.", 413
        ) from error
    except BadRequest as error:
        raise ProfileEditError("The profile upload could not be read. Please try again.") from error


def _validate_fields(entity, account_type: str, data: dict) -> dict:
    fields = {}
    for field in _FIELDS[account_type]:
        if field not in data:
            continue
        value = data[field]
        column = entity.__table__.columns[field]
        label = field.replace("_", " ").capitalize()
        if value is None and column.nullable:
            fields[field] = None
            continue
        if not isinstance(value, str) or (field == "name" and not value.strip()):
            raise ProfileEditError(f"{label} must be {'nonempty text' if field == 'name' else 'text'}.")
        if field == "pronouns":
            value = value.strip()
        length = getattr(column.type, "length", None)
        if length is not None and len(value) > length:
            raise ProfileEditError(f"{label} must be {length} characters or fewer.")
        fields[field] = (value or None) if field == "pronouns" else value
    if account_type == "player" and "pompfen" in data:
        selected = data["pompfen"]
        allowed = [weapon.name for weapon in Pompfen]
        if (
            not isinstance(selected, list)
            or len(selected) > len(allowed)
            or any(not isinstance(value, str) or value not in allowed for value in selected)
            or len(set(selected)) != len(selected)
        ):
            raise ProfileEditError("Pompfen must be a list of distinct supported choices.")
        fields["pompfen"] = [weapon for weapon in allowed if weapon in selected]
    return fields


def save_profile_edit(entity, account_type: str) -> None:
    """Validate everything, stage both images, then commit all changes together.

    Callers must authorize the typed owner before reading the upload. Image
    folders and old-file cleanup use the same ownership checks as raw uploads.
    """
    data, uploads, removals = _read_edit()
    fields = _validate_fields(entity, account_type, data)
    normalized = {
        field: normalize_image(data, output_size=OUTPUT_PHOTO_SIZE if field == "profile_photo" else OUTPUT_BANNER_SIZE)
        for field, data in uploads.items()
    }
    entity_id = entity.id
    previous = {field: getattr(entity, field) for field in _IMAGES}
    created: list[tuple[str, Path]] = []
    replacements = {}
    try:
        for field, (pixels, extension) in normalized.items():
            directory: Path = (
                _profile_directory(account_type, entity_id)
                if field == "profile_photo"
                else _banner_directory(account_type, entity_id)
            )
            folder = "profiles" if field == "profile_photo" else "banners"
            filename = f"{uuid4().hex}.{extension}"
            relative = f"uploads/{folder}/{directory.name}/{filename}"
            directory.mkdir(parents=True, exist_ok=True)
            destination = directory / filename
            with destination.open("xb") as output:
                # Track only files this request actually created, including a
                # partially written file. A UUID collision must never delete it.
                created.append((field, destination))
                output.write(pixels)
            replacements[field] = relative
        for field, value in fields.items():
            setattr(entity, field, value)
        for field in removals:
            setattr(entity, field, None)
        for field, path in replacements.items():
            setattr(entity, field, path)
        db.session.commit()
    except Exception as error:
        try:
            db.session.rollback()
        except Exception:
            # A disconnected database must not prevent filesystem cleanup.
            current_app.logger.exception("Could not roll back profile edit")
        for field, path in created:
            # These exact files were created by this failed request. Unlike
            # old legacy photos they need no reference query, so cleanup also
            # works when the database is unavailable.
            try:
                directory = (
                    _profile_directory(account_type, entity_id)
                    if field == "profile_photo"
                    else _banner_directory(account_type, entity_id)
                )
                if path.parent == directory and not path.is_symlink():
                    path.unlink(missing_ok=True)
            except (OSError, ProfileEditError):
                current_app.logger.warning("Could not remove a staged profile image for %s %s", account_type, entity_id)
        current_app.logger.exception("Could not save profile edit")
        raise ProfileEditError("Your profile could not be saved. Please try again.", 500) from error
    for field in removals | replacements.keys():
        cleanup = _remove_owned_photo if field == "profile_photo" else _remove_owned_banner
        cleanup(previous[field], account_type, entity_id)
