"""
Tournament management routes.
"""

from flask import (
    Blueprint,
    request,
    flash,
    jsonify,
    current_app,
)
from flask_login import login_required, current_user
from flask_executor import Executor

from datetime import datetime, timedelta, timezone
import json
import time
import threading
import uuid

from flask_login.utils import urlencode
from urllib3.util import url
from models import (
    Tournament,
    Match,
    Field,
    Tag,
    Camera,
    Point,
    TeamRegistration,
    PlayerRegistration,
    Team,
    TO,
    League,
    db,
)
from app.utils.helpers import (
    resolve_team_name_to_id,
    resolve_tag_to_team,
    can_head_ref_match,
)
from app.utils.scheduling import (
    compute_dynamic_match_nominal_start_time,
    validate_match_input,
    recompute_all_match_times,
    detect_match_conflicts,
)
from app.utils.name_validation import match_name_char_error
from app.utils.decorators import require_tournament_organizer
from app.filters import is_head_ref

from os import path, listdir

from app.utils.footage import finalize_recording_worker
from app.utils.user_uploads import (
    create_direct_user_upload_camera,
    list_batch_manifest_rows,
    register_batch_upload_completion,
)
from app.utils.camera_helpers import (
    generate_camera_key,
    validate_camera_key,
    get_camera_key_from_request,
    require_camera_key,
)
from app.utils.recording_retry import (
    RETRY_FINALIZATION_USER_IDS_ENV,
    current_user_can_retry_finalization,
)
from app.utils import preview_store
from app.utils.field_refs import set_match_field_from_name
from app.utils.match_1nf import (
    read_match_ref_slots,
    read_match_stream_starts,
    replace_match_ref_slots,
    replace_match_stream_starts,
)

from app.domain.enums import (
    MatchStatus,
    RegistrationStatus,
    ScheduleType,
    SetType,
)
from app.services.registration_resolver import is_player_registered

# for finalizing recordings which calls ffmpeg
# only one worker bc ffmpeg does its own parallelism
# so we only ever want to run one at a time
executor = Executor()

bp = Blueprint("tournaments", __name__, url_prefix="/_api")


def update_match_previous_link(
    match: Match, prev_match_id: str, tournament_url: str, is_new: bool = False
) -> None:
    """
    Update the previous_match link for a match, maintaining a doubly linked list structure.

    When inserting a match after prev_match, if prev_match already has a next_match:
    1. Store the old next_match of prev_match
    2. Set the current match's previous_match to prev_match
    3. Set prev_match's next_match to the current match
    4. Set the current match's next_match to the old next_match (if it existed)
    5. Set the old next_match's previous_match to the current match (if it existed)
    6. If updating (not new), handle cleanup of old previous_match's next_match

    This properly inserts the match into the chain: ... -> prev_match -> match -> old_next_match -> ...

    Args:
        match: The match to update
        prev_match_id: UUID of the match to set as previous_match
        tournament_url: Tournament URL for validation
        is_new: True if this is a new match, False if updating existing match
    """
    prev_match = Match.query.filter_by(uuid=prev_match_id, event=tournament_url).first()
    if not prev_match:
        return

    # Store old previous_match and next_match for cleanup (only for updates)
    old_prev_id = match.previous_match if not is_new else None
    old_next_id = match.next_match if not is_new else None

    # Store the old next_match of prev_match (before we change it)
    prev_match_old_next_id = prev_match.next_match

    # Set the current match's previous_match to prev_match
    match.previous_match = prev_match_id

    # Set prev_match's next_match to this match
    prev_match.next_match = match.uuid

    # If prev_match had a next_match that isn't this match, link it to this match
    if prev_match_old_next_id and prev_match_old_next_id != match.uuid:
        prev_match_old_next = Match.query.filter_by(
            uuid=prev_match_old_next_id, event=tournament_url
        ).first()
        if prev_match_old_next:
            # Set the current match's next_match to the old next_match
            match.next_match = prev_match_old_next_id
            # Set the old next_match's previous_match to this match
            prev_match_old_next.previous_match = match.uuid
    else:
        # No old next_match from prev_match
        # If updating an existing match, preserve its existing next_match if it's still valid
        # (only clear if this is a new match or if we're explicitly changing the chain)
        if is_new:
            match.next_match = None
        # For updates, preserve the existing next_match - it will be handled by cleanup logic below if needed

    # If updating and had an old previous_match, handle cleanup
    if old_prev_id and old_prev_id != prev_match_id:
        old_prev_match = Match.query.filter_by(
            uuid=old_prev_id, event=tournament_url
        ).first()
        if old_prev_match:
            # If old_prev_match's next_match pointed to this match, we need to update it
            if old_prev_match.next_match == match.uuid:
                # The old previous match's next should now point to this match's old next (if any)
                old_prev_match.next_match = (
                    old_next_id if old_next_id != old_prev_id else None
                )
                # If we set old_prev_match.next_match to something, update that match's previous_match
                if old_prev_match.next_match:
                    old_next_of_old_prev = Match.query.filter_by(
                        uuid=old_prev_match.next_match, event=tournament_url
                    ).first()
                    if old_next_of_old_prev:
                        old_next_of_old_prev.previous_match = old_prev_id

    # If updating and had an old next_match that we didn't preserve, handle cleanup
    if old_next_id and old_next_id != match.next_match:
        old_next_match = Match.query.filter_by(
            uuid=old_next_id, event=tournament_url
        ).first()
        if old_next_match and old_next_match.previous_match == match.uuid:
            # This match's old next_match no longer has this match as its previous
            old_next_match.previous_match = None


def is_not_TO(
    tournament_url, message="Only tournament organizers can access this page"
):
    """
    Legacy helper retained for compatibility.

    Prefer `@require_tournament_organizer()` going forward.
    """
    from app.services.permission_service import PermissionService

    if not PermissionService.is_tournament_organizer(tournament_url, current_user):
        flash(message, "error")
        return True
    return False


@bp.route("/create-tournament", methods=["POST"])
@login_required
def create_tournament():
    """Create a new tournament and assign the creator as TO.

    ``POST /_api/create-tournament``

    Creates the tournament record and, for standalone tournaments, a
    :class:`~app.models.registrable_config.RegistrableConfig`.  When
    *league_id* is provided the tournament inherits the league's config and
    the caller must be a league TO.

    Form Data:
        name (str): Display name for the tournament.
        url (str): URL slug (must be unique).
        league_id (str | None): Optional league to attach to.

    Returns:
        JSON ``{"success": true, "url": "<slug>"}`` on success, or error
        with HTTP 400/403.
    """
    name = request.form["name"]
    url = request.form["url"]

    if Tournament.query.filter_by(url=url).first():
        return (
            jsonify({"success": False, "error": "Tournament URL already exists"}),
            400,
        )

    league_id = None
    raw_league_id = request.form.get("league_id", "").strip()
    if raw_league_id:
        league = League.query.get(raw_league_id)
        if not league:
            return jsonify({"success": False, "error": "League not found"}), 400
        is_league_to = TO.query.filter_by(
            user_id=current_user.id,
            user_type=current_user.__class__.__name__.lower(),
            league_id=raw_league_id,
        ).first()
        if not is_league_to:
            return (
                jsonify(
                    {
                        "success": False,
                        "error": "You must be an organizer of that league to attach a tournament to it.",
                    }
                ),
                403,
            )
        league_id = raw_league_id

    from models import RegistrableConfig

    start_date = datetime.now(timezone.utc).replace(tzinfo=None)
    tournament = Tournament(
        url=url,
        name=name,
        start_date=start_date,
        end_date=start_date,
        league_id=league_id,
    )
    if not league_id:
        rc = RegistrableConfig(
            team_reg_fee=0.0,
            player_reg_fee=0.0,
            registration_open=False,
        )
        db.session.add(rc)
        db.session.flush()
        tournament.registrable_config_id = rc.id

    db.session.add(tournament)

    to_entry = TO(
        user_id=current_user.id,
        user_type=current_user.__class__.__name__.lower(),
        event=url,
    )
    db.session.add(to_entry)
    db.session.commit()

    return (
        jsonify(
            {
                "success": True,
                "message": f'Tournament "{name}" created successfully!',
                "url": url,
            }
        ),
        200,
    )


@bp.route("/create-league", methods=["POST"])
@login_required
def create_league():
    """Create a new league. TOs create a new league for each season."""
    from models import League, TO, db

    league_name = request.form.get("league_name", "").strip()
    league_url = request.form.get("league_url", "").strip()

    if not league_name or not league_url:
        return (
            jsonify(
                {
                    "success": False,
                    "error": "League name and URL slug are required.",
                }
            ),
            400,
        )

    if League.query.filter_by(url=league_url).first():
        return jsonify({"success": False, "error": "League URL already exists"}), 400

    from models import RegistrableConfig

    rc = RegistrableConfig(
        team_reg_fee=0.0,
        player_reg_fee=0.0,
        registration_open=False,
    )
    db.session.add(rc)
    db.session.flush()
    league = League(
        url=league_url,
        name=league_name,
        registrable_config_id=rc.id,
    )
    db.session.add(league)
    db.session.flush()

    to_entry = TO(
        user_id=current_user.id,
        user_type=current_user.__class__.__name__.lower(),
        event=None,
        league_id=league_url,
    )
    db.session.add(to_entry)
    db.session.commit()

    return (
        jsonify(
            {
                "success": True,
                "message": f'League "{league_name}" created successfully!',
                "league_url": league_url,
            }
        ),
        200,
    )


@bp.route("/<tournament_url>/recompute-schedule", methods=["POST"])
@login_required
def recompute_schedule(tournament_url):
    """Force full recompute of match times as if a match were just edited (TO only)."""
    if is_not_TO(tournament_url):
        return (
            jsonify(
                {
                    "success": False,
                    "error": "Only tournament organizers can access this page",
                }
            ),
            403,
        )
    try:
        recompute_all_match_times(tournament_url)
        return (
            jsonify({"success": True, "message": "Schedule recomputed successfully."}),
            200,
        )
    except Exception as e:
        return jsonify({"success": False, "error": f"Recompute failed: {e}"}), 500


@bp.route("/<tournament_url>/export-schedule")
@require_tournament_organizer("You must be a tournament organizer to export schedules")
def export_schedule(tournament_url):
    """Export schedule (tags, fields, matches) as TOML file download."""
    from app.services.schedule_import_export_service import ScheduleImportExportService
    from app.error_values import Ok, Err
    from flask import send_file
    import io

    res = ScheduleImportExportService.export_schedule(tournament_url)

    match res:
        case Ok(toml_content):
            # Create in-memory file
            file_obj = io.BytesIO(toml_content.encode("utf-8"))
            filename = f"{tournament_url}_schedule_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.toml"
            return send_file(
                file_obj,
                mimetype="application/toml",
                as_attachment=True,
                download_name=filename,
            )
        case Err(err):
            from app.utils.responses import json_error
            from app.utils.result_helpers import public_error_message

            return json_error(
                public_error_message(err),
                status_code=err.status_code if hasattr(err, "status_code") else 400,
            )


@bp.route("/<tournament_url>/import-schedule", methods=["POST"])
@require_tournament_organizer("You must be a tournament organizer to import schedules")
def import_schedule(tournament_url):
    """Import schedule from uploaded TOML file."""
    from app.services.schedule_import_export_service import ScheduleImportExportService
    from app.utils.result_helpers import json_from_result

    # Validate file upload
    if "schedule_file" not in request.files:
        return jsonify({"success": False, "error": "No file uploaded"}), 400

    file = request.files["schedule_file"]
    if file.filename == "":
        return jsonify({"success": False, "error": "No file selected"}), 400

    if not file.filename.endswith(".toml"):
        return jsonify({"success": False, "error": "File must be a .toml file"}), 400

    # Read file content
    try:
        toml_content = file.read().decode("utf-8")
    except UnicodeDecodeError:
        return (
            jsonify({"success": False, "error": "File must be valid UTF-8 text"}),
            400,
        )

    # Import schedule (all validation happens before any database changes)
    res = ScheduleImportExportService.import_schedule(tournament_url, toml_content)

    def result_to_payload(import_result):
        """Convert ImportResult to JSON payload."""
        return {
            "tags_created": import_result.tags_created,
            "tags_updated": import_result.tags_updated,
            "fields_created": import_result.fields_created,
            "fields_updated": import_result.fields_updated,
            "matches_created": import_result.matches_created,
            "matches_updated": import_result.matches_updated,
            "errors": import_result.errors,
        }

    return json_from_result(res, ok_to_payload=result_to_payload)


@bp.route("/camera-url")
@login_required
def camera_url_api():
    """Generate camera recording URL with access key. Requires TO access."""
    try:
        tournament_url = request.args.get("tournament")
        field_name = request.args.get("field")

        if not tournament_url or not field_name:
            return jsonify({"error": "Tournament and field parameters required"}), 400

        # Check if user is a TO for this tournament
        if is_not_TO(tournament_url):
            return (
                jsonify(
                    {"error": "Unauthorized: You must be a TO for this tournament"}
                ),
                403,
            )

        # Verify field exists
        field = Field.query.filter_by(event=tournament_url, name=field_name).first()
        if not field:
            return jsonify({"error": f'Field "{field_name}" not found'}), 404

        # Generate the camera URL with key (frontend route, not a Flask endpoint)
        access_key = generate_camera_key(tournament_url, field_name)
        from urllib.parse import quote

        base = request.url_root.rstrip("/")
        camera_url = (
            f"{base}/{tournament_url}/record"
            f"?field={quote(field_name)}&camera_key={quote(access_key)}&camera_name="
        )

        return jsonify({"url": camera_url})
    except Exception as e:
        import traceback

        print(f"Error in camera_url_api: {e}")
        print(traceback.format_exc())
        return jsonify({"error": f"Server error: {str(e)}"}), 500


