"""
YouTube upload helpers for match cameras.

This uses YouTube Data API v3 resumable uploads via `requests`.
Credentials are provided via an OAuth refresh token for a Google OAuth client.

Environment variables:
- YOUTUBE_UPLOAD_REFRESH_TOKEN (required)
- GOOGLE_CLIENT_ID (reused if YOUTUBE_UPLOAD_CLIENT_ID not set)
- GOOGLE_CLIENT_SECRET (reused if YOUTUBE_UPLOAD_CLIENT_SECRET not set)
- YOUTUBE_UPLOAD_PRIVACY_STATUS (optional; default: unlisted)
- YOUTUBE_UPLOAD_CATEGORY_ID (optional; default: 22)

Local source files are deleted only after a successful upload. On failure
the file is retained so a retry (or manual recovery) remains possible.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import dataclass
from os import path
from typing import Optional
from urllib.parse import parse_qs, urlparse

import requests

from flask import current_app

from app.services.registration_resolver import team_registration_for_tournament

from models import Camera, Field, Match, Team, Tournament, db


YOUTUBE_TOKEN_URL = "https://oauth2.googleapis.com/token"
YOUTUBE_UPLOAD_INIT_URL = "https://www.googleapis.com/upload/youtube/v3/videos"

_YT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
_YT_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
    "www.youtu.be",
    "youtube-nocookie.com",
    "www.youtube-nocookie.com",
}


def parse_youtube_video_id(raw: str) -> str:
    """Extract an 11-character YouTube video ID from a URL or bare ID.

    Accepts ``https`` YouTube hosts (watch, embed, shorts, youtu.be) or a bare
    video ID. Raises ``ValueError`` for empty input, non-HTTPS URLs, or
    unsupported hosts.
    """
    s = (raw or "").strip()
    if not s:
        raise ValueError("youtube_link is required")
    if _YT_ID_RE.fullmatch(s):
        return s

    parsed = urlparse(s)
    if parsed.scheme.lower() != "https":
        raise ValueError("youtube_link must be an https YouTube URL")
    host = (parsed.hostname or "").lower()
    if host not in _YT_HOSTS:
        raise ValueError("youtube_link must be a YouTube URL")

    video_id = None
    if host in {"youtu.be", "www.youtu.be"}:
        video_id = (parsed.path or "/").lstrip("/").split("/")[0] or None
    else:
        parts = [p for p in (parsed.path or "").split("/") if p]
        if parts and parts[0] in {"embed", "shorts", "v", "live"} and len(parts) >= 2:
            video_id = parts[1]
        else:
            qs = parse_qs(parsed.query)
            video_id = (qs.get("v") or [None])[0]

    if not video_id or not _YT_ID_RE.fullmatch(video_id):
        raise ValueError("youtube_link does not contain a valid video ID")
    return video_id


def canonical_youtube_watch_url(raw: str) -> str:
    """Normalize a YouTube URL or bare ID to ``https://www.youtube.com/watch?v=...``."""
    return f"https://www.youtube.com/watch?v={parse_youtube_video_id(raw)}"


@dataclass(frozen=True)
class YouTubeUploadConfig:
    refresh_token: str
    client_id: str
    client_secret: str
    privacy_status: str
    category_id: str


def _get_config() -> Optional[YouTubeUploadConfig]:
    refresh_token = os.environ.get("YOUTUBE_UPLOAD_REFRESH_TOKEN", "").strip()
    if not refresh_token:
        return None

    client_id = os.environ.get("YOUTUBE_UPLOAD_CLIENT_ID", "").strip()
    if not client_id:
        client_id = os.environ.get("GOOGLE_CLIENT_ID", "").strip()

    client_secret = os.environ.get("YOUTUBE_UPLOAD_CLIENT_SECRET", "").strip()
    if not client_secret:
        client_secret = os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()

    if not client_id or not client_secret:
        return None

    privacy_status = os.environ.get("YOUTUBE_UPLOAD_PRIVACY_STATUS", "unlisted").strip()
    if not privacy_status:
        privacy_status = "unlisted"

    category_id = os.environ.get("YOUTUBE_UPLOAD_CATEGORY_ID", "22").strip()
    if not category_id:
        category_id = "22"

    return YouTubeUploadConfig(
        refresh_token=refresh_token,
        client_id=client_id,
        client_secret=client_secret,
        privacy_status=privacy_status,
        category_id=category_id,
    )


