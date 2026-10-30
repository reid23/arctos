"""Validated avatar uploads with isolated storage and conservative legacy cleanup."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path, PurePosixPath
import re
from uuid import uuid4

from flask import current_app
from sqlalchemy import exists, or_
from sqlalchemy.exc import SQLAlchemyError

from app.utils.profile_banner_helpers import BannerUploadError as PhotoUploadError
from app.utils.profile_banner_helpers import normalize_image, read_image_upload
from models import Player, Team, db

OUTPUT_PHOTO_SIZE = (1024, 1024)
_PHOTO_FILENAME = re.compile(r"[0-9a-f]{32}\.(?:jpg|png)")


def safe_profile_photo_filename(prefix: str, entity_id: str) -> str:
    """Return the legacy filename, only for recognizing safe cleanup candidates."""
    if prefix not in ("player", "team"):
        raise ValueError("Unknown profile photo owner type")
    safe = "".join(c if c.isalnum() or c == "_" else "_" for c in entity_id)
    return f"{prefix}_{safe}.jpg"


def read_profile_photo_upload() -> bytes:
    return read_image_upload()


def _profiles_root() -> Path:
    directory = Path(current_app.static_folder).resolve() / "uploads" / "profiles"
    if directory.resolve() != directory:
        raise PhotoUploadError("Photo storage is unavailable. Please try again later.", 500)
    return directory


def _profile_directory(account_type: str, entity_id: str) -> Path:
    if account_type not in ("player", "team"):
        raise ValueError("Unknown profile photo owner type")
    directory = _profiles_root() / f"{account_type}-{sha256(entity_id.encode('utf-8')).hexdigest()}"
    if directory.resolve() != directory:
        raise PhotoUploadError("Photo storage is unavailable. Please try again later.", 500)
    return directory


def _remove_owned_photo(path: str | None, account_type: str, entity_id: str) -> None:
    """Remove only this owner's image when no account still references it."""
    if not path:
        return
    relative = PurePosixPath(path)
    legacy = f"uploads/profiles/{safe_profile_photo_filename(account_type, entity_id)}"
    try:
        if path == legacy:
            candidate = _profiles_root() / relative.name
        else:
            directory = _profile_directory(account_type, entity_id)
            expected_parent = PurePosixPath("uploads/profiles") / directory.name
            if (
                relative.parent != expected_parent
                or path != relative.as_posix()
                or not _PHOTO_FILENAME.fullmatch(relative.name)
            ):
                return
            candidate = directory / relative.name
        if candidate.is_symlink():
            return
        # Legacy sanitized filenames can be shared by distinct usernames.
        # Check both account types after the successful update/clear.
        referenced = db.session.query(
            or_(exists().where(Player.profile_photo == path), exists().where(Team.profile_photo == path))
        ).scalar()
        if not referenced:
            candidate.unlink(missing_ok=True)
    except SQLAlchemyError:
        # Keep the file when its reference cannot be checked. Request teardown
        # will dispose of this session; cleanup must not mask the save result.
        current_app.logger.warning("Could not check old profile photo references for %s %s", account_type, entity_id)
    except (OSError, PhotoUploadError):
        current_app.logger.warning("Could not remove an old profile photo for %s %s", account_type, entity_id)


def replace_profile_photo(entity, account_type: str, data: bytes) -> str:
    normalized, extension = normalize_image(data, output_size=OUTPUT_PHOTO_SIZE)
    entity_id = entity.id
    directory = _profile_directory(account_type, entity_id)
    filename = f"{uuid4().hex}.{extension}"
    relative = f"uploads/profiles/{directory.name}/{filename}"
    destination = directory / filename
    previous = entity.profile_photo
    created = False
    try:
        directory.mkdir(parents=True, exist_ok=True)
        with destination.open("xb") as output:
            created = True
            output.write(normalized)
        entity.profile_photo = relative
        db.session.commit()
    except Exception as error:
        try:
            db.session.rollback()
        except Exception:
            current_app.logger.exception("Could not roll back profile photo save")
        if created:
            _remove_owned_photo(relative, account_type, entity_id)
        current_app.logger.exception("Could not save profile photo")
        raise PhotoUploadError(
            "The photo save could not be confirmed. Check the saved image before trying again.", 500
        ) from error
    _remove_owned_photo(previous, account_type, entity_id)
    return relative


def remove_profile_photo(entity, account_type: str) -> None:
    entity_id = entity.id
    previous = entity.profile_photo
    try:
        entity.profile_photo = None
        db.session.commit()
    except Exception as error:
        db.session.rollback()
        current_app.logger.exception("Could not remove profile photo")
        raise PhotoUploadError("Your photo could not be removed. Please try again.", 500) from error
    _remove_owned_photo(previous, account_type, entity_id)