@bp.route("/record/match-status")
def record_match_status():
    """Check if a field has an active match for point recording. No access key required."""
    from models import Point

    tournament_url = request.args.get("tournament")
    field_name = request.args.get("field")
    current_match_id = request.args.get(
        "current_match_id"
    )  # Optional: track specific match

    if not tournament_url or not field_name:
        return jsonify({"error": "Tournament and field parameters required"}), 400

    # Verify field exists
    field = Field.query.filter_by(event=tournament_url, name=field_name).first()
    if not field:
        return jsonify({"error": "Field not found"}), 404

    preview_requested = preview_store.is_preview_requested(tournament_url, field_name)

    # Helper function to get points for a match
    def get_points_data(match):
        points = Point.query.filter_by(match=match.uuid).order_by(Point.stamp).all()
        points_data = []
        for p in points:
            # Ensure timestamps are sent as UTC with 'Z' suffix
            stamp_str = None
            end_stamp_str = None

            if p.stamp:
                # Convert to UTC if timezone-aware, or assume UTC if naive
                if p.stamp.tzinfo is None:
                    # Naive datetime - assume it's UTC
                    stamp_str = p.stamp.replace(tzinfo=timezone.utc).isoformat()
                else:
                    # Timezone-aware - convert to UTC
                    stamp_str = p.stamp.astimezone(timezone.utc).isoformat()
                # Ensure 'Z' suffix for UTC
                if not stamp_str.endswith("Z"):
                    stamp_str = stamp_str.replace("+00:00", "Z").replace("-00:00", "Z")
                    if not stamp_str.endswith("Z"):
                        stamp_str += "Z"

            if p.end_stamp:
                if p.end_stamp.tzinfo is None:
                    end_stamp_str = p.end_stamp.replace(tzinfo=timezone.utc).isoformat()
                else:
                    end_stamp_str = p.end_stamp.astimezone(timezone.utc).isoformat()
                if not end_stamp_str.endswith("Z"):
                    end_stamp_str = end_stamp_str.replace("+00:00", "Z").replace(
                        "-00:00", "Z"
                    )
                    if not end_stamp_str.endswith("Z"):
                        end_stamp_str += "Z"

            point_data = {
                "uuid": p.uuid,
                "stamp": stamp_str,
                "end_stamp": end_stamp_str,
            }
            points_data.append(point_data)
        return points_data

    # If we're tracking a specific match, check its status
    if current_match_id:
        match = Match.query.filter_by(
            uuid=current_match_id, event=tournament_url
        ).first()
        if match:
            # Continue recording if match is still IN_PROGRESS (not yet finalized)
            if match.status == MatchStatus.IN_PROGRESS:
                return jsonify(
                    {
                        "hasActiveMatch": True,
                        "match_id": match.uuid,
                        "match_name": match.name,
                        "start_time": (
                            match.confirmed_start_time.isoformat()
                            if match.confirmed_start_time
                            else None
                        ),
                        "status": match.status,
                        "points": get_points_data(match),
                        "preview_requested": preview_requested,
                    }
                )
            else:
                # Match is completed or in another state - stop recording
                return jsonify(
                    {
                        "hasActiveMatch": False,
                        "match_id": match.uuid,
                        "status": match.status,
                        "reason": "match_completed",
                        "preview_requested": preview_requested,
                    }
                )
        else:
            # Match not found - might have been deleted, stop recording
            return jsonify(
                {
                    "hasActiveMatch": False,
                    "reason": "match_not_found",
                    "preview_requested": preview_requested,
                }
            )

    # No specific match tracked - find any active match on this field
    match = Match.query.filter_by(
        event=tournament_url, field=field_name, status=MatchStatus.IN_PROGRESS
    ).first()

    if match:
        return jsonify(
            {
                "hasActiveMatch": True,
                "match_id": match.uuid,
                "match_name": match.name,
                "start_time": (
                    match.confirmed_start_time.isoformat()
                    if match.confirmed_start_time
                    else None
                ),
                "status": match.status,
                "points": get_points_data(match),
                "preview_requested": preview_requested,
            }
        )
    else:
        return jsonify(
            {
                "hasActiveMatch": False,
                "preview_requested": preview_requested,
            }
        )


@bp.route("/record/request-preview", methods=["POST"])
@login_required
def record_request_preview():
    """TO requests preview for a field; creates sentinel so record pages send frames."""
    data = request.get_json(silent=True) or {}
    tournament_url = (
        data.get("tournament") or request.args.get("tournament") or ""
    ).strip()
    field_name = (data.get("field") or request.args.get("field") or "").strip()
    if not tournament_url or not field_name:
        return jsonify({"error": "tournament and field required"}), 400
    if is_not_TO(tournament_url):
        return jsonify({"error": "Only tournament organizers can request preview"}), 403
    field = Field.query.filter_by(event=tournament_url, name=field_name).first()
    if not field:
        return jsonify({"error": "Field not found"}), 404
    preview_store.set_preview_requested(tournament_url, field_name)
    return jsonify({"success": True})


@bp.route("/record/release-preview", methods=["POST", "DELETE"])
@login_required
def record_release_preview():
    """TO releases preview for a field; deletes sentinel and optionally cleans pending/serving."""
    data = request.get_json(silent=True) or {}
    tournament_url = (
        data.get("tournament") or request.args.get("tournament") or ""
    ).strip()
    field_name = (data.get("field") or request.args.get("field") or "").strip()
    if not tournament_url or not field_name:
        return jsonify({"error": "tournament and field required"}), 400
    if is_not_TO(tournament_url):
        return jsonify({"error": "Only tournament organizers can release preview"}), 403
    preview_store.clear_preview_requested(tournament_url, field_name)
    return jsonify({"success": True})


@bp.route("/record/preview-frame", methods=["POST"])
def record_preview_frame_post():
    """Record page uploads a preview frame (JPEG). Writes to path A (pending). Requires camera_key."""
    tournament_url = (
        request.args.get("tournament") or request.form.get("tournament") or ""
    ).strip()
    field_name = (request.args.get("field") or request.form.get("field") or "").strip()
    camera_name = (
        request.args.get("camera_name") or request.form.get("camera_name") or "camera"
    ).strip() or "camera"
    if not tournament_url or not field_name:
        return jsonify({"error": "tournament and field required"}), 400
    is_valid, error_response = require_camera_key(tournament_url, field_name)
    if not is_valid:
        return error_response[0], error_response[1]
    field = Field.query.filter_by(event=tournament_url, name=field_name).first()
    if not field:
        return jsonify({"error": "Field not found"}), 404
    data = request.get_data()
    if not data:
        return jsonify({"error": "No image data"}), 400
    preview_store.write_pending(tournament_url, field_name, camera_name, data)
    return jsonify({"success": True})


@bp.route("/record/preview-frame-consumed", methods=["GET"])
def record_preview_frame_consumed():
    """Record page polls: true if no file at A (frame was consumed by TO). Requires camera_key."""
    tournament_url = request.args.get("tournament", "").strip()
    field_name = request.args.get("field", "").strip()
    camera_name = (request.args.get("camera_name") or "camera").strip() or "camera"
    if not tournament_url or not field_name:
        return jsonify({"error": "tournament and field required"}), 400
    is_valid, error_response = require_camera_key(tournament_url, field_name)
    if not is_valid:
        return error_response[0], error_response[1]
    consumed = not preview_store.has_pending(tournament_url, field_name, camera_name)
    return jsonify({"consumed": consumed})


@bp.route("/record/preview-metadata", methods=["POST"])
def record_preview_metadata_post():
    """Record page sends device metadata (storage, battery) with camera_key. Optional fields."""
    tournament_url = (
        request.args.get("tournament") or (request.json or {}).get("tournament") or ""
    ).strip()
    field_name = (
        request.args.get("field") or (request.json or {}).get("field") or ""
    ).strip()
    camera_name = (
        (
            request.args.get("camera_name")
            or (request.json or {}).get("camera_name")
            or "camera"
        )
    ).strip() or "camera"
    if not tournament_url or not field_name:
        return jsonify({"error": "tournament and field required"}), 400
    is_valid, error_response = require_camera_key(tournament_url, field_name)
    if not is_valid:
        return error_response[0], error_response[1]
    data = request.get_json(silent=True) or {}
    storage_usage = data.get("storage_usage")
    storage_quota = data.get("storage_quota")
    battery_level = data.get("battery_level")
    if storage_usage is not None:
        storage_usage = float(storage_usage)
    if storage_quota is not None:
        storage_quota = float(storage_quota)
    if battery_level is not None:
        battery_level = float(battery_level)
    preview_store.write_metadata(
        tournament_url,
        field_name,
        camera_name,
        storage_usage,
        storage_quota,
        battery_level,
    )
    return jsonify({"success": True})


@bp.route("/record/preview-metadata", methods=["GET"])
@login_required
def record_preview_metadata_get():
    """TO: get device metadata for a camera (storage, battery). Returns JSON or 204."""
    tournament_url = request.args.get("tournament", "").strip()
    field_name = request.args.get("field", "").strip()
    camera_name = (request.args.get("camera_name") or "camera").strip() or "camera"
    if not tournament_url or not field_name:
        return jsonify({"error": "tournament and field required"}), 400
    if is_not_TO(tournament_url):
        return (
            jsonify({"error": "Only tournament organizers can get preview metadata"}),
            403,
        )
    meta = preview_store.read_metadata(tournament_url, field_name, camera_name)
    if not meta:
        return "", 204
    return jsonify(meta)


@bp.route("/record/preview-cameras", methods=["GET"])
@login_required
def record_preview_cameras():
    """TO: list camera_name s that have a recent frame for this field (for dropdown)."""
    tournament_url = request.args.get("tournament", "").strip()
    field_name = request.args.get("field", "").strip()
    if not tournament_url or not field_name:
        return jsonify({"error": "tournament and field required"}), 400
    if is_not_TO(tournament_url):
        return (
            jsonify({"error": "Only tournament organizers can list preview cameras"}),
            403,
        )
    field = Field.query.filter_by(event=tournament_url, name=field_name).first()
    if not field:
        return jsonify({"error": "Field not found"}), 404
    cameras = preview_store.list_cameras_with_recent_frame(tournament_url, field_name)
    return jsonify({"cameras": cameras})


@bp.route("/record/preview-frame", methods=["GET"])
@login_required
def record_preview_frame_get():
    """TO: get latest preview frame for a camera. Moves A→B then serves B, or serves stale B or 204."""
    from flask import send_file, make_response
    import io

    tournament_url = request.args.get("tournament", "").strip()
    field_name = request.args.get("field", "").strip()
    camera_name = (request.args.get("camera_name") or "camera").strip() or "camera"
    if not tournament_url or not field_name:
        return jsonify({"error": "tournament and field required"}), 400
    if is_not_TO(tournament_url):
        return (
            jsonify({"error": "Only tournament organizers can get preview frame"}),
            403,
        )
    # Prevent Safari (and others) from caching; Safari may not send cookies with img requests.
    no_cache_headers = {
        "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
        "Pragma": "no-cache",
    }
    # Move pending to serving if we have a new frame
    preview_store.move_pending_to_serving(tournament_url, field_name, camera_name)
    data, mtime = preview_store.read_serving(tournament_url, field_name, camera_name)
    if not data:
        resp = make_response("", 204)
        resp.headers.update(no_cache_headers)
        return resp
    # Optional: treat stale B as "camera offline" after RECENT_MTIME_SEC
    if mtime and (time.time() - mtime) > preview_store.RECENT_MTIME_SEC:
        resp = make_response("", 204)
        resp.headers.update(no_cache_headers)
        return resp
    resp = send_file(
        io.BytesIO(data),
        mimetype="image/jpeg",
        as_attachment=False,
    )
    resp.headers.update(no_cache_headers)
    return resp