def _get_access_token(cfg: YouTubeUploadConfig) -> str:
    resp = requests.post(
        YOUTUBE_TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "refresh_token": cfg.refresh_token,
            "client_id": cfg.client_id,
            "client_secret": cfg.client_secret,
        },
        timeout=20,
    )
    if not resp.ok:
        try:
            err_body = resp.json()
        except Exception:
            err_body = resp.text[:800] if resp.text else "(empty body)"
        raise RuntimeError(
            f"OAuth token refresh failed HTTP {resp.status_code}: {err_body}. "
            "Typical causes: invalid/expired/revoked refresh token, or client_id/client_secret "
            "do not match the Google Cloud OAuth client that issued the refresh token."
        ) from None
    data = resp.json()
    token = data.get("access_token")
    if not token:
        raise RuntimeError(f"Token response missing access_token: {data}")
    return token


def _video_file_abs_path(camera: Camera) -> str:
    """
    `camera.file` is stored like: static/uploads/videos/<...>/<filename>
    Flask's app root_path is /.../app, so actual static path is /.../static.
    """
    if not camera.file:
        raise RuntimeError("Camera missing file path")
    return path.normpath(path.join(current_app.root_path, "..", camera.file))


def _recording_artifact_dir_abs(camera: Camera) -> str:
    return path.dirname(_video_file_abs_path(camera))


def _delete_local_recording_artifacts(camera: Camera) -> None:
    root_dir_abs = _recording_artifact_dir_abs(camera)
    if not path.isdir(root_dir_abs):
        return
    shutil.rmtree(root_dir_abs)
    current_app.logger.info(
        "youtube_upload: deleted local recording artifacts camera uuid=%s dir=%s",
        camera.uuid,
        root_dir_abs,
    )


def _team_label_for_youtube_title(tournament_url: str, team_id: str | None, fallback: str) -> str:
    """Prefer tournament/league registration pseudonym; else team account name; else id."""
    if not team_id:
        return fallback
    tournament = Tournament.query.filter_by(url=tournament_url).first()
    if not tournament:
        team = Team.query.get(team_id)
        return team.name if team and team.name else team_id
    reg = team_registration_for_tournament(tournament, team_id)
    if reg and reg.pseudonym:
        return reg.pseudonym
    team = Team.query.get(team_id)
    if team and team.name:
        return team.name
    return team_id


def _build_camera_title(camera: Camera) -> str:
    match = Match.query.filter_by(uuid=camera.match_uuid).first()
    if not match:
        return camera.name

    field_obj = Field.query.get(camera.field)
    field_name = field_obj.name if field_obj else str(camera.field)

    turl = match.event
    tournament = Tournament.query.filter_by(url=turl).first()
    event_name = (tournament.name if tournament and tournament.name else turl).strip()
    t1 = _team_label_for_youtube_title(turl, match.team1, "T1")
    t2 = _team_label_for_youtube_title(turl, match.team2, "T2")
    return f"[{event_name}] {match.name}: {t1} vs {t2} ({camera.name} on {field_name})"


def _youtube_init_request(
    session: requests.Session,
    access_token: str,
    file_size: int,
    content_type: str,
    title: str,
    cfg: YouTubeUploadConfig,
) -> str:
    params = {"uploadType": "resumable", "part": "snippet,status"}
    headers = {
        "Authorization": f"Bearer {access_token}",
        "X-Upload-Content-Length": str(file_size),
        "X-Upload-Content-Type": content_type,
    }
    body = {
        "snippet": {
            "title": title,
            "categoryId": cfg.category_id,
        },
        "status": {
            "privacyStatus": cfg.privacy_status,
        },
    }
    resp = session.post(
        YOUTUBE_UPLOAD_INIT_URL,
        params=params,
        headers=headers,
        data=json.dumps(body),
        timeout=30,
    )
    # YouTube returns 200 or 201 with Location header for resumable session.
    if resp.status_code not in (200, 201):
        raise RuntimeError(f"YouTube init failed: {resp.status_code} {resp.text[:300]}")
    location = resp.headers.get("Location")
    if not location:
        raise RuntimeError(f"YouTube init missing Location header: {resp.headers}")
    return location


