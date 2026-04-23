import json
import logging
import os
import re
import subprocess
from datetime import datetime, timezone
from itertools import groupby
from os import path

from models import Camera, Field, Match, Point, db

log = logging.getLogger(__name__)


def _parse_timestamp_ms(raw):
    """Parse epoch-ms or ISO-8601 timestamps into epoch milliseconds."""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    s = str(raw).strip()
    if not s:
        return None
    try:
        return float(s)
    except (TypeError, ValueError):
        pass
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt.timestamp() * 1000.0
    except (TypeError, ValueError):
        return None


def _chunk_timestamp_sort_key(chunk):
    return _parse_timestamp_ms(chunk.get("chunk_start_timestamp")) or 0.0


def _chunk_sort_key(chunk):
    """Prefer BlobEvent ordering; fall back to chunk_start_timestamp."""
    raw = chunk.get("blob_event_timestamp_ms")
    if raw is not None:
        try:
            return float(raw)
        except (TypeError, ValueError):
            pass
    return _chunk_timestamp_sort_key(chunk)


def _epoch_ms_to_iso_utc(ms):
    if ms is None:
        return None
    try:
        dt = datetime.fromtimestamp(float(ms) / 1000.0, tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None
    return dt.isoformat().replace("+00:00", "Z")


def _session_world_start_ms(chunk):
    return _parse_timestamp_ms(
        chunk.get("chunk_start_timestamp")
    ) or _parse_timestamp_ms(chunk.get("recording_session_start_time"))


def _is_init_segment(chunk):
    return bool(chunk.get("is_init_segment"))


def _top_level_box_spans(data: bytes):
    """Yield (fourcc, start_byte, end_exclusive) for each top-level ISO BMFF box."""
    idx = 0
    n = len(data)
    while idx + 8 <= n:
        size = int.from_bytes(data[idx : idx + 4], "big")
        if size < 8:
            break
        typ = data[idx + 4 : idx + 8]
        end = idx + size
        if end > n:
            break
        yield typ, idx, end
        idx = end


def _strip_leading_init_segment(data: bytes) -> bytes:
    """
    Keep only the fragment payload after a leading [ftyp][moov] init segment.
    Later chunks are expected to contribute only moof/mdat media data.
    """
    spans = list(_top_level_box_spans(data))
    if not spans:
        return data
    idx = 0
    if spans[idx][0] == b"ftyp":
        idx += 1
    else:
        return data
    if idx < len(spans) and spans[idx][0] == b"moov":
        idx += 1
    else:
        return data
    if idx >= len(spans):
        return b""
    return data[spans[idx][1] :]


def _extract_ftyp_moov(data: bytes) -> bytes | None:
    spans = list(_top_level_box_spans(data))
    out = bytearray()
    saw_ftyp = False
    saw_moov = False
    for typ, start, end in spans:
        if typ == b"ftyp":
            out.extend(data[start:end])
            saw_ftyp = True
        elif typ == b"moov":
            out.extend(data[start:end])
            saw_moov = True
            if saw_ftyp:
                break
    if saw_ftyp and saw_moov:
        return bytes(out)
    return None


def _chunk_file_starts_with_ftyp(chunk_dir: str, filename: str) -> bool:
    fp = path.join(chunk_dir, filename)
    try:
        with open(fp, "rb") as handle:
            head = handle.read(12)
        return len(head) >= 8 and head[4:8] == b"ftyp"
    except OSError:
        return False


def _reorder_session_chunks_ftyp_first(chunks, chunk_dir):
    return sorted(
        chunks,
        key=lambda chunk: (
            0 if _chunk_file_starts_with_ftyp(chunk_dir, chunk["filename"]) else 1,
            _chunk_sort_key(chunk),
        ),
    )


def _concat_session_chunks(
    ordered_chunks, chunk_dir, joined_raw_path, logger, session_id, init_segment
):
    """
    Join one session's fragments into a single raw MP4 stream:
    - explicit `init_segment` is written first when available
    - later chunks drop repeated init segments
    """
    if not ordered_chunks:
        return False
    try:
        with open(joined_raw_path, "wb") as out:
            if init_segment:
                out.write(init_segment)
            else:
                first_filename = ordered_chunks[0]["filename"]
                logger.warning(
                    "finalize_recording: session %s has no init segment; first media chunk is %s",
                    session_id,
                    first_filename,
                )
            for chunk in ordered_chunks:
                chunk_path = path.join(chunk_dir, chunk["filename"])
                with open(chunk_path, "rb") as handle:
                    data = handle.read()
                out.write(_strip_leading_init_segment(data))
        return path.exists(joined_raw_path) and path.getsize(joined_raw_path) > 0
    except OSError as exc:
        logger.warning(
            "finalize_recording: session %s raw concat failed: %s", session_id, exc
        )
        return False


def _run_subprocess(args, cwd=None):
    return subprocess.run(
        args,
        cwd=cwd,
        capture_output=True,
        text=True,
    )


def _get_video_codec(file_path, cwd=None):
    input_arg = path.basename(file_path) if cwd else file_path
    run_cwd = cwd or path.dirname(path.abspath(file_path))
    result = _run_subprocess(
        [
            "ffprobe",
            "-v",
            "quiet",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_name",
            "-of",
            "csv=p=0",
            "-i",
            input_arg,
        ],
        cwd=run_cwd,
    )
    if result.returncode != 0 or not result.stdout:
        return None
    codec = result.stdout.strip().lower()
    return codec or None


def _codec_tag_args(codec_name):
    if codec_name == "hevc":
        return ["-tag:v", "hvc1"]
    if codec_name in ("h264", "avc1"):
        return ["-tag:v", "avc1"]
    return []


def _get_media_duration_sec(file_path, cwd=None):
    input_arg = path.basename(file_path) if cwd else file_path
    run_cwd = cwd or path.dirname(path.abspath(file_path))
    result = _run_subprocess(
        [
            "ffprobe",
            "-v",
            "quiet",
            "-show_entries",
            "format=duration",
            "-of",
            "csv=p=0",
            "-i",
            input_arg,
        ],
        cwd=run_cwd,
    )
    if result.returncode != 0 or not result.stdout:
        return None
    s = result.stdout.strip()
    if not s or s == "N/A":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _get_first_video_keyframe_time_sec(file_path, cwd=None):
    """Ask ffprobe for the first keyframe timestamp in seconds."""
    input_arg = path.basename(file_path) if cwd else file_path
    run_cwd = cwd or path.dirname(path.abspath(file_path))
    result = _run_subprocess(
        [
            "ffprobe",
            "-v",
            "error",
            "-skip_frame",
            "nokey",
            "-select_streams",
            "v:0",
            "-show_frames",
            "-show_entries",
            "frame=best_effort_timestamp_time,pkt_dts_time,pkt_pts_time",
            "-of",
            "json",
            input_arg,
        ],
        cwd=run_cwd,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return 0.0
    try:
        payload = json.loads(result.stdout)
    except ValueError:
        return 0.0
    for frame in payload.get("frames") or []:
        for key in (
            "best_effort_timestamp_time",
            "pkt_dts_time",
            "pkt_pts_time",
        ):
            raw = frame.get(key)
            if raw in (None, ""):
                continue
            try:
                return max(0.0, float(raw))
            except (TypeError, ValueError):
                continue
    return 0.0


def _remux_fragment_stream(
    input_path, output_basename, chunk_dir, codec_name, logger, label
):
    tag_args = _codec_tag_args(codec_name)
    result = _run_subprocess(
        [
            "ffmpeg",
            "-i",
            path.basename(input_path),
            "-c",
            "copy",
            "-map",
            "0",
            *tag_args,
            "-movflags",
            "+faststart",
            "-loglevel",
            "error",
            "-y",
            output_basename,
        ],
        cwd=chunk_dir,
    )
    output_path = path.join(chunk_dir, output_basename)
    if (
        result.returncode != 0
        or not path.exists(output_path)
        or path.getsize(output_path) == 0
    ):
        logger.warning(
            "finalize_recording: %s remux failed returncode=%s stderr=%s",
            label,
            result.returncode,
            (result.stderr or "").strip(),
        )
        return None
    return output_path


def _clip_session_to_first_keyframe(
    session_unclipped_path,
    session_basename,
    chunk_dir,
    codec_name,
    clip_start_sec,
    logger,
    session_id,
):
    final_path = path.join(chunk_dir, session_basename)
    tag_args = _codec_tag_args(codec_name)
    clip_source_basename = path.basename(session_unclipped_path)
    clip_output_basename = f"session_clipped_{session_id}.mp4"
    clip_output_path = path.join(chunk_dir, clip_output_basename)

    if clip_start_sec > 0.0005:
        clip_result = _run_subprocess(
            [
                "ffmpeg",
                "-ss",
                f"{clip_start_sec:.6f}",
                "-i",
                clip_source_basename,
                "-c",
                "copy",
                "-map",
                "0",
                *tag_args,
                "-avoid_negative_ts",
                "make_zero",
                "-movflags",
                "+faststart",
                "-loglevel",
                "error",
                "-y",
                clip_output_basename,
            ],
            cwd=chunk_dir,
        )
        if (
            clip_result.returncode != 0
            or not path.exists(clip_output_path)
            or path.getsize(clip_output_path) == 0
        ):
            logger.warning(
                "finalize_recording: session %s keyframe clip failed returncode=%s stderr=%s",
                session_id,
                clip_result.returncode,
                (clip_result.stderr or "").strip(),
            )
            return None
        clip_source_basename = clip_output_basename
    else:
        clip_output_path = session_unclipped_path

    normalize_result = _run_subprocess(
        [
            "ffmpeg",
            "-fflags",
            "+genpts",
            "-i",
            clip_source_basename,
            "-c",
            "copy",
            "-map",
            "0",
            *tag_args,
            "-reset_timestamps",
            "1",
            "-avoid_negative_ts",
            "make_zero",
            "-movflags",
            "+faststart",
            "-loglevel",
            "error",
            "-y",
            session_basename,
        ],
        cwd=chunk_dir,
    )
    if (
        normalize_result.returncode != 0
        or not path.exists(final_path)
        or path.getsize(final_path) == 0
    ):
        logger.warning(
            "finalize_recording: session %s timestamp normalize failed returncode=%s stderr=%s",
            session_id,
            normalize_result.returncode,
            (normalize_result.stderr or "").strip(),
        )
        return None
    try:
        os.remove(session_unclipped_path)
    except OSError:
        pass
    if clip_output_path != session_unclipped_path:
        try:
            os.remove(clip_output_path)
        except OSError:
            pass
    return final_path


def _build_playable_session(session_id, session_chunks, chunk_dir, logger):
    """
    Build one independently playable MP4 for a session:
    1. concatenate fragments
    2. remux into a complete MP4
    3. locate first video keyframe
    4. clip the session to that keyframe boundary
    """
    init_chunks = [
        chunk
        for chunk in session_chunks
        if _is_init_segment(chunk)
        and path.exists(path.join(chunk_dir, chunk["filename"]))
    ]
    media_chunks = [
        chunk
        for chunk in session_chunks
        if not _is_init_segment(chunk)
        and path.exists(path.join(chunk_dir, chunk["filename"]))
    ]
    if not media_chunks:
        logger.warning(
            "finalize_recording: session %s has no media chunk files on disk",
            session_id,
        )
        return None

    ordered_chunks = [
        chunk
        for chunk in media_chunks
        if path.exists(path.join(chunk_dir, chunk["filename"]))
    ]
    earliest_chunk = min(ordered_chunks, key=_chunk_sort_key)
    concat_chunks = _reorder_session_chunks_ftyp_first(ordered_chunks, chunk_dir)

    init_segment = None
    for init_chunk in init_chunks:
        init_path = path.join(chunk_dir, init_chunk["filename"])
        try:
            with open(init_path, "rb") as handle:
                init_data = handle.read()
            init_segment = _extract_ftyp_moov(init_data) or init_data
            if init_segment:
                break
        except OSError:
            continue
    if init_segment is None:
        for chunk in concat_chunks:
            chunk_path = path.join(chunk_dir, chunk["filename"])
            try:
                with open(chunk_path, "rb") as handle:
                    maybe_init = _extract_ftyp_moov(handle.read())
                if maybe_init:
                    init_segment = maybe_init
                    logger.info(
                        "finalize_recording: session %s recovered init segment from media chunk %s",
                        session_id,
                        chunk["filename"],
                    )
                    break
            except OSError:
                continue

    joined_raw_basename = f"joined_raw_{session_id}.mp4"
    joined_raw_path = path.join(chunk_dir, joined_raw_basename)
    if not _concat_session_chunks(
        concat_chunks, chunk_dir, joined_raw_path, logger, session_id, init_segment
    ):
        return None

    codec_name = _get_video_codec(joined_raw_path, cwd=chunk_dir)
    session_unclipped_basename = f"session_unclipped_{session_id}.mp4"
    session_unclipped_path = _remux_fragment_stream(
        joined_raw_path,
        session_unclipped_basename,
        chunk_dir,
        codec_name,
        logger,
        f"session {session_id}",
    )
    if session_unclipped_path is None:
        return None

    clip_start_sec = _get_first_video_keyframe_time_sec(
        session_unclipped_path, cwd=chunk_dir
    )
    session_basename = f"session_{session_id}.mp4"
    session_path = _clip_session_to_first_keyframe(
        session_unclipped_path,
        session_basename,
        chunk_dir,
        codec_name,
        clip_start_sec,
        logger,
        session_id,
    )
    if session_path is None:
        return None

    duration_sec = _get_media_duration_sec(session_path, cwd=chunk_dir)
    if duration_sec is None or duration_sec <= 0:
        logger.warning(
            "finalize_recording: session %s duration unavailable after clip", session_id
        )
        return None

    session_world_start_ms = _session_world_start_ms(earliest_chunk)
    if session_world_start_ms is None:
        logger.warning(
            "finalize_recording: session %s missing world timestamp metadata",
            session_id,
        )
        return None
    session_world_start_ms += clip_start_sec * 1000.0
    session_world_start_iso = _epoch_ms_to_iso_utc(session_world_start_ms)
    if session_world_start_iso is None:
        logger.warning(
            "finalize_recording: session %s world timestamp could not be normalized",
            session_id,
        )
        return None

    logger.info(
        "finalize_recording: session %s built basename=%s clip_start_sec=%.6f duration_sec=%.3f world_start=%s",
        session_id,
        session_basename,
        clip_start_sec,
        duration_sec,
        session_world_start_iso,
    )
    return {
        "session_id": session_id,
        "basename": session_basename,
        "path": session_path,
        "duration_sec": duration_sec,
        "world_start_ms": session_world_start_ms,
        "world_start_iso": session_world_start_iso,
        "clip_start_sec": clip_start_sec,
        "joined_raw_basename": joined_raw_basename,
    }


def _concat_session_outputs(session_outputs, chunk_dir, logger):
    concat_list_path = path.join(chunk_dir, "concat_sessions.txt")
    with open(concat_list_path, "w") as handle:
        for session in session_outputs:
            abs_path = path.abspath(session["path"])
            handle.write(f"file {repr(abs_path)}\n")

    final_basename = "final_video.mp4"
    final_path = path.join(chunk_dir, final_basename)
    concat_result = _run_subprocess(
        [
            "ffmpeg",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            path.basename(concat_list_path),
            "-c",
            "copy",
            "-loglevel",
            "error",
            "-y",
            final_basename,
        ],
        cwd=chunk_dir,
    )
    if (
        concat_result.returncode != 0
        or not path.exists(final_path)
        or path.getsize(final_path) == 0
    ):
        logger.warning(
            "finalize_recording: match concat failed returncode=%s stderr=%s",
            concat_result.returncode,
            (concat_result.stderr or "").strip(),
        )
        return None

    remux_basename = "final_video_fixed.mp4"
    remux_path = path.join(chunk_dir, remux_basename)
    remux_result = _run_subprocess(
        [
            "ffmpeg",
            "-fflags",
            "+genpts",
            "-i",
            path.basename(final_path),
            "-c",
            "copy",
            "-map",
            "0",
            "-reset_timestamps",
            "1",
            "-movflags",
            "+faststart",
            "-loglevel",
            "error",
            "-y",
            remux_basename,
        ],
        cwd=chunk_dir,
    )
    if (
        remux_result.returncode == 0
        and path.exists(remux_path)
        and path.getsize(remux_path) > 0
    ):
        try:
            os.remove(final_path)
        except OSError:
            pass
        return {
            "basename": remux_basename,
            "path": remux_path,
            "concat_list_path": concat_list_path,
        }
    logger.info(
        "finalize_recording: final remux skipped or failed; using raw concat stderr=%s",
        (remux_result.stderr or "").strip() or "n/a",
    )
    return {
        "basename": final_basename,
        "path": final_path,
        "concat_list_path": concat_list_path,
    }


def _sort_session_outputs_chronologically(session_outputs):
    return sorted(
        session_outputs,
        key=lambda session: (
            float(session.get("world_start_ms") or 0.0),
            str(session.get("session_id") or ""),
        ),
    )


def _build_point_timestamps(session_outputs, pts_rows):
    time_world = [session["world_start_iso"] for session in session_outputs]
    time_video = []
    running_offset = 0.0
    for session in session_outputs:
        time_video.append(running_offset)
        running_offset += session["duration_sec"]

    point_timestamps = []
    for pt in pts_rows:
        if pt["stamp"] is None or pt["end_stamp"] is None:
            continue
        point_world_sec = pt["stamp"].replace(tzinfo=timezone.utc).timestamp()
        for idx, session in enumerate(session_outputs):
            session_world_sec = session["world_start_ms"] / 1000.0
            session_end_sec = session_world_sec + session["duration_sec"]
            if session_world_sec <= point_world_sec < session_end_sec:
                in_video_start = time_video[idx] + (point_world_sec - session_world_sec)
                point_timestamps.append(
                    {
                        "point_uuid": pt["uuid"],
                        "in_video_start": round(max(0.0, in_video_start), 3),
                    }
                )
                break

    return time_world, time_video, point_timestamps


def _create_camera_outputs(
    tournament_url,
    field_name,
    match_id,
    camera_name,
    final_basename,
    session_outputs,
    point_timestamps,
):
    video_path = path.join(
        "static",
        "uploads",
        "videos",
        tournament_url,
        field_name,
        match_id,
        camera_name,
        final_basename,
    ).replace("\\", "/")

    match = Match.query.filter_by(uuid=match_id).first()
    if not match:
        raise RuntimeError(f"match not found uuid={match_id}")

    stream_starts = {}
    if match.camera_stream_starts:
        try:
            stream_starts = json.loads(match.camera_stream_starts)
        except (TypeError, ValueError):
            stream_starts = {}
    stream_starts[camera_name] = {
        "video_path": video_path,
        "point_timestamps": point_timestamps,
        "type": "recorded",
        "stream_start_time": session_outputs[0]["world_start_iso"],
    }
    match.camera_stream_starts = json.dumps(stream_starts)
    db.session.commit()

    field_obj = Field.query.filter_by(event=tournament_url, name=field_name).first()
    if not field_obj:
        raise RuntimeError(
            f"field not found for camera output event={tournament_url} field={field_name}"
        )

    time_world = [session["world_start_iso"] for session in session_outputs]
    running_offset = 0.0
    time_video = []
    for session in session_outputs:
        time_video.append(running_offset)
        running_offset += session["duration_sec"]

    camera_row = (
        Camera.query.filter_by(
            match_uuid=match_id,
            event=tournament_url,
            name=camera_name,
            source_type="recording",
        )
        .order_by(Camera.uuid.asc())
        .first()
    )
    if camera_row is None:
        camera_row = Camera(
            match_uuid=match_id,
            event=tournament_url,
            field=field_obj.id,
            field_id=field_obj.id,
            name=camera_name,
            source_type="recording",
        )
        db.session.add(camera_row)

    camera_row.field = field_obj.id
    camera_row.field_id = field_obj.id
    camera_row.status = "UPLOADING"
    camera_row.link = None
    camera_row.file = video_path
    camera_row.time_world = json.dumps(time_world)
    camera_row.time_video = json.dumps(time_video)
    db.session.commit()
    return camera_row


def finalize_recording_worker(
    logger, tournament_url, field_name, match_id, camera_name, chunk_dir
):
    """
    Build independently playable per-session MP4s from uploaded chunks, concatenate the
    sessions into one match MP4, compute world-to-video interpolation metadata, and hand
    the final file to the existing YouTube upload worker.
    """
    import threading
    from flask import current_app

    _log = logger or log
    chunk_dir = path.abspath(path.normpath(chunk_dir))
    _log.info(
        "finalize_recording: worker started match_id=%s camera_name=%s chunk_dir=%s",
        match_id,
        camera_name,
        chunk_dir,
    )

    chunks_meta_path = path.join(chunk_dir, "chunks_meta.json")
    if not path.exists(chunks_meta_path):
        _log.warning("finalize_recording: missing chunks_meta.json in %s", chunk_dir)
        return

    with open(chunks_meta_path, "r") as handle:
        payload = json.load(handle)
    all_meta = (
        list(payload.values()) if isinstance(payload, dict) else list(payload or [])
    )
    if not all_meta:
        _log.warning("finalize_recording: no chunks in chunks_meta.json")
        return

    pts = Point.query.filter_by(match=match_id).order_by(Point.stamp.asc()).all()
    pts_rows = [
        {"uuid": str(pt.uuid), "stamp": pt.stamp, "end_stamp": pt.end_stamp}
        for pt in pts
    ]
    db.session.remove()

    def session_key(chunk):
        return chunk.get("session_id") or ""

    sorted_meta = sorted(
        all_meta, key=lambda chunk: (session_key(chunk), _chunk_sort_key(chunk))
    )
    grouped_sessions = []
    for session_id, group in groupby(sorted_meta, key=session_key):
        if not session_id:
            continue
        chunks = sorted(list(group), key=_chunk_sort_key)
        if chunks:
            grouped_sessions.append((session_id, chunks))
    grouped_sessions.sort(
        key=lambda entry: _chunk_sort_key(entry[1][0]) if entry[1] else 0.0
    )

    if not grouped_sessions:
        _log.warning("finalize_recording: no sessions with session_id in chunks_meta")
        return

    session_outputs = []
    for session_id, session_chunks in grouped_sessions:
        session_output = _build_playable_session(
            session_id, session_chunks, chunk_dir, _log
        )
        if session_output is not None:
            session_outputs.append(session_output)

    if not session_outputs:
        _log.warning("finalize_recording: no playable session outputs produced")
        return

    session_outputs = _sort_session_outputs_chronologically(session_outputs)
    _log.info(
        "finalize_recording: chronological session order=%s",
        [
            {
                "session_id": session["session_id"],
                "world_start_iso": session["world_start_iso"],
                "duration_sec": round(session["duration_sec"], 3),
            }
            for session in session_outputs
        ],
    )

    final_output = _concat_session_outputs(session_outputs, chunk_dir, _log)
    if final_output is None:
        return

    time_world, time_video, point_timestamps = _build_point_timestamps(
        session_outputs, pts_rows
    )
    _log.info(
        "finalize_recording: interpolation sessions=%s time_world=%s time_video=%s point_count=%s",
        len(session_outputs),
        time_world,
        time_video,
        len(point_timestamps),
    )

    try:
        camera_row = _create_camera_outputs(
            tournament_url,
            field_name,
            match_id,
            camera_name,
            final_output["basename"],
            session_outputs,
            point_timestamps,
        )
    except Exception as exc:
        _log.exception(
            "finalize_recording: failed to persist match/camera outputs: %s", exc
        )
        return

    app_obj = current_app._get_current_object()

    def _yt_upload():
        with app_obj.app_context():
            from app.utils.youtube_upload import upload_camera_to_youtube

            upload_camera_to_youtube(str(camera_row.uuid))

    threading.Thread(target=_yt_upload, daemon=True).start()