@bp.route("/record/upload-chunk", methods=["POST"])
def record_upload_chunk():
    """Receive one fMP4 fragment for point recording (container=mp4). Camera key required."""
    import os
    from flask import current_app
    from datetime import datetime
    import fcntl

    tournament_url = request.form.get("tournament")
    field_name = request.form.get("field")
    match_id = request.form.get("match_id")
    session_id = request.form.get("session_id")
    chunk_start_timestamp = request.form.get(
        "chunk_start_timestamp"
    )  # Absolute world time when chunk started
    recording_session_start_time = request.form.get("recording_session_start_time")
    chunk_duration = request.form.get("chunk_duration")  # Duration in milliseconds
    camera_name = request.form.get("camera_name")
    blob_event_timestamp_ms_raw = request.form.get("blob_event_timestamp_ms")
    is_init_segment_raw = request.form.get("is_init_segment")
    # Validate camera access key
    is_valid, error_response = require_camera_key(tournament_url, field_name)
    if not is_valid:
        return error_response[0], error_response[1]

    if not tournament_url or not field_name or not session_id or not match_id:
        return jsonify({"error": "Missing required parameters"}), 400

    # Verify field exists
    field = Field.query.filter_by(event=tournament_url, name=field_name).first()
    if not field:
        return jsonify({"error": "Field not found"}), 404
    db.session.remove()

    if "chunk" not in request.files:
        return jsonify({"error": "No chunk file provided"}), 400

    chunk_file = request.files["chunk"]
    if chunk_file.filename == "":
        return jsonify({"error": "Empty chunk file"}), 400

    # New record page: fragmented MP4 only (fMP4 from MediaRecorder).
    container = (request.form.get("container") or "mp4").strip().lower()
    if container != "mp4":
        return jsonify({"error": "Only container=mp4 is supported"}), 400
    chunk_ext = "mp4"

    upload_dir = os.path.join(
        current_app.root_path,
        "../static/uploads/videos",
        tournament_url,
        field_name,
        match_id,
        camera_name,
    )
    os.makedirs(upload_dir, exist_ok=True)

    chunks_meta_path = os.path.join(upload_dir, "chunks_meta.json")

    def parse_timestamp(val):
        if val is None or val == "":
            return None
        s = str(val).strip()
        if not s:
            return None
        try:
            return float(val)
        except (TypeError, ValueError):
            pass
        if "T" in s and ("Z" in s or "+" in s or "-" in s[-6:]):
            return s  # Store ISO string as-is; footage.py accepts both
        try:
            return float(s)
        except ValueError:
            return s

    def parse_float_opt(val):
        if val is None or val == "":
            return None
        try:
            return float(val)
        except (TypeError, ValueError):
            return None

    def parse_bool(val):
        if val is None:
            return False
        return str(val).strip().lower() in {"1", "true", "yes", "on"}

    chunk_index = None
    try:
        file_mode = "r+" if os.path.exists(chunks_meta_path) else "w+"
        with open(chunks_meta_path, file_mode) as lock_file:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except IOError:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)

            lock_file.seek(0)
            content = lock_file.read()
            if content.strip():
                try:
                    chunks_meta = json.loads(content)
                except (json.JSONDecodeError, ValueError):
                    chunks_meta = {}
            else:
                chunks_meta = {}

            # Assign index under lock so concurrent uploads never get same index or overwrite
            chunk_index = len(chunks_meta)
            chunk_filename = f"chunk_{chunk_index}.{chunk_ext}"
            chunk_path = os.path.join(upload_dir, chunk_filename)
            chunk_file.save(chunk_path)

            chunk_meta = {
                "filename": chunk_filename,
                "session_id": session_id,
                "chunk_start_timestamp": parse_timestamp(chunk_start_timestamp),
                "chunk_duration": float(chunk_duration),
                "camera_name": camera_name,
                "recording_session_start_time": parse_timestamp(
                    recording_session_start_time
                ),
                "blob_event_timestamp_ms": parse_float_opt(blob_event_timestamp_ms_raw),
                "is_init_segment": parse_bool(is_init_segment_raw),
            }
            chunks_meta[str(chunk_index)] = chunk_meta

            lock_file.seek(0)
            lock_file.truncate(0)
            json.dump(chunks_meta, lock_file, indent=2)
            lock_file.flush()
    except (IOError, OSError) as e:
        print("error writing :sob:")
        return jsonify({"error": "Failed to save chunk"}), 500

    try:
        with open(chunk_path, "rb") as f:
            head = f.read(4)
        file_size = os.path.getsize(chunk_path)
        current_app.logger.info(
            "record chunk %s: size=%s bytes, ext=%s, first4=%s",
            chunk_index,
            file_size,
            chunk_ext,
            head.hex() if len(head) == 4 else "short",
        )
    except Exception as e:
        current_app.logger.warning("record chunk debug read failed: %s", e)

    return jsonify(
        {"success": True, "chunk_index": chunk_index, "session_id": session_id}
    )


@bp.route("/record/finalize", methods=["POST"])
def record_finalize():
    data = request.json
    tournament_url = data.get("tournament")
    field_name = data.get("field")
    match_id = data.get("match_id")
    camera_name = data.get("camera_name")

    # Validate camera access key
    is_valid, error_response = require_camera_key(tournament_url, field_name)
    if not is_valid:
        return error_response[0], error_response[1]

    if not tournament_url or not field_name or not match_id or not camera_name:
        return jsonify({"error": "Missing required parameters"}), 400

    # Verify field exists
    if not Field.query.filter_by(event=tournament_url, name=field_name).first():
        return jsonify({"error": "Field not found"}), 404

    # Directory where chunks are stored (same layout as upload-chunk: tournament/field/match_id/camera_name)
    chunk_dir = path.join(
        current_app.root_path,
        "../static/uploads/videos",
        tournament_url,
        field_name,
        match_id,
        camera_name,
    )
    if not path.exists(chunk_dir):
        return jsonify({"error": "Recording directory not found"}), 404

    # Worker runs in a background thread; it must run inside an app context for db.session to persist.
    app = current_app._get_current_object()
    logger = current_app.logger
    current_app.logger.info(
        "record_finalize: submitting worker for match_id=%s camera_name=%s chunk_dir=%s",
        match_id,
        camera_name,
        chunk_dir,
    )

    def run_finalize_with_app_context():
        with app.app_context():
            finalize_recording_worker(
                logger,
                tournament_url,
                field_name,
                match_id,
                camera_name,
                chunk_dir,
            )

    _ = executor.submit(run_finalize_with_app_context)

    # For now, just return success
    return jsonify(
        {
            "success": True,
            "message": "all recordings uploaded; processing has begun",
            "match_id": match_id,
        }
    )


@bp.route(
    "/tournaments/<tournament_url>/matches/<match_id>/retry-finalization",
    methods=["POST"],
)
@login_required
def retry_match_finalization(tournament_url: str, match_id: str):
    import os

    if not current_user_can_retry_finalization(current_user):
        return (
            jsonify(
                {
                    "success": False,
                    "error": (
                        f"Forbidden. Add your user id to {RETRY_FINALIZATION_USER_IDS_ENV} "
                        "to enable retry finalization."
                    ),
                }
            ),
            403,
        )

    match = Match.query.filter_by(uuid=match_id, event=tournament_url).first()
    if not match:
        return jsonify({"success": False, "error": "Match not found"}), 404
    if not match.field:
        return jsonify({"success": False, "error": "Match has no field"}), 400
    field_name = match.field

    root_dir = os.path.join(
        current_app.root_path,
        "../static/uploads/videos",
        tournament_url,
        field_name,
        match_id,
    )
    if not os.path.isdir(root_dir):
        return (
            jsonify(
                {
                    "success": False,
                    "error": "No recording artifacts found for this match",
                }
            ),
            404,
        )

    chunk_dirs: list[tuple[str, str]] = []
    for camera_name in sorted(os.listdir(root_dir)):
        chunk_dir = os.path.join(root_dir, camera_name)
        if not os.path.isdir(chunk_dir):
            continue
        if not os.path.exists(os.path.join(chunk_dir, "chunks_meta.json")):
            continue
        chunk_dirs.append((camera_name, chunk_dir))

    if not chunk_dirs:
        return (
            jsonify(
                {"success": False, "error": "No chunk metadata found for this match"}
            ),
            404,
        )

    app = current_app._get_current_object()
    logger = current_app.logger
    for camera_name, chunk_dir in chunk_dirs:
        logger.info(
            "retry_match_finalization: submitting worker for match_id=%s camera_name=%s chunk_dir=%s requested_by=%s",
            match_id,
            camera_name,
            chunk_dir,
            getattr(current_user, "id", None),
        )

        def run_finalize_with_app_context(
            chunk_dir: str = chunk_dir, camera_name: str = camera_name
        ):
            with app.app_context():
                finalize_recording_worker(
                    logger,
                    tournament_url,
                    field_name,
                    match_id,
                    camera_name,
                    chunk_dir,
                )

        _ = executor.submit(run_finalize_with_app_context)

    return jsonify(
        {
            "success": True,
            "message": f"Requeued finalization for {len(chunk_dirs)} recording(s).",
        }
    )


@bp.route("/tournaments/<tournament_url>/user-upload", methods=["POST"])
@login_required
def user_upload_video_footage(tournament_url: str):
    """Authenticated endpoint for raw clip generation or direct edited uploads."""
    import os

    _tournament, err = _require_registered_player_for_upload(tournament_url)
    if err:
        return err

    try:
        upload_mode = _normalize_user_upload_mode(request.form.get("upload_mode"))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    field_id_raw = request.form.get("field_id") or request.form.get("field")
    match_uuid = (request.form.get("match_uuid") or "").strip() or None

    video_file = request.files.get("video") or request.files.get("file")
    if not video_file or video_file.filename == "":
        return jsonify({"error": "video file is required"}), 400

    start_world_override = request.form.get("start_world") or request.form.get(
        "start_timestamp"
    )
    try:
        start_world_override = _normalize_user_upload_start_world(start_world_override)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    camera_name_override = (request.form.get("camera_name") or "").strip()
    field_obj = None
    field_name_resolved = None
    if upload_mode == "raw_clips":
        if not field_id_raw:
            return jsonify({"error": "field_id is required for raw clip uploads"}), 400
        try:
            field_id = int(field_id_raw)
        except ValueError:
            return jsonify({"error": "field_id must be an integer"}), 400
        field_obj = Field.query.filter_by(event=tournament_url, id=field_id).first()
        if not field_obj:
            return jsonify({"error": "Field not found"}), 404
        field_name_resolved = field_obj.name
    else:
        if not match_uuid:
            return jsonify({"error": "match_uuid is required for edited uploads"}), 400
        match_obj = Match.query.filter_by(uuid=match_uuid, event=tournament_url).first()
        if not match_obj:
            return jsonify({"error": "Match not found"}), 404
        if not match_obj.field:
            return jsonify({"error": "Selected match has no field"}), 400
        field_name_resolved = match_obj.field

    db.session.remove()

    upload_group_name = uuid.uuid4().hex[:12]
    original_filename = video_file.filename
    orig_stem = path.splitext(path.basename(original_filename))[0] or "upload"
    if camera_name_override:
        orig_stem = camera_name_override
    ext = path.splitext(original_filename)[1].lower()
    if not ext:
        ext = ".webm"

    upload_dir = path.join(
        current_app.root_path,
        "../static/uploads/videos",
        tournament_url,
        field_name_resolved,
        "user_uploads",
        upload_group_name,
    )
    os.makedirs(upload_dir, exist_ok=True)

    saved_name = f"source{ext}"
    saved_abs_path = path.join(upload_dir, saved_name)
    video_file.save(saved_abs_path)

    uploader_user_id = str(current_user.id)
    uploader_user_type = current_user.__class__.__name__.lower()

    app_obj = current_app._get_current_object()  # type: ignore[attr-defined]
    logger = current_app.logger

    batch_camera_name = camera_name_override or orig_stem
    try:
        if upload_mode == "edited_match":
            create_direct_user_upload_camera(
                logger,
                app_obj,
                tournament_url=tournament_url,
                match_uuid=match_uuid or "",
                camera_name=batch_camera_name,
                upload_key=upload_group_name,
                saved_abs_path=saved_abs_path,
                uploader_user_id=uploader_user_id,
                uploader_user_type=uploader_user_type,
            )
        else:
            register_batch_upload_completion(
                logger,
                app_obj,
                tournament_url=tournament_url,
                field_name=field_name_resolved,
                batch_id=upload_group_name,
                batch_index=0,
                batch_total=1,
                camera_name=batch_camera_name,
                upload_id=upload_group_name,
                saved_abs_path=saved_abs_path,
                start_world_override=start_world_override,
                incoming_dir_name=None,
                uploader_user_id=uploader_user_id,
                uploader_user_type=uploader_user_type,
            )
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    return jsonify(
        {
            "success": True,
            "message": (
                "Upload received; YouTube upload has begun."
                if upload_mode == "edited_match"
                else "Upload received; processing has begun."
            ),
            "upload_group_name": upload_group_name,
        }
    )


def _user_upload_incoming_dir_name(upload_id: str, batch_index: int) -> str:
    """Return the expected directory name for an upload batch.

    Args:
        upload_id: The unique upload session identifier.
        batch_index: Zero-based index of this batch within the session.

    Returns:
        A string of the form ``"<upload_id>__<batch_index:06d>"``.
    """
    return f"{upload_id}__{batch_index:06d}"


def _locate_user_upload_incoming_dir(
    tournament_url: str, upload_id: str, batch_index: int | None = None
):
    """Search all fields for an in-progress user-upload directory.

    Looks in ``static/uploads/videos/<tournament>/<field>/user_uploads/_incoming/``
    for a directory whose name or whose ``meta.json`` content matches
    *upload_id* and optionally *batch_index*.

    Args:
        tournament_url: Tournament URL slug used to scope the search.
        upload_id: The unique upload session identifier to look for.
        batch_index: Optional batch index; when provided, only directories
            with a matching ``batch_index`` in ``meta.json`` are returned.

    Returns:
        A ``(abs_path, field_name)`` tuple when found, or ``(None, None)``
        when no matching directory exists.
    """
    incoming_root = path.join(
        current_app.root_path,
        "../static/uploads/videos",
        tournament_url,
    )

    for field_obj in Field.query.filter_by(event=tournament_url).all():
        incoming_base = path.join(
            incoming_root,
            field_obj.name,
            "user_uploads",
            "_incoming",
        )
        if not path.isdir(incoming_base):
            continue

        candidate_names = []
        if batch_index is not None:
            candidate_names.append(
                _user_upload_incoming_dir_name(upload_id, batch_index)
            )
        candidate_names.append(upload_id)

        for dir_name in candidate_names:
            candidate = path.join(incoming_base, dir_name)
            if path.exists(candidate):
                return candidate, field_obj.name

        for dir_name in listdir(incoming_base):
            candidate = path.join(incoming_base, dir_name)
            meta_path = path.join(candidate, "meta.json")
            if not path.exists(meta_path):
                continue
            try:
                with open(meta_path, "r") as f:
                    meta = json.load(f)
            except (OSError, json.JSONDecodeError):
                continue
            if str(meta.get("upload_id") or "").strip() != upload_id:
                continue
            if batch_index is not None:
                try:
                    existing_batch_index = int(
                        meta.get("batch_index")
                        if meta.get("batch_index") is not None
                        else 0
                    )
                except (TypeError, ValueError):
                    existing_batch_index = 0
                if existing_batch_index != batch_index:
                    continue
            return candidate, field_obj.name

    return None, None


