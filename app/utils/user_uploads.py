from __future__ import annotations

import math
import os
import re
import shutil
import threading
from datetime import datetime
from os import path

from flask import current_app

from models import Camera, Field, Match, Point, db
from app.utils.youtube_upload import upload_camera_to_youtube


def _normalize_world_time(raw: str) -> str:
    """Normalize an ISO 8601 string to a stored world timestamp.

    Accepts a trailing ``Z`` or an explicit offset; returns the string
    unchanged when it parses. Raises ``ValueError`` on empty or
    unparsable input.
    """
    s = (raw or "").strip()
    if not s:
        raise ValueError("world_time must be a non-empty ISO 8601 string")
    candidate = s[:-1] if s.endswith("Z") else s
    try:
        datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise ValueError(f"world_time is not valid ISO 8601: {raw!r}") from exc
    return s


def normalize_anchors(match_uuid: str, tournament_url: str, anchors) -> tuple[list[str], list[float]]:
    """Convert an API ``anchors`` payload into parallel arrays.

    Each element is either ``{"world_time", "video_offset"}`` or
    ``{"point_index", "video_offset"}``. ``point_index`` (0-based, ordered
    by ``Point.stamp``) is resolved to that point's stamp at ingest and
    never stored as an index. ``None`` / empty anchors return ``([], [])``.
    The result is sorted by ``video_offset``. Raises ``ValueError`` on bad
    input (the caller maps this to HTTP 400).
    """
    if not anchors:
        return [], []
    if not isinstance(anchors, list):
        raise ValueError("anchors must be a list")

    points = None
    pairs: list[tuple[str, float]] = []
    for element in anchors:
        if not isinstance(element, dict):
            raise ValueError("each anchor must be an object")
        if "video_offset" not in element:
            raise ValueError("each anchor requires video_offset")
        try:
            offset = float(element["video_offset"])
        except (TypeError, ValueError) as exc:
            raise ValueError("video_offset must be a number") from exc
        if not math.isfinite(offset):
            raise ValueError("video_offset must be a finite number")
        if offset < 0:
            raise ValueError("video_offset must be >= 0")

        if "world_time" in element:
            world = _normalize_world_time(element["world_time"])
        elif "point_index" in element:
            if points is None:
                points = Point.query.filter_by(match=match_uuid).order_by(Point.stamp).all()
            try:
                index = int(element["point_index"])
            except (TypeError, ValueError) as exc:
                raise ValueError("point_index must be an integer") from exc
            if index < 0 or index >= len(points):
                raise ValueError(f"point_index {index} out of range (match has {len(points)} points)")
            stamp = points[index].stamp
            if stamp is None:
                raise ValueError(f"point {index} has no timestamp")
            world = _normalize_world_time(stamp.isoformat() + "Z")
        else:
            raise ValueError("each anchor requires world_time or point_index")

        pairs.append((world, offset))

    pairs.sort(key=lambda pair: pair[1])
    worlds = [pair[0] for pair in pairs]
    videos = [pair[1] for pair in pairs]
    return worlds, videos


def _slug_camera_dir(s: str, max_len: int = 48) -> str:
    t = re.sub(r"[^a-zA-Z0-9._-]+", "_", s.strip())[:max_len]
    return t or "cam"


def _camera_fs_dir_name(batch_id: str, camera_display_name: str) -> str:
    slug = _slug_camera_dir(camera_display_name)
    name = f"{batch_id}_{slug}"
    return name[:120]


def create_direct_user_upload_camera(
    logger,
    app_obj,
    *,
    tournament_url: str,
    match_uuid: str,
    camera_name: str,
    upload_key: str,
    saved_abs_path: str,
    uploader_user_id: str,
    uploader_user_type: str,
    anchors=None,
) -> str:
    """
    Create a single match-scoped camera from an upload and start the YouTube
    upload immediately. Optional ``anchors`` are normalized into
    ``camera_timepoints`` for jump-to-point playback.
    """
    _log = logger or current_app.logger

    match_obj = Match.query.filter_by(uuid=match_uuid, event=tournament_url).first()
    if not match_obj:
        raise ValueError("Match not found")
    if not match_obj.field:
        raise ValueError("Selected match has no field")

    field_obj = Field.query.filter_by(event=tournament_url, name=match_obj.field).first()
    if not field_obj:
        raise ValueError("Match field not found")

    display_name = (camera_name or "").strip() or (path.splitext(path.basename(saved_abs_path))[0] or "upload")
    if len(display_name) > 200:
        display_name = display_name[:200]

    ext = path.splitext(path.basename(saved_abs_path))[1].lower() or ".webm"
    if ext not in {".mp4", ".webm"}:
        raise ValueError("uploaded file must be .mp4 or .webm")
    camera_fs_name = _camera_fs_dir_name(upload_key, display_name)
    field_seg = str(field_obj.id)
    videos_root = path.realpath(path.join(current_app.root_path, "..", "static", "uploads", "videos"))
    match_out_dir = path.realpath(path.join(videos_root, tournament_url, field_seg, match_uuid, camera_fs_name))
    if match_out_dir != videos_root and not match_out_dir.startswith(videos_root + os.sep):
        raise ValueError("Invalid upload path")
    os.makedirs(match_out_dir, exist_ok=True)

    final_abs_path = path.join(match_out_dir, f"source{ext}")
    saved_abs_path = path.abspath(path.normpath(saved_abs_path))
    shutil.move(saved_abs_path, final_abs_path)

    source_dir = path.dirname(saved_abs_path)
    if path.isdir(source_dir):
        try:
            shutil.rmtree(source_dir)
        except OSError:
            _log.warning(
                "direct user upload: could not remove temp dir %s",
                source_dir,
            )

    final_rel = path.join(
        "static",
        "uploads",
        "videos",
        tournament_url,
        field_seg,
        match_uuid,
        camera_fs_name,
        f"source{ext}",
    ).replace("\\", "/")

    camera_row = Camera(
        match_uuid=match_uuid,
        event=tournament_url,
        field=field_obj.id,
        name=display_name,
        source_type="user_upload",
        uploaded_by_user_id=uploader_user_id,
        uploaded_by_user_type=uploader_user_type,
        status="UPLOADING",
        file=final_rel,
    )
    db.session.add(camera_row)
    db.session.flush()

    worlds, videos = normalize_anchors(match_uuid, tournament_url, anchors)
    if worlds:
        from app.services.dual_write import set_camera_timepoints

        set_camera_timepoints(camera_row, worlds, videos)

    db.session.commit()

    def _yt_upload():
        with app_obj.app_context():
            upload_camera_to_youtube(str(camera_row.uuid))

    threading.Thread(target=_yt_upload, daemon=True).start()

    _log.info(
        "direct user upload: created camera uuid=%s match=%s",
        camera_row.uuid,
        match_uuid,
    )
    return str(camera_row.uuid)