def _youtube_upload_resumable(
    session: requests.Session,
    upload_url: str,
    access_token: str,
    file_path: str,
    file_size: int,
    content_type: str,
) -> str:
    chunk_size = 8 * 1024 * 1024  # 8MB chunks
    start = 0

    with open(file_path, "rb") as f:
        while start < file_size:
            end = min(start + chunk_size - 1, file_size - 1)
            f.seek(start)
            data = f.read(end - start + 1)
            if not data:
                raise RuntimeError(f"Read empty chunk at bytes={start}-{end}")

            headers = {
                "Authorization": f"Bearer {access_token}",
                "Content-Length": str(len(data)),
                "Content-Type": content_type,
                "Content-Range": f"bytes {start}-{end}/{file_size}",
            }
            resp = session.put(
                upload_url,
                headers=headers,
                data=data,
                timeout=60 * 10,
            )

            if resp.status_code in (200, 201):
                result = resp.json()
                vid = result.get("id")
                if not vid:
                    raise RuntimeError(f"Upload succeeded but missing id: {result}")
                return vid

            # 308 = Resume Incomplete
            if resp.status_code == 308:
                range_header = resp.headers.get("Range")
                if range_header and "-" in range_header:
                    # Example: Range: bytes=0-12345
                    last_end = int(range_header.split("-")[-1])
                    start = last_end + 1
                    continue
                # Fallback: assume this chunk was uploaded
                start = end + 1
                continue

            raise RuntimeError(f"YouTube upload failed: {resp.status_code} {resp.text[:300]}")

    raise RuntimeError("YouTube upload ended without success")


def upload_camera_to_youtube(camera_uuid: str) -> None:
    """
    Worker: transitions camera.status UPLOADING -> SUCCESS/FAILED.

    Local source files are deleted only after SUCCESS. On FAILURE the file
    and ``camera.file`` are retained so a later retry can reuse them.
    """
    cfg = _get_config()
    camera: Camera = Camera.query.filter_by(uuid=camera_uuid).first()
    if not camera:
        current_app.logger.warning("youtube_upload: camera not found uuid=%s", camera_uuid)
        return

    if camera.status != "UPLOADING":
        return

    if not cfg:
        camera.status = "FAILED"
        db.session.commit()
        current_app.logger.warning(
            "youtube_upload: missing YouTube upload config (YOUTUBE_UPLOAD_REFRESH_TOKEN/clients); camera uuid=%s",
            camera_uuid,
        )
        return

    try:
        file_path_abs = _video_file_abs_path(camera)
    except RuntimeError:
        camera.status = "FAILED"
        db.session.commit()
        current_app.logger.error("youtube_upload: camera missing file path uuid=%s", camera_uuid)
        return

    if not path.exists(file_path_abs):
        camera.status = "FAILED"
        db.session.commit()
        current_app.logger.error(
            "youtube_upload: local file missing abs=%s camera uuid=%s",
            file_path_abs,
            camera_uuid,
        )
        return

    file_size = path.getsize(file_path_abs)

    # Uploads are mp4/webm only; derive content type from the stored extension.
    ext = path.splitext(camera.file or "")[1].lower()
    if ext == ".mp4":
        content_type = "video/mp4"
    elif ext == ".webm":
        content_type = "video/webm"
    else:
        camera.status = "FAILED"
        db.session.commit()
        current_app.logger.error(
            "youtube_upload: unsupported extension %s camera uuid=%s",
            ext,
            camera_uuid,
        )
        return

    title = _build_camera_title(camera)

    try:
        access_token = _get_access_token(cfg)
        session = requests.Session()
        upload_url = _youtube_init_request(
            session=session,
            access_token=access_token,
            file_size=file_size,
            content_type=content_type,
            title=title,
            cfg=cfg,
        )
        video_id = _youtube_upload_resumable(
            session=session,
            upload_url=upload_url,
            access_token=access_token,
            file_path=file_path_abs,
            file_size=file_size,
            content_type=content_type,
        )
    except Exception:
        camera.status = "FAILED"
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            raise
        current_app.logger.exception("youtube_upload: OAuth/init/upload failed camera uuid=%s", camera_uuid)
        return

    # SUCCESS
    camera.link = canonical_youtube_watch_url(video_id)
    camera.status = "SUCCESS"
    db.session.commit()

    try:
        _delete_local_recording_artifacts(camera)
        camera.file = None
        db.session.commit()
    except OSError:
        current_app.logger.warning(
            "youtube_upload: could not clean local recording artifacts camera uuid=%s path=%s",
            camera_uuid,
            _recording_artifact_dir_abs(camera),
        )