def _normalize_user_upload_start_world(raw_value: str | None) -> str | None:
    """Parse and normalise a ``start_world`` upload timestamp to UTC ISO format.

    Accepts any ISO-8601 string with a timezone designator (including ``Z``).

    Args:
        raw_value: Raw timestamp string from the request, or ``None``.

    Returns:
        UTC ISO-8601 string ending with ``"Z"``, or ``None`` when *raw_value*
        is empty or ``None``.

    Raises:
        ValueError: If the value is not a valid timezone-aware ISO timestamp.
    """
    if raw_value is None:
        return None
    s = raw_value.strip()
    if not s:
        return None
    if s.endswith("Z"):
        s = s.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError as exc:
        raise ValueError(
            "start_world must be an ISO timestamp with timezone, e.g. 2026-03-18T01:23:45Z"
        ) from exc
    if dt.tzinfo is None:
        raise ValueError(
            "start_world must include timezone, e.g. 2026-03-18T01:23:45Z or 2026-03-17T18:23:45-07:00"
        )
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _normalize_user_upload_mode(raw_value: str | None) -> str:
    """Normalise the user-upload mode string to a canonical value.

    Accepts several alias strings and maps them to ``"raw_clips"`` or
    ``"edited_match"``.

    Args:
        raw_value: Raw mode string from the request, or ``None``.

    Returns:
        ``"raw_clips"`` or ``"edited_match"``.

    Raises:
        ValueError: If *raw_value* is not a recognised mode alias.
    """
    mode = (raw_value or "raw_clips").strip().lower()
    if mode in ("raw_clips", "raw", "clips"):
        return "raw_clips"
    if mode in ("edited_match", "edited", "direct"):
        return "edited_match"
    raise ValueError("upload_mode must be raw_clips or edited_match")


def _require_registered_player_for_upload(tournament_url: str):
    tournament = Tournament.query.filter_by(url=tournament_url).first()
    if not tournament:
        return None, (jsonify({"error": "Tournament not found"}), 404)
    if current_user.__class__.__name__.lower() != "player":
        return None, (
            jsonify({"error": "Only registered players can upload footage"}),
            403,
        )
    if not is_player_registered(tournament, str(current_user.id)):
        return None, (
            jsonify(
                {
                    "error": "You must be registered for this tournament to upload footage"
                }
            ),
            403,
        )
    return tournament, None


@bp.route("/tournaments/<tournament_url>/user-upload/planning", methods=["GET"])
@login_required
def user_upload_planning(tournament_url: str):
    _, err = _require_registered_player_for_upload(tournament_url)
    if err:
        return err

    field_id_raw = (
        request.args.get("field_id") or request.args.get("field") or ""
    ).strip()
    if not field_id_raw:
        return jsonify({"error": "field_id is required"}), 400
    try:
        field_id = int(field_id_raw)
    except ValueError:
        return jsonify({"error": "field_id must be an integer"}), 400

    field_obj = Field.query.filter_by(event=tournament_url, id=field_id).first()
    if not field_obj:
        return jsonify({"error": "Field not found"}), 404

    matches = (
        Match.query.filter_by(event=tournament_url, field=field_obj.name)
        .order_by(
            Match.confirmed_start_time.asc(),
            Match.nominal_start_time.asc(),
            Match.name.asc(),
        )
        .all()
    )
    match_ids = [m.uuid for m in matches]
    points_by_match: dict[str, list[Point]] = {m.uuid: [] for m in matches}
    if match_ids:
        points = (
            Point.query.filter(Point.match.in_(match_ids))
            .order_by(Point.stamp.asc(), Point.uuid.asc())
            .all()
        )
        for pt in points:
            if pt.match:
                points_by_match.setdefault(pt.match, []).append(pt)

    rows = []
    for match_obj in matches:
        match_points = []
        for idx, pt in enumerate(points_by_match.get(match_obj.uuid, []), start=1):
            match_points.append(
                {
                    "uuid": str(pt.uuid),
                    "index": idx,
                    "stamp": (
                        pt.stamp.replace(tzinfo=timezone.utc)
                        .isoformat()
                        .replace("+00:00", "Z")
                        if pt.stamp
                        else None
                    ),
                    "end_stamp": (
                        pt.end_stamp.replace(tzinfo=timezone.utc)
                        .isoformat()
                        .replace("+00:00", "Z")
                        if pt.end_stamp
                        else None
                    ),
                }
            )
        rows.append(
            {
                "uuid": str(match_obj.uuid),
                "name": match_obj.name,
                "field_name": field_obj.name,
                "points": match_points,
            }
        )

    return jsonify(
        {
            "field": {"id": field_obj.id, "name": field_obj.name},
            "matches": rows,
        }
    )


@bp.route("/tournaments/<tournament_url>/user-upload/chunk", methods=["POST"])
@login_required
def user_upload_video_footage_chunk(tournament_url: str):
    """
    Chunked upload endpoint for large user footage files.
    Each request contains one chunk (<100MB from frontend).
    """
    import os
    import re

    _tournament, err = _require_registered_player_for_upload(tournament_url)
    if err:
        return err

    field_id_raw = request.form.get("field_id") or request.form.get("field")
    match_uuid = (request.form.get("match_uuid") or "").strip() or None
    try:
        upload_mode = _normalize_user_upload_mode(request.form.get("upload_mode"))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    upload_id = (request.form.get("upload_id") or "").strip()
    chunk_index_raw = (request.form.get("chunk_index") or "").strip()
    total_chunks_raw = (request.form.get("total_chunks") or "").strip()
    filename = (request.form.get("filename") or "source.webm").strip()
    content_type = (request.form.get("content_type") or "").strip()
    start_world_override = (request.form.get("start_world") or "").strip() or None
    camera_name_override = (request.form.get("camera_name") or "").strip() or None
    chunk_file = request.files.get("chunk")

    batch_id_raw = (request.form.get("batch_id") or "").strip()
    batch_index_raw = (request.form.get("batch_index") or "").strip()
    batch_total_raw = (request.form.get("batch_total") or "").strip()

    if not upload_id or chunk_file is None:
        return jsonify({"error": "upload_id and chunk are required"}), 400
    if not re.fullmatch(r"[A-Za-z0-9_-]{6,80}", upload_id):
        return jsonify({"error": "Invalid upload_id"}), 400

    try:
        chunk_index = int(chunk_index_raw)
        total_chunks = int(total_chunks_raw)
    except ValueError:
        return jsonify({"error": "chunk_index and total_chunks must be integers"}), 400
    if chunk_index < 0 or total_chunks <= 0 or chunk_index >= total_chunks:
        return jsonify({"error": "Invalid chunk_index/total_chunks"}), 400

    try:
        batch_total = int(batch_total_raw) if batch_total_raw != "" else 1
    except ValueError:
        return jsonify({"error": "batch_total must be an integer"}), 400
    try:
        batch_index = int(batch_index_raw) if batch_index_raw != "" else 0
    except ValueError:
        return jsonify({"error": "batch_index must be an integer"}), 400
    if batch_total < 1 or batch_index < 0 or batch_index >= batch_total:
        return jsonify({"error": "Invalid batch_index/batch_total"}), 400

    if batch_total > 1:
        if not batch_id_raw or not re.fullmatch(r"[A-Za-z0-9_-]{6,80}", batch_id_raw):
            return jsonify({"error": "batch_id is required when batch_total > 1"}), 400
        batch_id = batch_id_raw
    else:
        batch_id = batch_id_raw or upload_id
    try:
        start_world_override = _normalize_user_upload_start_world(start_world_override)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    filename_stem = path.splitext(path.basename(filename))[0] or "upload"
    batch_camera_name = (camera_name_override or "").strip() or filename_stem

    field_obj = None
    field_name_resolved = None
    if upload_mode == "raw_clips":
        if not field_id_raw:
            return jsonify({"error": "field_id is required for raw clip uploads"}), 400
        try:
            field_id = int(field_id_raw)
        except ValueError:
            return jsonify({"error": "field_id must be an integer"}), 400
        field_obj = Field.query.filter_by(event=tournament_url, id=field_id).first()
        if not field_obj:
            return jsonify({"error": "Field not found"}), 404
        field_name_resolved = field_obj.name
    else:
        if not match_uuid:
            return jsonify({"error": "match_uuid is required for edited uploads"}), 400
        match_obj = Match.query.filter_by(uuid=match_uuid, event=tournament_url).first()
        if not match_obj:
            return jsonify({"error": "Match not found"}), 404
        if not match_obj.field:
            return jsonify({"error": "Selected match has no field"}), 400
        field_name_resolved = match_obj.field
        field_obj = Field.query.filter_by(
            event=tournament_url, name=field_name_resolved
        ).first()
        if not field_obj:
            return jsonify({"error": "Match field not found"}), 404
        field_id = field_obj.id

    db.session.remove()
    incoming_dir_name = _user_upload_incoming_dir_name(upload_id, batch_index)

    incoming_dir = path.join(
        current_app.root_path,
        "../static/uploads/videos",
        tournament_url,
        field_name_resolved,
        "user_uploads",
        "_incoming",
        incoming_dir_name,
    )
    os.makedirs(incoming_dir, exist_ok=True)

    meta_path = path.join(incoming_dir, "meta.json")
    if path.exists(meta_path):
        with open(meta_path, "r") as f:
            existing = json.load(f)
        if existing.get("upload_id") != upload_id:
            return jsonify({"error": "upload_id mismatch"}), 400
        if int(existing.get("total_chunks") or 0) != total_chunks:
            return jsonify({"error": "total_chunks mismatch"}), 400
        if existing.get("batch_id") != batch_id:
            return jsonify({"error": "batch_id mismatch"}), 400
        if int(existing.get("batch_total") or 0) != batch_total:
            return jsonify({"error": "batch_total mismatch"}), 400
        existing_batch_index = existing.get("batch_index")
        try:
            existing_batch_index = (
                int(existing_batch_index) if existing_batch_index is not None else -1
            )
        except (TypeError, ValueError):
            existing_batch_index = -1
        if existing_batch_index != batch_index:
            current_app.logger.warning(
                "user_upload chunk batch_index mismatch: upload_id=%s incoming_dir=%s existing_batch_index=%s request_batch_index=%s batch_id=%s existing_meta=%s",
                upload_id,
                incoming_dir,
                existing_batch_index,
                batch_index,
                batch_id,
                meta_path,
            )
            return jsonify({"error": "batch_index mismatch"}), 400
        if existing.get("batch_camera_name") != batch_camera_name:
            return jsonify({"error": "camera_name mismatch"}), 400
        if int(existing.get("field_id") or 0) != field_id:
            return jsonify({"error": "field_id mismatch"}), 400
        if str(existing.get("upload_mode") or "raw_clips") != upload_mode:
            return jsonify({"error": "upload_mode mismatch"}), 400
        if (str(existing.get("match_uuid") or "").strip() or None) != match_uuid:
            return jsonify({"error": "match_uuid mismatch"}), 400

    chunk_filename = f"chunk_{chunk_index:06d}.part"
    chunk_abs_path = path.join(incoming_dir, chunk_filename)
    chunk_file.save(chunk_abs_path)

    meta = {
        "upload_id": upload_id,
        "tournament_url": tournament_url,
        "field_id": field_id,
        "field_name": field_name_resolved,
        "filename": filename,
        "content_type": content_type,
        "upload_mode": upload_mode,
        "match_uuid": match_uuid,
        "total_chunks": total_chunks,
        "start_world_override": start_world_override,
        "camera_name_override": camera_name_override,
        "batch_id": batch_id,
        "batch_index": batch_index,
        "batch_total": batch_total,
        "batch_camera_name": batch_camera_name,
        "incoming_dir_name": incoming_dir_name,
        "uploaded_by_user_id": str(current_user.id),
        "uploaded_by_user_type": current_user.__class__.__name__.lower(),
    }
    with open(meta_path, "w") as f:
        json.dump(meta, f)

    return jsonify(
        {
            "success": True,
            "upload_id": upload_id,
            "chunk_index": chunk_index,
            "total_chunks": total_chunks,
        }
    )


