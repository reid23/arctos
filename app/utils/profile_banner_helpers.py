"""Shared raster validation and safe storage for account and event banners."""

from __future__ import annotations

from hashlib import sha256
from io import BytesIO
from pathlib import Path, PurePosixPath
import re
from uuid import uuid4
import warnings

from flask import current_app, request
from PIL import Image, ImageOps, UnidentifiedImageError
from werkzeug.exceptions import RequestEntityTooLarge

from models import db

MAX_BANNER_BYTES = 8 * 1024 * 1024
MAX_BANNER_DIMENSION = 8192
MAX_BANNER_PIXELS = 24_000_000
OUTPUT_BANNER_SIZE = (2400, 1600)
_BANNER_FILENAME = re.compile(r"[0-9a-f]{32}\.(?:jpg|png)")


class BannerUploadError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def read_banner_upload() -> bytes:
    return read_image_upload()


def read_image_upload() -> bytes:
    """Read at most the image limit plus one byte, including streamed bodies."""
    if request.content_length is not None and request.content_length > MAX_BANNER_BYTES:
        raise BannerUploadError("Choose an image no larger than 8 MiB.", 413)
    try:
        data = request.stream.read(MAX_BANNER_BYTES + 1)
    except RequestEntityTooLarge as error:
        raise BannerUploadError("Choose an image no larger than 8 MiB.", 413) from error
    if len(data) > MAX_BANNER_BYTES:
        raise BannerUploadError("Choose an image no larger than 8 MiB.", 413)
    return data


def normalize_banner(data: bytes) -> tuple[bytes, str]:
    return normalize_image(data, output_size=OUTPUT_BANNER_SIZE)


def normalize_image(data: bytes, *, output_size: tuple[int, int]) -> tuple[bytes, str]:
    """Decode a bounded still raster and re-encode its pixels without metadata."""
    if not data:
        raise BannerUploadError("Choose a JPEG, PNG, or WebP image.")
    if len(data) > MAX_BANNER_BYTES:
        raise BannerUploadError("Choose an image no larger than 8 MiB.", 413)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data), formats=("JPEG", "PNG", "WEBP")) as source:
                width, height = source.size
                if max(width, height) > MAX_BANNER_DIMENSION or width * height > MAX_BANNER_PIXELS:
                    raise BannerUploadError("Choose an image up to 8192 pixels per side and 24 megapixels.")
                if getattr(source, "is_animated", False):
                    raise BannerUploadError("Choose a still JPEG, PNG, or WebP image.")
                source.verify()
            with Image.open(BytesIO(data), formats=("JPEG", "PNG", "WEBP")) as source:
                source.load()
                oriented = ImageOps.exif_transpose(source)
                transparent = "A" in oriented.getbands() or "transparency" in oriented.info
                pixels = oriented.convert("RGBA" if transparent else "RGB")
                pixels.thumbnail(output_size, Image.Resampling.LANCZOS)
                # A fresh pixel-only image drops EXIF/GPS, comments, and other
                # embedded metadata even when the decoder preserves info.
                clean = Image.new(pixels.mode, pixels.size)
                clean.paste(pixels)
                output = BytesIO()
                if transparent:
                    clean.save(output, format="PNG", optimize=True)
                    extension = "png"
                else:
                    clean.save(output, format="JPEG", quality=85, optimize=True, progressive=True)
                    extension = "jpg"
                return output.getvalue(), extension
    except (
        UnidentifiedImageError,
        OSError,
        SyntaxError,
        ValueError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as error:
        raise BannerUploadError("Choose a valid JPEG, PNG, or WebP image.") from error


def _owner_folder(account_type: str, entity_id: str) -> str:
    if account_type not in ("player", "team", "tournament", "league"):
        raise ValueError("Unknown banner owner type")
    return f"{account_type}-{sha256(entity_id.encode('utf-8')).hexdigest()}"


def _banner_directory(account_type: str, entity_id: str) -> Path:
    static_root = Path(current_app.static_folder).resolve()
    directory = static_root / "uploads" / "banners" / _owner_folder(account_type, entity_id)
    if directory.resolve() != directory:
        raise BannerUploadError("Banner storage is unavailable. Please try again later.", 500)
    return directory


def _remove_owned_banner(path: str | None, account_type: str, entity_id: str) -> None:
    """Never delete avatars, another owner's image, or arbitrary stored paths."""
    if not path:
        return
    relative = PurePosixPath(path)
    parent = PurePosixPath("uploads/banners") / _owner_folder(account_type, entity_id)
    if relative.parent != parent or not _BANNER_FILENAME.fullmatch(relative.name):
        return
    try:
        directory = _banner_directory(account_type, entity_id)
        candidate = directory / relative.name
        if not candidate.is_symlink():
            candidate.unlink(missing_ok=True)
    except (OSError, BannerUploadError):
        current_app.logger.warning("Could not remove an old banner for %s %s", account_type, entity_id)


def replace_profile_banner(entity, account_type: str, data: bytes) -> str:
    return replace_banner(entity, account_type, entity.id, data)


def replace_banner(entity, owner_type: str, owner_id: str, data: bytes) -> str:
    """Replace a banner within a typed owner scope, after the caller authorizes it."""
    normalized, extension = normalize_banner(data)
    directory = _banner_directory(owner_type, owner_id)
    filename = f"{uuid4().hex}.{extension}"
    relative = f"uploads/banners/{directory.name}/{filename}"
    destination = directory / filename
    previous = entity.banner_photo
    created = False
    try:
        directory.mkdir(parents=True, exist_ok=True)
        with destination.open("xb") as output:
            created = True
            output.write(normalized)
        entity.banner_photo = relative
        db.session.commit()
    except Exception as error:
        db.session.rollback()
        if created:
            _remove_owned_banner(relative, owner_type, owner_id)
        current_app.logger.exception("Could not save banner")
        raise BannerUploadError("Your banner could not be saved. Please try again.", 500) from error
    # Keep the previous file until both the new file and database update succeed.
    _remove_owned_banner(previous, owner_type, owner_id)
    return relative


def remove_profile_banner(entity, account_type: str) -> None:
    remove_banner(entity, account_type, entity.id)


def remove_banner(entity, owner_type: str, owner_id: str) -> None:
    """Clear a banner and remove only a file belonging to the authorized scope."""
    previous = entity.banner_photo
    try:
        entity.banner_photo = None
        db.session.commit()
    except Exception as error:
        db.session.rollback()
        current_app.logger.exception("Could not remove banner")
        raise BannerUploadError("Your banner could not be removed. Please try again.", 500) from error
    _remove_owned_banner(previous, owner_type, owner_id)