@bp.route("/tournaments/<tournament_url>/user-upload/complete", methods=["POST"])
@login_required
def user_upload_video_footage_complete(tournament_url: str):
    """Finalize a chunked upload, assemble source file on disk, then start processing worker."""
    import os

    _tournament, err = _require_registered_player_for_upload(tournament_url)
    if err:
        return err

    payload = request.get_json(silent=True) or {}
    upload_id = (
        payload.get("upload_id") or request.form.get("upload_id") or ""
    ).strip()
    if not upload_id:
        return jsonify({"error": "upload_id is required"}), 400

    batch_index_raw = payload.get("batch_index")
    if batch_index_raw is None:
        batch_index_raw = request.form.get("batch_index")
    if batch_index_raw in (None, ""):
        batch_index = None
    else:
        try:
            batch_index = int(batch_index_raw)
        except (TypeError, ValueError):
            return jsonify({"error": "batch_index must be an integer"}), 400

    incoming_dir, field_name = _locate_user_upload_incoming_dir(
        tournament_url, upload_id, batch_index
    )
    if not incoming_dir or not field_name:
        return jsonify({"error": "Upload not found"}), 404
    db.session.remove()

    meta_path = path.join(incoming_dir, "meta.json")
    if not path.exists(meta_path):
        return jsonify({"error": "Upload metadata missing"}), 400

    with open(meta_path, "r") as f:
        meta = json.load(f)

    if str(meta.get("uploaded_by_user_id")) != str(current_user.id):
        return jsonify({"error": "You cannot complete another user's upload"}), 403

    total_chunks = int(meta.get("total_chunks") or 0)
    if total_chunks <= 0:
        return jsonify({"error": "Invalid total_chunks metadata"}), 400

    filename = meta.get("filename") or "source.webm"
    ext = path.splitext(path.basename(filename))[1].lower() or ".webm"
    incoming_dir_name = (meta.get("incoming_dir_name") or "").strip() or path.basename(
        incoming_dir
    )
    final_dir = path.join(
        current_app.root_path,
        "../static/uploads/videos",
        tournament_url,
        field_name,
        "user_uploads",
        incoming_dir_name,
    )
    os.makedirs(final_dir, exist_ok=True)
    saved_abs_path = path.join(final_dir, f"source{ext}")

    # Assemble chunk files in order without loading entire upload into RAM.
    with open(saved_abs_path, "wb") as out:
        for i in range(total_chunks):
            part_path = path.join(incoming_dir, f"chunk_{i:06d}.part")
            if not path.exists(part_path):
                return jsonify({"error": f"Missing chunk {i}"}), 400
            with open(part_path, "rb") as inp:
                while True:
                    buf = inp.read(1024 * 1024)
                    if not buf:
                        break
                    out.write(buf)

    start_world_override = meta.get("start_world_override")
    try:
        start_world_override = _normalize_user_upload_start_world(start_world_override)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    try:
        upload_mode = _normalize_user_upload_mode(meta.get("upload_mode"))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    match_uuid = (meta.get("match_uuid") or "").strip() or None

    batch_id = (meta.get("batch_id") or "").strip() or upload_id
    try:
        batch_index = int(
            meta.get("batch_index") if meta.get("batch_index") is not None else 0
        )
    except (TypeError, ValueError):
        batch_index = 0
    try:
        batch_total = int(
            meta.get("batch_total") if meta.get("batch_total") is not None else 1
        )
    except (TypeError, ValueError):
        batch_total = 1
    batch_camera_name = (meta.get("batch_camera_name") or "").strip()
    if not batch_camera_name:
        co = (meta.get("camera_name_override") or "").strip()
        batch_camera_name = co or path.splitext(path.basename(filename))[0] or "upload"

    uploader_user_id = str(current_user.id)
    uploader_user_type = current_user.__class__.__name__.lower()

    app_obj = current_app._get_current_object()  # type: ignore[attr-defined]
    logger = current_app.logger

    try:
        if upload_mode == "edited_match":
            if not match_uuid:
                return (
                    jsonify({"error": "match_uuid is required for edited uploads"}),
                    400,
                )
            create_direct_user_upload_camera(
                logger,
                app_obj,
                tournament_url=tournament_url,
                match_uuid=match_uuid,
                camera_name=batch_camera_name,
                upload_key=upload_id,
                saved_abs_path=saved_abs_path,
                uploader_user_id=uploader_user_id,
                uploader_user_type=uploader_user_type,
            )
        else:
            register_batch_upload_completion(
                logger,
                app_obj,
                tournament_url=tournament_url,
                field_name=field_name,
                batch_id=batch_id,
                batch_index=batch_index,
                batch_total=batch_total,
                camera_name=batch_camera_name,
                upload_id=upload_id,
                saved_abs_path=saved_abs_path,
                start_world_override=start_world_override,
                incoming_dir_name=incoming_dir_name,
                uploader_user_id=uploader_user_id,
                uploader_user_type=uploader_user_type,
            )
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    # Best-effort cleanup of chunk parts after assembly.
    try:
        for i in range(total_chunks):
            part_path = path.join(incoming_dir, f"chunk_{i:06d}.part")
            if path.exists(part_path):
                os.remove(part_path)
    except Exception:
        pass

    return jsonify(
        {
            "success": True,
            "message": (
                "Upload received; YouTube upload has begun."
                if upload_mode == "edited_match"
                else "Upload received; processing has begun."
            ),
            "upload_group_name": upload_id,
        }
    )


@bp.route(
    "/tournaments/<tournament_url>/user-upload/delete-camera/<camera_uuid>",
    methods=["DELETE"],
)
@require_tournament_organizer()
def user_upload_delete_camera(tournament_url: str, camera_uuid: str):
    """TO-only: delete a user-uploaded camera highlight."""
    import os

    cam = (
        Camera.query.filter_by(uuid=camera_uuid, event=tournament_url)
        .filter_by(source_type="user_upload")
        .first()
    )
    if not cam:
        return jsonify({"error": "Camera not found"}), 404

    # Best-effort local file cleanup.
    if cam.file:
        try:
            abs_fp = path.join(current_app.root_path, "..", cam.file)
            if os.path.exists(abs_fp):
                os.remove(abs_fp)
        except Exception:
            pass

    db.session.delete(cam)
    db.session.commit()
    return jsonify({"success": True})


@bp.route("/tournaments/<tournament_url>/user-uploaded-cameras", methods=["GET"])
@require_tournament_organizer()
def user_upload_list_cameras(tournament_url: str):
    """TO-only: list user-uploaded cameras so TOs can moderate/delete them."""
    cams = (
        Camera.query.filter_by(event=tournament_url, source_type="user_upload")
        .order_by(Camera.match_uuid.asc(), Camera.name.asc())
        .all()
    )

    rows = []
    for cam in cams:
        m = Match.query.filter_by(uuid=cam.match_uuid).first()
        f = Field.query.filter_by(id=cam.field).first()
        world_start = None
        if cam.time_world:
            try:
                tw = json.loads(cam.time_world)
                if isinstance(tw, list) and tw:
                    world_start = str(tw[0])
            except Exception:
                world_start = None
        uploader = None
        if cam.uploaded_by_user_type and cam.uploaded_by_user_id:
            uploader = f"{cam.uploaded_by_user_type}:{cam.uploaded_by_user_id}"
        elif cam.uploaded_by_user_id:
            uploader = str(cam.uploaded_by_user_id)
        rows.append(
            {
                "uuid": cam.uuid,
                "match_uuid": cam.match_uuid,
                "match_name": m.name if m else cam.match_uuid,
                "field_name": f.name if f else str(cam.field),
                "camera_name": cam.name,
                "status": cam.status,
                "user": uploader,
                "world_start_timestamp": world_start,
                "link": cam.link,
                "file": cam.file,
                "uploaded_by_user_id": cam.uploaded_by_user_id,
                "uploaded_by_user_type": cam.uploaded_by_user_type,
                "manifest_only": False,
                "error": None,
            }
        )

    rows.extend(list_batch_manifest_rows(tournament_url))
    return jsonify({"cameras": rows})


@bp.route("/<tournament_url>/update-settings", methods=["POST"])
@login_required
def update_tournament_settings(tournament_url):
    """Update tournament settings."""
    if is_not_TO(tournament_url):
        return (
            jsonify(
                {
                    "success": False,
                    "error": "Only tournament organizers can access this page",
                }
            ),
            403,
        )

    tournament = Tournament.query.filter_by(url=tournament_url).first_or_404()

    tournament.name = request.form["name"]
    tournament.location = request.form.get("location", "")
    tournament.about = request.form.get("about", "")
    tournament.head_refs_allowed_list = request.form.get("head_refs_allowed_list", "")
    tournament.head_refs_allow_reffing_teams = (
        "head_refs_allow_reffing_teams" in request.form
    )
    tournament.head_refs_allow_anyone = "head_refs_allow_anyone" in request.form
    tournament.published = "published" in request.form
    tournament.schedule_published = "schedule_published" in request.form
    if not tournament.league_id and tournament.registrable_config:
        rc = tournament.registrable_config
        rc.team_reg_fee = float(request.form.get("team_reg_fee", 0))
        rc.player_reg_fee = float(request.form.get("player_reg_fee", 0))
        rc.terms_link = request.form.get("terms_link", "") or None
        # Legacy combined flag; if provided, acts as default for team/player when
        # more specific checkboxes are absent.
        legacy_reg_open = "registration_open" in request.form
        rc.team_registration_open = (
            "team_registration_open" in request.form or legacy_reg_open
        )
        rc.player_registration_open = (
            "player_registration_open" in request.form or legacy_reg_open
        )
        n_max = request.form.get("n_max_teams", "").strip()
        rc.n_max_teams = int(n_max) if n_max else None
        roster = request.form.get("max_team_size_roster", "").strip()
        rc.max_team_size_roster = int(roster) if roster else None
        field = request.form.get("max_team_size_field", "").strip()
        rc.max_team_size_field = int(field) if field else None
        if "require_waiver_signature" not in request.form:
            rc.waiver_filepath = None
            rc.waiver_sha256 = None

    if request.form.get("start_date"):
        tournament.start_date = datetime.strptime(
            request.form["start_date"], "%Y-%m-%d"
        )

    end_date_val = request.form.get("end_date", "").strip()
    if not end_date_val:
        return (
            jsonify(
                {
                    "success": False,
                    "error": "End date is required.",
                }
            ),
            400,
        )
    tournament.end_date = datetime.strptime(end_date_val, "%Y-%m-%d")

    db.session.commit()
    return (
        jsonify(
            {"success": True, "message": "Tournament settings updated successfully!"}
        ),
        200,
    )


@bp.route("/<tournament_url>/add-match", methods=["POST"])
@login_required
def add_match(tournament_url):
    """Add a match to tournament."""
    if is_not_TO(tournament_url):
        return (
            jsonify(
                {
                    "success": False,
                    "error": "Only tournament organizers can access this page",
                }
            ),
            403,
        )

    # Check if BREAK or JOIN is selected from the Match Type dropdown (renamed from 'dynamic')
    match_type_value = request.form.get("dynamic", "")

    if match_type_value == ScheduleType.BREAK:
        schedule_type = ScheduleType.BREAK
        set_type = SetType.SETS  # Not used for BREAK, but set a default
        nominal_length = int(request.form.get("length", 60))
    elif match_type_value == ScheduleType.JOIN:
        schedule_type = ScheduleType.JOIN
        set_type = SetType.SETS  # Not used for JOIN, but set a default
        nominal_length = 0
    else:
        if match_type_value == ScheduleType.SAFE:
            schedule_type = ScheduleType.SAFE
        elif match_type_value == ScheduleType.FAST:
            schedule_type = ScheduleType.FAST
        else:
            schedule_type = ScheduleType.STATIC
        set_type = request.form.get("match_type", SetType.SETS)
        nominal_length = int(request.form.get("length", 60))

    # BREAK and JOIN matches don't have teams/refs
    if schedule_type in (ScheduleType.BREAK, ScheduleType.JOIN):
        team1_id = None
        team1_name = ""
        team2_id = None
        team2_name = ""
        refs_initial = ""
    else:
        team1_name = request.form.get("team1", "")
        team2_name = request.form.get("team2", "")
        team1_id, _ = resolve_team_name_to_id(team1_name, tournament_url)
        team2_id, _ = resolve_team_name_to_id(team2_name, tournament_url)
        refs_initial = request.form.get("refs", "")

    ribbon = request.form.get("ribbon", "") == "on"  # Checkbox value

    match_name = request.form["match_name"]
    mn_err = match_name_char_error(match_name)
    if mn_err:
        return jsonify({"success": False, "error": mn_err}), 400

    # Validate match name uniqueness
    # BREAK and JOIN matches can have duplicate names on different fields
    # Other matches must have unique names within the tournament
    match_field = request.form.get("field", "")
    if schedule_type in (ScheduleType.BREAK, ScheduleType.JOIN):
        # For BREAK/JOIN: check uniqueness by (name, event, field)
        existing_match = Match.query.filter_by(
            event=tournament_url,
            name=match_name,
            field=match_field,
            schedule_type=schedule_type,
        ).first()
        if existing_match:
            return (
                jsonify(
                    {
                        "success": False,
                        "error": f'A {schedule_type} match with the name "{match_name}" already exists on field "{match_field}" in this tournament',
                    }
                ),
                400,
            )
    else:
        # For other matches: check uniqueness by (name, event)
        existing_match = Match.query.filter_by(
            event=tournament_url, name=match_name
        ).first()
        if existing_match:
            return (
                jsonify(
                    {
                        "success": False,
                        "error": f'A match with the name "{match_name}" already exists in this tournament',
                    }
                ),
                400,
            )

    # Get stones_per_set for STONES matches.
    stones_per_set_value = None
    if set_type == SetType.STONES:
        stones_per_set_str = request.form.get("stones_per_set")
        if stones_per_set_str:
            try:
                stones_per_set_value = int(stones_per_set_str)
            except (ValueError, TypeError):
                stones_per_set_value = None

    # Helper to check if a value is an explicit team ID (not a tag or match reference)
    def is_explicit_team_id(val: str) -> bool:
        if not val or not val.strip():
            return False
        val = val.strip()
        # Not a tag reference
        if val.lower().startswith("tag::"):
            return False
        # Not a match reference (contains ::winner or ::loser)
        if "::winner" in val.lower() or "::loser" in val.lower():
            return False
        # Must be an explicit team ID
        return True

    # For new matches, populate explicit team IDs from _initial fields
    # Tag references are resolved by querying the Tag table, match references by apply_match_dependencies
    final_team1 = None
    if team1_id:
        final_team1 = team1_id
    elif team1_name:
        if is_explicit_team_id(team1_name):
            final_team1 = team1_name
        else:
            # Try to resolve as tag reference
            resolved_team = resolve_tag_to_team(team1_name, tournament_url)
            if resolved_team:
                final_team1 = resolved_team

    final_team2 = None
    if team2_id:
        final_team2 = team2_id
    elif team2_name:
        if is_explicit_team_id(team2_name):
            final_team2 = team2_name
        else:
            # Try to resolve as tag reference
            resolved_team = resolve_tag_to_team(team2_name, tournament_url)
            if resolved_team:
                final_team2 = resolved_team

    # For refs, populate explicit team IDs and resolve tag references maintaining index structure
    final_refs = None
    if refs_initial:
        refs_initial_list = [r.strip() for r in refs_initial.split(",")]
        refs_list = [""] * len(refs_initial_list)
        has_explicit_ids = False
        for i, initial_ref in enumerate(refs_initial_list):
            if initial_ref:
                if is_explicit_team_id(initial_ref):
                    refs_list[i] = initial_ref
                    has_explicit_ids = True
                else:
                    # Try to resolve as tag reference
                    resolved_team = resolve_tag_to_team(initial_ref, tournament_url)
                    if resolved_team:
                        refs_list[i] = resolved_team
                        has_explicit_ids = True
        if has_explicit_ids:
            final_refs = ", ".join(refs_list)

    # Skip condition only for SAFE and FAST; clear for STATIC, BREAK, and JOIN
    skip_condition_raw = request.form.get("skip_condition", "").strip() or None
    skip_condition = (
        skip_condition_raw
        if schedule_type in (ScheduleType.SAFE, ScheduleType.FAST)
        else None
    )

    match = Match(
        name=match_name,
        event=tournament_url,
        team1=final_team1,
        team1_initial=team1_name,
        team2=final_team2,
        team2_initial=team2_name,
        schedule_type=schedule_type,
        set_type=set_type,
        ribbon=ribbon,
        nsets=(
            int(request.form.get("nsets", 3))
            if schedule_type not in (ScheduleType.BREAK, ScheduleType.JOIN)
            else None
        ),
        nominal_length=nominal_length,
        refs=final_refs,
        refs_initial=refs_initial,
        stones_per_set=stones_per_set_value,
        skip_condition=skip_condition,
    )
    set_match_field_from_name(tournament_url, match, request.form.get("field", ""))
    replace_match_ref_slots(match, final_refs, refs_initial)

    db.session.add(match)
    db.session.flush()  # Flush to get UUID before updating links

    # For dynamic matches, set previous_match from form and compute start time from it
    # For static matches, use the provided start_time
    if schedule_type != ScheduleType.STATIC:
        # Get previous_match from form
        prev_match_id = request.form.get("previous_match", "")
        if prev_match_id:
            # Update doubly linked list: insert this match after prev_match
            update_match_previous_link(
                match, prev_match_id, tournament_url, is_new=True
            )
        else:
            match.previous_match = None
        match.nominal_start_time = compute_dynamic_match_nominal_start_time(
            match, tournament_url
        )
    else:
        # Static matches can have manual start time
        # Prefer UTC ISO format from client conversion, fallback to datetime-local (assumed server-local)
        if request.form.get("start_time_utc"):
            # Client sent UTC ISO string
            from app.utils.datetime_helpers import to_aware_utc

            utc_str = request.form["start_time_utc"]
            try:
                dt = datetime.fromisoformat(utc_str.replace("Z", "+00:00"))
                match.nominal_start_time = dt.replace(tzinfo=None)  # Store as naive UTC
            except (ValueError, AttributeError):
                # Fallback to old format
                if request.form.get("start_time"):
                    from app.utils.datetime_helpers import parse_datetime_local_to_utc

                    match.nominal_start_time = parse_datetime_local_to_utc(
                        request.form["start_time"]
                    )
        elif request.form.get("start_time"):
            # Old format: datetime-local (assumed server-local), convert to UTC
            from app.utils.datetime_helpers import parse_datetime_local_to_utc

            match.nominal_start_time = parse_datetime_local_to_utc(
                request.form["start_time"]
            )

    # Set initial status: STATIC matches are TIME_FINALIZED, others are NOT_STARTED
    if schedule_type == ScheduleType.STATIC:
        match.status = MatchStatus.TIME_FINALIZED
    else:
        match.status = MatchStatus.NOT_STARTED

    # Validate inputs and constraints (after start time is computed)
    ok, err = validate_match_input(match, tournament_url)
    if not ok:
        db.session.rollback()
        return jsonify({"success": False, "error": err}), 400

    db.session.commit()

    try:
        recompute_all_match_times(tournament_url)
    except Exception:
        pass

    return jsonify({"success": True, "message": "Match added successfully!"}), 200


@bp.route("/<tournament_url>/add-field", methods=["POST"])
@login_required
def add_field(tournament_url):
    """Add a field to tournament."""
    if is_not_TO(tournament_url):
        return (
            jsonify(
                {
                    "success": False,
                    "error": "Only tournament organizers can access this page",
                }
            ),
            403,
        )

    tournament = Tournament.query.filter_by(url=tournament_url).first_or_404()

    # Get camera URLs from form (camera[] array)
    camera_urls = request.form.getlist("camera[]")
    # Filter out empty values
    camera_urls = [url.strip() for url in camera_urls if url.strip()]

    # Store as JSON array
    camera_value = json.dumps(camera_urls) if camera_urls else ""

    field = Field(
        event=tournament_url, name=request.form["field_name"], camera=camera_value
    )

    db.session.add(field)
    db.session.commit()

    return jsonify({"success": True, "message": "Field added successfully!"}), 200


@bp.route("/<tournament_url>/update-field", methods=["POST"])
@login_required
def update_field(tournament_url):
    """Update field."""
    if is_not_TO(tournament_url):
        return (
            jsonify(
                {
                    "success": False,
                    "error": "Only tournament organizers can access this page",
                }
            ),
            403,
        )

    field_id = request.form.get("field_id")
    if not field_id:
        return jsonify({"success": False, "error": "Field ID is required"}), 400

    field = Field.query.get_or_404(field_id)
    old_field_name = field.name
    new_field_name = request.form["field_name"]

    # Update field name
    field.name = new_field_name

    # Get camera URLs from form (camera[] array)
    camera_urls = request.form.getlist("camera[]")
    # Filter out empty values
    camera_urls = [url.strip() for url in camera_urls if url.strip()]

    # Get old camera URLs for comparison
    old_camera_urls = []
    if field.camera:
        from app.utils.camera_helpers import parse_camera_urls

        old_camera_urls = parse_camera_urls(field.camera)

    # Store as JSON array
    field.camera = json.dumps(camera_urls) if camera_urls else ""

    # Get all matches that reference this field (for both name and camera updates)
    # Use old field name if name changed, otherwise use current name
    field_name_for_query = (
        old_field_name if old_field_name != new_field_name else new_field_name
    )
    matches_to_update = Match.query.filter_by(
        event=tournament_url, field=field_name_for_query
    ).all()

    # If camera URLs changed, update matches and points that reference this field
    camera_urls_changed = old_camera_urls != camera_urls
    camera_update_count = 0
    if camera_urls_changed:
        # Build mapping from old index to new index based on URL matching
        # This handles reordering, additions, and removals
        old_to_new_index_map = {}
        for new_idx, new_url in enumerate(camera_urls):
            # Find if this URL existed in old list
            try:
                old_idx = old_camera_urls.index(new_url)
                old_to_new_index_map[str(old_idx)] = str(new_idx)
            except ValueError:
                # New URL, no mapping needed
                pass

        # Update matches that reference this field
        for match in matches_to_update:
            stream_starts = read_match_stream_starts(match)
            # Remap camera indices
            new_stream_starts = {}
            for old_idx_str, start_time in stream_starts.items():
                if old_idx_str in old_to_new_index_map:
                    new_idx_str = old_to_new_index_map[old_idx_str]
                    new_stream_starts[new_idx_str] = start_time
                # If old index not in map, camera was removed - don't include it
            replace_match_stream_starts(match, new_stream_starts)
            camera_update_count += 1

        # Update points that reference this field (via the match)
        # Get all points for matches on this field
        from models import Point
        from app.utils.camera_helpers import calculate_stream_timestamp

        point_update_count = 0
        for match in matches_to_update:
            points = Point.query.filter_by(match=match.uuid).all()

            # Get stream start times for this match
            stream_starts = read_match_stream_starts(match)

            for point in points:
                # First, handle camera_index remapping if needed
                if point.camera_index is not None:
                    old_idx_str = str(point.camera_index)
                    if old_idx_str in old_to_new_index_map:
                        # Remap to new index
                        new_idx = int(old_to_new_index_map[old_idx_str])
                        point.camera_index = new_idx
                        point_update_count += 1
                    else:
                        # Camera at this index was removed - try to find matching URL
                        # If we can't find it, set to None
                        if point.camera_index < len(old_camera_urls):
                            old_url = old_camera_urls[point.camera_index]
                            try:
                                new_idx = camera_urls.index(old_url)
                                point.camera_index = new_idx
                                point_update_count += 1
                            except ValueError:
                                # URL not found in new list, set to None
                                point.camera_index = None
                                point.stream_timestamp = None
                                point_update_count += 1
                        else:
                            # Index was out of bounds, set to None
                            point.camera_index = None
                            point.stream_timestamp = None
                            point_update_count += 1

                # Recompute stream_timestamp for all points that have a camera_index and stamp
                # This ensures timestamps are recalculated based on current stream start times
                if point.camera_index is not None and point.stamp:
                    camera_idx_str = str(point.camera_index)
                    if camera_idx_str in stream_starts:
                        stream_start_time = stream_starts[camera_idx_str]
                        new_timestamp = calculate_stream_timestamp(
                            point.stamp, stream_start_time
                        )
                        if new_timestamp is not None:
                            point.stream_timestamp = new_timestamp
                            point_update_count += 1

    # Propagate field name change to all matches that reference this field
    name_update_count = 0
    if old_field_name != new_field_name:
        for match in matches_to_update:
            set_match_field_from_name(tournament_url, match, new_field_name)
            name_update_count += 1

    # Generate success message
    update_messages = []
    if name_update_count > 0:
        update_messages.append(
            f"Updated {name_update_count} match(es) to use the new field name"
        )
    if camera_urls_changed:
        if camera_update_count > 0:
            update_messages.append(
                f"Updated camera stream data for {camera_update_count} match(es)"
            )
        if point_update_count > 0:
            update_messages.append(
                f"Updated camera indices for {point_update_count} point(s)"
            )

    msg = (
        f'Field updated successfully! {" ".join(update_messages)}.'
        if update_messages
        else "Field updated successfully!"
    )
    db.session.commit()
    return jsonify({"success": True, "message": msg}), 200


@bp.route("/<tournament_url>/delete-field", methods=["POST"])
@login_required
def delete_field(tournament_url):
    """Delete field."""
    if is_not_TO(tournament_url):
        return (
            jsonify(
                {
                    "success": False,
                    "error": "Only tournament organizers can access this page",
                }
            ),
            403,
        )

    field_id = request.form.get("field_id")
    if not field_id:
        return jsonify({"success": False, "error": "Field ID is required"}), 400

    field = Field.query.get_or_404(field_id)
    db.session.delete(field)
    db.session.commit()
    return jsonify({"success": True, "message": "Field deleted successfully!"}), 200


@bp.route("/<tournament_url>/add-tag", methods=["POST"])
@login_required
def add_tag(tournament_url):
    """Add a tag to tournament."""
    if is_not_TO(tournament_url):
        return (
            jsonify(
                {
                    "success": False,
                    "error": "Only tournament organizers can access this page",
                }
            ),
            403,
        )

    tag = Tag(event=tournament_url, name=request.form["tag_name"])

    db.session.add(tag)
    db.session.commit()

    return jsonify({"success": True, "message": "Tag added successfully!"}), 200


@bp.route("/<tournament_url>/delete-tag", methods=["POST"])
@login_required
def delete_tag(tournament_url):
    """Delete tag."""
    if is_not_TO(tournament_url):
        return (
            jsonify(
                {
                    "success": False,
                    "error": "Only tournament organizers can access this page",
                }
            ),
            403,
        )

    tag_id = request.form.get("tag_id")
    if not tag_id:
        return jsonify({"success": False, "error": "Tag ID is required"}), 400

    tag = Tag.query.get_or_404(tag_id)
    db.session.delete(tag)
    db.session.commit()
    return jsonify({"success": True, "message": "Tag deleted successfully!"}), 200


@bp.route("/<tournament_url>/update-match", methods=["POST"])
@login_required
def update_match(tournament_url):
    """Update match."""
    if is_not_TO(tournament_url):
        return (
            jsonify(
                {
                    "success": False,
                    "error": "Only tournament organizers can access this page",
                }
            ),
            403,
        )

    match_id = request.form.get("match_id")
    if not match_id:
        return jsonify({"success": False, "error": "Match ID is required"}), 400

    match = Match.query.get_or_404(match_id)
    tournament = Tournament.query.filter_by(url=tournament_url).first_or_404()

    # Check if BREAK or JOIN is selected from the Match Type dropdown (renamed from 'dynamic')
    match_type_value = request.form.get("dynamic", "")

    if match_type_value == ScheduleType.BREAK:
        schedule_type = ScheduleType.BREAK
        set_type = match.set_type  # Keep existing set_type
    elif match_type_value == ScheduleType.JOIN:
        schedule_type = ScheduleType.JOIN
        set_type = match.set_type  # Keep existing set_type
    else:
        if match_type_value == ScheduleType.SAFE:
            schedule_type = ScheduleType.SAFE
        elif match_type_value == ScheduleType.FAST:
            schedule_type = ScheduleType.FAST
        else:
            schedule_type = ScheduleType.STATIC
        set_type = request.form.get("match_type", match.set_type)

    # Allowed schedule type transitions (only these target types allowed from each source)
    _ALLOWED_SCHEDULE_TYPE_TRANSITIONS = {
        ScheduleType.STATIC: (
            ScheduleType.STATIC,
            ScheduleType.SAFE,
            ScheduleType.FAST,
        ),
        ScheduleType.SAFE: (ScheduleType.SAFE, ScheduleType.FAST),
        ScheduleType.FAST: (ScheduleType.FAST,),
        ScheduleType.BREAK: (ScheduleType.BREAK,),
        ScheduleType.JOIN: (ScheduleType.JOIN,),
    }
    current_schedule_type = match.schedule_type
    allowed = _ALLOWED_SCHEDULE_TYPE_TRANSITIONS.get(
        current_schedule_type, (current_schedule_type,)
    )
    if schedule_type not in allowed:
        return (
            jsonify(
                {
                    "success": False,
                    "error": f"Match type cannot be changed from {current_schedule_type.value} to {schedule_type.value}. "
                    "Allowed changes: Static→Safe/Fast, Safe→Fast only.",
                }
            ),
            400,
        )

    # BREAK and JOIN matches don't have teams/refs
    if schedule_type in (ScheduleType.BREAK, ScheduleType.JOIN):
        team1_id = None
        team1_name = ""
        team2_id = None
        team2_name = ""
        refs_initial = ""
    else:
        team1_name = request.form.get("team1", "")
        team2_name = request.form.get("team2", "")
        team1_id, _ = resolve_team_name_to_id(team1_name, tournament_url)
        team2_id, _ = resolve_team_name_to_id(team2_name, tournament_url)
        refs_initial = request.form.get("refs", "")

    new_match_name = request.form.get("match_name", match.name)
    mn_err = match_name_char_error(new_match_name)
    if mn_err:
        return jsonify({"success": False, "error": mn_err}), 400

    # Validate match name uniqueness (excluding current match)
    # BREAK and JOIN matches can have duplicate names on different fields
    # Other matches must have unique names within the tournament
    new_match_field = request.form.get("field", match.field or "")
    if new_match_name != match.name or new_match_field != (match.field or ""):
        if schedule_type in (ScheduleType.BREAK, ScheduleType.JOIN):
            # For BREAK/JOIN: check uniqueness by (name, event, field)
            existing_match = Match.query.filter_by(
                event=tournament_url,
                name=new_match_name,
                field=new_match_field,
                schedule_type=schedule_type,
            ).first()
            if existing_match and existing_match.uuid != match.uuid:
                return (
                    jsonify(
                        {
                            "success": False,
                            "error": f'A {schedule_type} match with the name "{new_match_name}" already exists on field "{new_match_field}" in this tournament',
                        }
                    ),
                    400,
                )
        else:
            # For other matches: check uniqueness by (name, event)
            existing_match = Match.query.filter_by(
                event=tournament_url, name=new_match_name
            ).first()
            if existing_match and existing_match.uuid != match.uuid:
                return (
                    jsonify(
                        {
                            "success": False,
                            "error": f'A match with the name "{new_match_name}" already exists in this tournament',
                        }
                    ),
                    400,
                )

    # Helper to check if a value is an explicit team ID (not a tag or match reference)
    def is_explicit_team_id(val: str) -> bool:
        if not val or not val.strip():
            return False
        val = val.strip()
        # Not a tag reference
        if val.lower().startswith("tag::"):
            return False
        # Not a match reference (contains ::winner or ::loser)
        if "::winner" in val.lower() or "::loser" in val.lower():
            return False
        # Must be an explicit team ID
        return True

    match.name = new_match_name
    set_match_field_from_name(tournament_url, match, request.form.get("field", ""))

    # Handle team1_initial changes
    old_team1_initial = match.team1_initial or ""
    match.team1_initial = team1_name
    if old_team1_initial != team1_name:
        # Clear team1, but populate if explicit team ID or resolved tag
        if team1_id:
            match.team1 = team1_id
        elif is_explicit_team_id(team1_name):
            match.team1 = team1_name
        else:
            # Try to resolve as tag reference
            resolved_team = resolve_tag_to_team(team1_name, tournament_url)
            match.team1 = resolved_team if resolved_team else None
    else:
        # If team1_initial didn't change, only update team1 if we have an explicit team_id or can resolve tag
        if team1_id:
            match.team1 = team1_id
        elif not match.team1 and team1_name:
            # Try to resolve tag if team1 is not set
            resolved_team = resolve_tag_to_team(team1_name, tournament_url)
            if resolved_team:
                match.team1 = resolved_team

    # Handle team2_initial changes
    old_team2_initial = match.team2_initial or ""
    match.team2_initial = team2_name
    if old_team2_initial != team2_name:
        # Clear team2, but populate if explicit team ID or resolved tag
        if team2_id:
            match.team2 = team2_id
        elif is_explicit_team_id(team2_name):
            match.team2 = team2_name
        else:
            # Try to resolve as tag reference
            resolved_team = resolve_tag_to_team(team2_name, tournament_url)
            match.team2 = resolved_team if resolved_team else None
    else:
        # If team2_initial didn't change, only update team2 if we have an explicit team_id or can resolve tag
        if team2_id:
            match.team2 = team2_id
        elif not match.team2 and team2_name:
            # Try to resolve tag if team2 is not set
            resolved_team = resolve_tag_to_team(team2_name, tournament_url)
            if resolved_team:
                match.team2 = resolved_team

    match.schedule_type = schedule_type
    match.set_type = set_type
    match.ribbon = request.form.get("ribbon", "") == "on"  # Checkbox value

    # BREAK and JOIN don't have nsets
    if schedule_type not in (ScheduleType.BREAK, ScheduleType.JOIN):
        match.nsets = int(request.form.get("nsets", 3))
    else:
        match.nsets = None

    # Update stones_per_set for STONES matches.
    if set_type == SetType.STONES:
        stones_per_set_str = request.form.get("stones_per_set")
        if stones_per_set_str:
            try:
                match.stones_per_set = int(stones_per_set_str)
            except (ValueError, TypeError):
                pass  # Keep existing value if invalid
    else:
        # Clear stones_per_set for non-STONES matches
        match.stones_per_set = None

    # JOIN has zero length, BREAK can have length
    if schedule_type == ScheduleType.JOIN:
        match.nominal_length = 0
    elif schedule_type == ScheduleType.BREAK:
        match.nominal_length = int(
            request.form.get("length", match.nominal_length or 60)
        )
    else:
        match.nominal_length = int(
            request.form.get("length", match.nominal_length or 60)
        )

    # Update skip_condition (only for SAFE, FAST; clear for STATIC, BREAK, and JOIN)
    skip_condition_raw = request.form.get("skip_condition", "").strip() or None
    match.skip_condition = (
        skip_condition_raw
        if schedule_type in (ScheduleType.SAFE, ScheduleType.FAST)
        else None
    )

    # If refs_initial changed, clear refs and repopulate with explicit team IDs and resolved tag references
    old_refs_initial = read_match_ref_slots(match)[1] or ""
    if old_refs_initial != refs_initial:
        # Clear refs, but populate any explicit team IDs and resolved tag references from refs_initial
        if refs_initial:
            refs_initial_list = [r.strip() for r in refs_initial.split(",")]
            refs_list = [""] * len(refs_initial_list)
            has_explicit_ids = False
            for i, initial_ref in enumerate(refs_initial_list):
                if initial_ref:
                    if is_explicit_team_id(initial_ref):
                        # Explicit team ID
                        refs_list[i] = initial_ref
                        has_explicit_ids = True
                    else:
                        # Try to resolve as tag reference
                        resolved_team = resolve_tag_to_team(initial_ref, tournament_url)
                        if resolved_team:
                            refs_list[i] = resolved_team
                            has_explicit_ids = True
            if has_explicit_ids:
                replace_match_ref_slots(match, ", ".join(refs_list), refs_initial)
            else:
                replace_match_ref_slots(match, None, refs_initial)
        else:
            replace_match_ref_slots(match, None, None)
    else:
        replace_match_ref_slots(match, match.refs, refs_initial)

    # For dynamic matches, set previous_match from form and compute start time from it
    # For static matches, ensure previous_match is cleared and use provided start_time
    if schedule_type != ScheduleType.STATIC:
        # Get previous_match from form
        prev_match_id = request.form.get("previous_match", "")
        if prev_match_id:
            # Update doubly linked list: insert this match after prev_match
            update_match_previous_link(
                match, prev_match_id, tournament_url, is_new=False
            )
        else:
            # Clear previous_match and update old previous's next_match if needed
            old_prev = match.previous_match
            match.previous_match = None
            if old_prev:
                old_prev_m = Match.query.filter_by(
                    uuid=old_prev, event=tournament_url
                ).first()
                if old_prev_m and old_prev_m.next_match == match.uuid:
                    old_prev_m.next_match = None
        match.nominal_start_time = compute_dynamic_match_nominal_start_time(
            match, tournament_url
        )
    else:
        # Static matches can have manual start time
        match.previous_match = None
        # Prefer UTC ISO format from client conversion, fallback to datetime-local (assumed server-local)
        if request.form.get("start_time_utc"):
            # Client sent UTC ISO string
            utc_str = request.form["start_time_utc"]
            try:
                dt = datetime.fromisoformat(utc_str.replace("Z", "+00:00"))
                match.nominal_start_time = dt.replace(tzinfo=None)  # Store as naive UTC
            except (ValueError, AttributeError):
                # Fallback to old format
                if request.form.get("start_time"):
                    from app.utils.datetime_helpers import parse_datetime_local_to_utc

                    match.nominal_start_time = parse_datetime_local_to_utc(
                        request.form["start_time"]
                    )
                else:
                    match.nominal_start_time = None
        elif request.form.get("start_time"):
            # Old format: datetime-local (assumed server-local), convert to UTC
            from app.utils.datetime_helpers import parse_datetime_local_to_utc

            match.nominal_start_time = parse_datetime_local_to_utc(
                request.form["start_time"]
            )
        else:
            match.nominal_start_time = None

    # Validate inputs and constraints
    ok, err = validate_match_input(match, tournament_url)
    if not ok:
        db.session.rollback()
        return jsonify({"success": False, "error": err}), 400

    db.session.flush()  # Flush before updating sequence

    # Recompute all match times (for all dynamic matches that depend on this one)
    recompute_all_match_times(tournament_url)

    db.session.commit()
    return jsonify({"success": True, "message": "Match updated successfully!"}), 200


@bp.route("/<tournament_url>/update-all-references", methods=["POST"])
@login_required
def update_all_references(tournament_url):
    """Update all match references (winner/loser) for troubleshooting."""
    if is_not_TO(tournament_url):
        return (
            jsonify(
                {
                    "success": False,
                    "error": "Only tournament organizers can access this page",
                }
            ),
            403,
        )

    from app.utils.dependencies import apply_match_dependencies

    # Get all completed matches (have a winner; skipped matches are excluded)
    completed_matches = Match.query.filter_by(
        event=tournament_url, status=MatchStatus.COMPLETED
    ).all()

    updated_count = 0
    for match in completed_matches:
        if match.match_winner in ("TEAM1", "TEAM2"):
            try:
                apply_match_dependencies(tournament_url, match)
                updated_count += 1
            except Exception as e:
                print(f"Error updating references for match {match.name}: {e}")

    if updated_count > 0:
        msg = f"Updated references for {updated_count} completed matches"
    else:
        msg = "No references were updated"
    return jsonify({"success": True, "message": msg}), 200


@bp.route("/<tournament_url>/push-back-matches", methods=["POST"])
@login_required
def push_back_matches(tournament_url):
    """Push all non-started matches backwards by a specified amount of time (in minutes)."""
    if is_not_TO(tournament_url):
        return (
            jsonify(
                {
                    "success": False,
                    "error": "Only tournament organizers can access this page",
                }
            ),
            403,
        )

    try:
        minutes = int(request.form.get("minutes", 0))
    except (ValueError, TypeError):
        return jsonify({"success": False, "error": "Invalid number of minutes"}), 400

    non_started_matches = (
        Match.query.filter_by(event=tournament_url)
        .filter(
            ~Match.status.in_(
                [MatchStatus.IN_PROGRESS, MatchStatus.COMPLETED, MatchStatus.SKIPPED]
            )
        )
        .all()
    )

    updated_count = 0
    for match in non_started_matches:
        # Push back nominal_start_time if it exists
        if match.nominal_start_time:
            match.nominal_start_time = match.nominal_start_time + timedelta(
                minutes=minutes
            )
            updated_count += 1

        # Also push back confirmed_start_time if it exists (even when start time is already finalized)
        if match.confirmed_start_time:
            match.confirmed_start_time = match.confirmed_start_time + timedelta(
                minutes=minutes
            )

    db.session.commit()

    if updated_count > 0:
        msg = (
            f"Pushed back {updated_count} non-started match(es) by {minutes} minute(s)"
        )
    else:
        msg = "No matches were updated. All matches have already started or been completed."
    return jsonify({"success": True, "message": msg}), 200


@bp.route("/<tournament_url>/autocomplete")
def tournament_autocomplete(tournament_url):
    """Autocomplete endpoint for tournament setup.
    Returns a list of suggestions with fields: type, value, label, id
    Supports both standalone events (event=url) and league events (league registrants).
    """
    q_raw = request.args.get("q", "")
    query = (q_raw or "").strip().lower()

    suggestions = []

    tournament = Tournament.query.filter_by(url=tournament_url).first()
    from app.services.registration_resolver import team_registrations_for_tournament

    # Teams registered for this tournament (event or league)
    team_regs = team_registrations_for_tournament(tournament) if tournament else []
    for reg in team_regs:
        pseudonym = (reg.pseudonym or "").strip()
        if not query or query in pseudonym.lower():
            suggestions.append(
                {
                    "type": "team",
                    "value": reg.team,  # Use team ID instead of pseudonym
                    "label": pseudonym,  # Display pseudonym in label
                    "id": reg.team,
                }
            )

    # Tags for this tournament (by name, surfaced as tag::TAG_NAME values)
    tags = (
        Tag.query.filter_by(event=tournament_url).all()
        if "Tag" in globals() or True
        else []
    )
    try:
        tags = Tag.query.filter_by(event=tournament_url).all()
    except Exception:
        tags = []
    for t in tags:
        name = (t.name or "").strip()
        if not query or query in name.lower():
            tag_ref = f"tag::{name}"
            suggestions.append(
                {"type": "tag", "value": tag_ref, "label": tag_ref, "id": t.id}
            )

    # Matches in this tournament (by name)
    # Exclude BREAK and JOIN matches entirely
    matches = (
        Match.query.filter_by(event=tournament_url)
        .filter(Match.schedule_type.notin_([ScheduleType.BREAK, ScheduleType.JOIN]))
        .all()
    )
    for m in matches:
        name = (m.name or "").strip()

        # Also offer winner/loser variants to help dynamic references (new format)
        winner_label = f"{name}::winner"
        loser_label = f"{name}::loser"
        if not query or query in winner_label.lower():
            suggestions.append(
                {
                    "type": "result",
                    "value": winner_label,
                    "label": winner_label,
                    "id": m.uuid,
                }
            )
        if not query or query in loser_label.lower():
            suggestions.append(
                {
                    "type": "result",
                    "value": loser_label,
                    "label": loser_label,
                    "id": m.uuid,
                }
            )

    # Limit and return
    # When query is empty, return all suggestions (for preloading)
    # When query is provided, limit to 50 for performance
    if not query:
        return jsonify(suggestions)
    else:
        return jsonify(suggestions[:50])


@bp.route("/<tournament_url>/validate-dsl", methods=["POST"])
def validate_dsl(tournament_url):
    """Validate and simplify a DSL expression.
    Returns JSON with: valid (bool), value (the full interpreted value), simplified (str representation), error (str or None)
    """
    from flask import jsonify
    from app.utils.parser import (
        get_parser,
        DSLValidationError,
        Team,
        Match,
        SymbolicTeam,
        SymbolicMatch,
        Lambda,
    )

    def serialize_value(value):
        """Convert the interpreted value to a JSON-serializable format."""
        if isinstance(value, (int, bool, type(None))):
            return value
        elif isinstance(value, list):
            # Recursively serialize list elements
            return [serialize_value(item) for item in value]
        elif isinstance(value, Team):
            # Return team ID
            return {"type": "team", "id": value.obj.id}
        elif isinstance(value, Match):
            # Return match name
            return {"type": "match", "name": value.obj.name}
        elif isinstance(value, SymbolicTeam):
            # Return symbolic representation
            return {"type": "symbolic_team", "literal": value.literal}
        elif isinstance(value, SymbolicMatch):
            # Return symbolic representation
            return {"type": "symbolic_match", "literal": value.literal}
        elif isinstance(value, Lambda):
            # Lambda objects shouldn't appear in final results, but handle gracefully
            return {"type": "lambda", "params": value.params}
        else:
            # Fallback to string representation
            return str(value)

    def value_to_string(value):
        """Convert the interpreted value to a readable string representation."""
        if isinstance(value, (int, bool, type(None))):
            return str(value)
        elif isinstance(value, list):
            # Format as Lisp-like expression
            if len(value) > 0 and isinstance(value[0], str):
                # Preserved expression - format as s-expression
                return "(" + " ".join(value_to_string(item) for item in value) + ")"
            else:
                # Data list
                return "[" + ", ".join(value_to_string(item) for item in value) + "]"
        elif isinstance(value, Team):
            return f"[{value.obj.id}]"
        elif isinstance(value, Match):
            return f"{{{value.obj.name}}}"
        elif isinstance(value, SymbolicTeam):
            return f"[{value.literal}]"
        elif isinstance(value, SymbolicMatch):
            return f"{{{value.literal}}}"
        elif isinstance(value, Lambda):
            # Lambda objects shouldn't appear in final results, but handle gracefully
            params_str = " ".join(value.params) if value.params else ""
            return f"(lambda ({params_str}) ...)"
        else:
            return str(value)

    data = request.get_json()
    expression = data.get("expression", "").strip()

    if not expression:
        return jsonify(
            {"valid": True, "value": None, "simplified": None, "error": None}
        )

    try:
        parser = get_parser(tournament_url)
        result = parser.parse(expression)

        # Serialize the full value for JSON response
        serialized_value = serialize_value(result)

        # Create string representation
        simplified_str = value_to_string(result)

        # Only include simplified if it's different from the input
        simplified = simplified_str if simplified_str != expression else None

        return jsonify(
            {
                "valid": True,
                "value": serialized_value,
                "simplified": simplified,
                "error": None,
            }
        )
    except DSLValidationError as e:
        return jsonify(
            {"valid": False, "value": None, "simplified": None, "error": str(e)}
        )
    except Exception as e:
        return jsonify(
            {
                "valid": False,
                "value": None,
                "simplified": None,
                "error": f"Parse error: {str(e)}",
            }
        )


@bp.route("/<tournament_url>/delete", methods=["POST"])
@login_required
def delete_tournament(tournament_url):
    """Delete a tournament and all related data."""
    if is_not_TO(tournament_url):
        return (
            jsonify(
                {
                    "success": False,
                    "error": "Only tournament organizers can access this page",
                }
            ),
            403,
        )

    tournament = Tournament.query.filter_by(url=tournament_url).first_or_404()

    # Verify confirmation URL slug
    confirm_url = request.form.get("confirm_url", "").strip()
    if confirm_url != tournament_url:
        return (
            jsonify(
                {
                    "success": False,
                    "error": "Confirmation URL does not match. Tournament not deleted.",
                }
            ),
            400,
        )

    # Import all necessary models
    from models import (
        Point,
        MatchNote,
        Match,
        HeadRef,
        PlayerRegistration,
        TeamRegistration,
        Field,
        Tag,
        SideComp,
        SideCompResult,
        PenaltyType,
    )

    # Delete in order to respect foreign key constraints.
    # Order: side comp results -> side comps; points & match notes -> matches;
    # then penalty types, head refs, registrations, TOs, fields, tags; finally tournament.

    side_comps = SideComp.query.filter_by(event=tournament_url).all()
    side_comp_ids = [sc.id for sc in side_comps]
    if side_comp_ids:
        SideCompResult.query.filter(SideCompResult.comp.in_(side_comp_ids)).delete(
            synchronize_session=False
        )

    SideComp.query.filter_by(event=tournament_url).delete(synchronize_session=False)

    matches = Match.query.filter_by(event=tournament_url).all()
    match_uuids = [m.uuid for m in matches]
    if match_uuids:
        Point.query.filter(Point.match.in_(match_uuids)).delete(
            synchronize_session=False
        )
        MatchNote.query.filter(MatchNote.match.in_(match_uuids)).delete(
            synchronize_session=False
        )
    Match.query.filter_by(event=tournament_url).delete(synchronize_session=False)
    # PenaltyType after MatchNote (notes reference penalty_type_id)
    # Only delete event-level penalty types; league events use league's penalty types
    if not tournament.league_id:
        PenaltyType.query.filter_by(event=tournament_url).delete(
            synchronize_session=False
        )
    HeadRef.query.filter_by(event=tournament_url).delete(synchronize_session=False)
    PlayerRegistration.query.filter_by(event=tournament_url).delete(
        synchronize_session=False
    )
    TeamRegistration.query.filter_by(event=tournament_url).delete(
        synchronize_session=False
    )
    Field.query.filter_by(event=tournament_url).delete(synchronize_session=False)
    Tag.query.filter_by(event=tournament_url).delete(synchronize_session=False)
    TO.query.filter_by(event=tournament_url).delete(synchronize_session=False)
    rc_id = tournament.registrable_config_id if not tournament.league_id else None
    db.session.delete(tournament)
    if rc_id:
        from models import RegistrableConfig

        rc = RegistrableConfig.query.get(rc_id)
        if rc:
            db.session.delete(rc)
    db.session.commit()

    return (
        jsonify(
            {
                "success": True,
                "message": f'Tournament "{tournament.name}" has been permanently deleted.',
            }
        ),
        200,
    )


@bp.route("/<tournament_url>/add-to", methods=["POST"])
@login_required
def add_to(tournament_url):
    """Add a TO to the tournament."""

    tournament = Tournament.query.filter_by(url=tournament_url).first_or_404()
    if tournament.league_id:
        return (
            jsonify(
                {
                    "success": False,
                    "error": "TOs for league events are managed from the league page.",
                }
            ),
            403,
        )

    if is_not_TO(tournament_url):
        return (
            jsonify(
                {
                    "success": False,
                    "error": "Only tournament organizers can access this page",
                }
            ),
            403,
        )

    user_id = request.form.get("user_id", "").strip()
    user_type = request.form.get("user_type", "").strip().lower()

    if not user_id or user_type not in ["player", "team"]:
        return jsonify({"success": False, "error": "Invalid user ID or type"}), 400

    # Verify the user exists
    from models import Player, Team

    if user_type == "player":
        user = Player.query.get(user_id)
        if not user:
            return (
                jsonify(
                    {"success": False, "error": f'Player with ID "{user_id}" not found'}
                ),
                404,
            )
    else:  # team
        user = Team.query.get(user_id)
        if not user:
            return (
                jsonify(
                    {"success": False, "error": f'Team with ID "{user_id}" not found'}
                ),
                404,
            )

    # Check if TO already exists
    existing_to = TO.query.filter_by(
        user_id=user_id, user_type=user_type, event=tournament_url
    ).first()

    if existing_to:
        return (
            jsonify(
                {
                    "success": False,
                    "error": "This user is already a TO for this tournament",
                }
            ),
            400,
        )

    # Create new TO entry
    new_to = TO(user_id=user_id, user_type=user_type, event=tournament_url)
    db.session.add(new_to)
    db.session.commit()

    user_name = user.name if user else user_id
    return (
        jsonify(
            {"success": True, "message": f"Successfully added {user_name} as a TO"}
        ),
        200,
    )


@bp.route("/<tournament_url>/remove-to", methods=["POST"])
@login_required
def remove_to(tournament_url):
    """Remove a TO from the tournament."""

    tournament = Tournament.query.filter_by(url=tournament_url).first_or_404()
    if tournament.league_id:
        return (
            jsonify(
                {
                    "success": False,
                    "error": "TOs for league events are managed from the league page.",
                }
            ),
            403,
        )

    if is_not_TO(tournament_url):
        return (
            jsonify(
                {
                    "success": False,
                    "error": "Only tournament organizers can access this page",
                }
            ),
            403,
        )

    to_id = request.form.get("to_id")
    if not to_id:
        return jsonify({"success": False, "error": "TO ID is required"}), 400

    # Get the TO entry to remove
    to_to_remove = TO.query.get_or_404(to_id)

    # Verify it's for this tournament
    if to_to_remove.event != tournament_url:
        return jsonify({"success": False, "error": "Invalid TO entry"}), 400

    # Prevent removing yourself (optional - you might want to allow this)
    if (
        to_to_remove.user_id == current_user.id
        and to_to_remove.user_type == current_user.__class__.__name__.lower()
    ):
        return (
            jsonify({"success": False, "error": "You cannot remove yourself as a TO"}),
            400,
        )

    # Get user info for flash message
    from models import Player, Team

    if to_to_remove.user_type == "player":
        user = Player.query.get(to_to_remove.user_id)
    else:
        user = Team.query.get(to_to_remove.user_id)
    user_name = user.name if user else to_to_remove.user_id

    # Delete the TO entry
    db.session.delete(to_to_remove)
    db.session.commit()

    return (
        jsonify(
            {"success": True, "message": f"Successfully removed {user_name} as a TO"}
        ),
        200,
    )
