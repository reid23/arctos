"""
Match operation routes (start, run, finalize, view).
"""

from flask import Blueprint, request, jsonify
from flask_login import login_required, current_user
from datetime import datetime, timezone
import json
from models import (
    Match,
    Tournament,
    Point,
    PlayerRegistration,
    Player,
    Field,
    PenaltyType,
    db,
)
from app.filters import is_head_ref
from app.utils.helpers import can_head_ref_match
from app.utils.dependencies import apply_match_dependencies
from app.utils.scheduling import recompute_all_match_times
from app.serializers.match_note_serializer import MatchNoteSerializer
from app.utils.player_helpers import get_player_display_from_registration
from app.utils.responses import json_error, json_success
from app.utils.datetime_helpers import to_iso_z
from app.error_values import Ok, Err
from app.utils.result_helpers import json_from_result, public_error_message
from app.utils.field_refs import resolve_match_field_obj, sync_match_field_ref
from app.utils.match_1nf import (
    read_match_roster,
    read_match_stream_starts,
    replace_match_stream_starts,
)
from app.domain.enums import RegistrationStatus, MatchStatus, ScheduleType, SetType

bp = Blueprint("matches", __name__, url_prefix="/_api")


@bp.route("/scoreboard")
def scoreboard():
    """Scoreboard page for OBS overlay. Public endpoint."""
    from flask import make_response

    tournament_url = request.args.get("tournament")
    field_name = request.args.get("field")

    if not tournament_url or not field_name:
        return (
            render_template(
                "scoreboard.html", error="Tournament and field parameters required"
            ),
            400,
        )

    # Find the active match on this field (only IN_PROGRESS)
    match = Match.query.filter_by(
        event=tournament_url, field=field_name, status=MatchStatus.IN_PROGRESS
    ).first()

    # Get team information helper
    from models import Team, TeamRegistration

    def get_team_info(m):
        if not m:
            return None, None, None, None
        team1_obj = Team.query.get(m.team1) if m.team1 else None
        team2_obj = Team.query.get(m.team2) if m.team2 else None

        # Get team names - prefer initial (for dynamic teams), then registration pseudonym, then team name
        # Handle empty strings and missing registration (e.g. dynamic/unregistered team)
        reg1 = (
            TeamRegistration.query.filter_by(event=tournament_url, team=m.team1).first()
            if m.team1
            else None
        )
        team1_name = (
            (
                reg1.pseudonym
                if reg1 and reg1.pseudonym
                else (team1_obj.name if team1_obj else m.team1_initial)
            )
            if m.team1
            else m.team1_initial
        )
        reg2 = (
            TeamRegistration.query.filter_by(event=tournament_url, team=m.team2).first()
            if m.team2
            else None
        )
        team2_name = (
            (
                reg2.pseudonym
                if reg2 and reg2.pseudonym
                else (team2_obj.name if team2_obj else m.team2_initial)
            )
            if m.team2
            else m.team2_initial
        )

        # Only include photos if there's an actual team object with a photo (not dynamic teams)
        team1_photo = (
            team1_obj.profile_photo
            if (team1_obj and team1_obj.profile_photo and m.team1)
            else None
        )
        team2_photo = (
            team2_obj.profile_photo
            if (team2_obj and team2_obj.profile_photo and m.team2)
            else None
        )
        return team1_name, team2_name, team1_photo, team2_photo

    # If there's an active match, show it
    if match:
        team1_name, team2_name, team1_photo, team2_photo = get_team_info(match)

        # Get points and calculate scores by set
        points = Point.query.filter_by(match=match.uuid).order_by(Point.stamp).all()

        # Calculate scores by set
        sets = sorted(set(p.set_number for p in points if p.set_number))
        scores_by_set = {}
        for set_num in sets:
            set_points = [
                p for p in points if p.set_number == set_num and not p.rerolled
            ]
            scores_by_set[set_num] = {
                "team1_score": sum(1 for p in set_points if p.winner == "TEAM1"),
                "team2_score": sum(1 for p in set_points if p.winner == "TEAM2"),
            }

        # For STONES matches, get stones info
        stones_info = None
        if match.set_type == "STONES":
            stones_info = {
                "stones_per_set": match.stones_per_set or 100,
                "stones_remaining": match.stones_remaining,
            }

        response = make_response(
            render_template(
                "scoreboard.html",
                match=match,
                team1_name=team1_name,
                team2_name=team2_name,
                team1_photo=team1_photo,
                team2_photo=team2_photo,
                scores_by_set=scores_by_set,
                sets=sets,
                stones_info=stones_info,
                tournament_url=tournament_url,
                field_name=field_name,
                show_between_matches=False,
            )
        )
        # Cache the HTML page for 1 second (short cache since it updates frequently)
        response.cache_control.max_age = 1
        return response

    # No active match - find previous and next matches
    # Get all matches on this field, ordered by time
    all_field_matches = (
        Match.query.filter_by(event=tournament_url, field=field_name)
        .order_by(Match.nominal_start_time.asc(), Match.completed_time.asc())
        .all()
    )

    # Find most recent completed or skipped match (previous) - skip BREAK/JOIN matches
    prev_match = None
    for m in reversed(all_field_matches):
        if (
            m.status in (MatchStatus.COMPLETED, MatchStatus.SKIPPED)
            and m.completed_time
            and m.schedule_type not in (ScheduleType.BREAK, ScheduleType.JOIN)
        ):
            prev_match = m
            break

    # Find next match (not started or ready to start) - skip BREAK/JOIN matches
    next_match = None
    for m in all_field_matches:
        if m.schedule_type not in (ScheduleType.BREAK, ScheduleType.JOIN) and (
            m.status in (MatchStatus.NOT_STARTED, MatchStatus.IN_PROGRESS)
            or (
                m.status in (MatchStatus.COMPLETED, MatchStatus.SKIPPED)
                and not m.completed_time
            )
        ):
            next_match = m
            break

    # If no matches found at all
    if not prev_match and not next_match:
        response = make_response(
            render_template(
                "scoreboard.html",
                error="No match found on this field",
                tournament_url=tournament_url,
                field_name=field_name,
            ),
            404,
        )
        response.cache_control.max_age = 1
        return response

    # Get team info for previous and next matches
    if prev_match:
        prev_team1_name, prev_team2_name, prev_team1_photo, prev_team2_photo = (
            get_team_info(prev_match)
        )
        # Ensure we always have names (fallback if somehow None)
        prev_team1_name = prev_team1_name or "Team 1"
        prev_team2_name = prev_team2_name or "Team 2"
    else:
        prev_team1_name, prev_team2_name, prev_team1_photo, prev_team2_photo = (
            None,
            None,
            None,
            None,
        )

    if next_match:
        next_team1_name, next_team2_name, next_team1_photo, next_team2_photo = (
            get_team_info(next_match)
        )
        # Ensure we always have names (fallback if somehow None)
        next_team1_name = next_team1_name or "Team 1"
        next_team2_name = next_team2_name or "Team 2"
    else:
        next_team1_name, next_team2_name, next_team1_photo, next_team2_photo = (
            None,
            None,
            None,
            None,
        )

    # Determine winner for previous match
    prev_winner = None
    if prev_match and prev_match.match_winner:
        prev_winner = prev_match.match_winner

    response = make_response(
        render_template(
            "scoreboard.html",
            match=None,
            show_between_matches=True,
            prev_match=prev_match,
            prev_team1_name=prev_team1_name,
            prev_team2_name=prev_team2_name,
            prev_team1_photo=prev_team1_photo,
            prev_team2_photo=prev_team2_photo,
            prev_winner=prev_winner,
            next_match=next_match,
            next_team1_name=next_team1_name,
            next_team2_name=next_team2_name,
            next_team1_photo=next_team1_photo,
            next_team2_photo=next_team2_photo,
            tournament_url=tournament_url,
            field_name=field_name,
        )
    )
    response.cache_control.max_age = 1
    return response


@bp.route("/scoreboard-state")
def scoreboard_state():
    """Get scoreboard state as JSON for polling. Public endpoint."""
    tournament_url = request.args.get("tournament")
    field_name = request.args.get("field")

    if not tournament_url or not field_name:
        return jsonify({"error": "Tournament and field parameters required"}), 400

    # Find the active match on this field (only IN_PROGRESS)
    match = Match.query.filter_by(
        event=tournament_url, field=field_name, status=MatchStatus.IN_PROGRESS
    ).first()

    # Get team information helper
    from models import Team, TeamRegistration

    def get_team_info(m):
        if not m:
            return None, None, None, None
        team1_obj = Team.query.get(m.team1) if m.team1 else None
        team2_obj = Team.query.get(m.team2) if m.team2 else None

        # Get team names - prefer initial (for dynamic teams), then registration pseudonym, then team name
        # Handle empty strings and missing registration (e.g. dynamic/unregistered team)
        reg1 = (
            TeamRegistration.query.filter_by(event=tournament_url, team=m.team1).first()
            if m.team1
            else None
        )
        team1_name = (
            (
                reg1.pseudonym
                if reg1 and reg1.pseudonym
                else (team1_obj.name if team1_obj else m.team1_initial)
            )
            if m.team1
            else m.team1_initial
        )
        reg2 = (
            TeamRegistration.query.filter_by(event=tournament_url, team=m.team2).first()
            if m.team2
            else None
        )
        team2_name = (
            (
                reg2.pseudonym
                if reg2 and reg2.pseudonym
                else (team2_obj.name if team2_obj else m.team2_initial)
            )
            if m.team2
            else m.team2_initial
        )

        # Only include photos if there's an actual team object with a photo (not dynamic teams)
        team1_photo = (
            team1_obj.profile_photo
            if (team1_obj and team1_obj.profile_photo and m.team1)
            else None
        )
        team2_photo = (
            team2_obj.profile_photo
            if (team2_obj and team2_obj.profile_photo and m.team2)
            else None
        )
        return team1_name, team2_name, team1_photo, team2_photo

    # If there's an active match, return match state
    if match:
        team1_name, team2_name, team1_photo, team2_photo = get_team_info(match)

        # Get points and calculate scores by set
        points = Point.query.filter_by(match=match.uuid).order_by(Point.stamp).all()

        # Calculate scores by set
        sets = sorted(set(p.set_number for p in points if p.set_number))
        scores_by_set = {}
        for set_num in sets:
            set_points = [
                p for p in points if p.set_number == set_num and not p.rerolled
            ]
            scores_by_set[set_num] = {
                "team1_score": sum(1 for p in set_points if p.winner == "TEAM1"),
                "team2_score": sum(1 for p in set_points if p.winner == "TEAM2"),
            }

        # For STONES matches, get stones info and points for live stones during a point
        stones_info = None
        points_for_stones = None
        if match.set_type == "STONES":
            stones_info = {
                "stones_per_set": match.stones_per_set or 100,
                "stones_remaining": match.stones_remaining,
            }

            def _iso_z(dt):
                if dt is None:
                    return None
                s = dt.isoformat()
                if dt.tzinfo is None and not s.endswith("Z"):
                    s = s + "Z"
                return s

            points_for_stones = [
                {
                    "stamp": _iso_z(p.stamp),
                    "end_stamp": _iso_z(p.end_stamp),
                    "stones_at_start": p.stones_at_start,
                }
                for p in points
            ]

        return jsonify(
            {
                "has_active_match": True,
                "match_id": match.uuid,
                "team1_name": team1_name,
                "team2_name": team2_name,
                "team1_photo": team1_photo,
                "team2_photo": team2_photo,
                "scores_by_set": scores_by_set,
                "sets": sets,
                "stones_info": stones_info,
                "points_for_stones": points_for_stones,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )

    # No active match - find previous and next matches
    # Get all matches on this field, ordered by time
    all_field_matches = (
        Match.query.filter_by(event=tournament_url, field=field_name)
        .order_by(Match.nominal_start_time.asc(), Match.completed_time.asc())
        .all()
    )

    # Find most recent completed or skipped match (previous) - skip BREAK/JOIN matches
    prev_match = None
    for m in reversed(all_field_matches):
        if (
            m.status in (MatchStatus.COMPLETED, MatchStatus.SKIPPED)
            and m.completed_time
            and m.schedule_type not in (ScheduleType.BREAK, ScheduleType.JOIN)
        ):
            prev_match = m
            break

    # Find next match (not started or ready to start) - skip BREAK/JOIN matches
    next_match = None
    for m in all_field_matches:
        if m.schedule_type not in (ScheduleType.BREAK, ScheduleType.JOIN) and (
            m.status in (MatchStatus.NOT_STARTED, MatchStatus.IN_PROGRESS)
            or (
                m.status in (MatchStatus.COMPLETED, MatchStatus.SKIPPED)
                and not m.completed_time
            )
        ):
            next_match = m
            break

    # Get team info for previous and next matches
    prev_data = None
    if prev_match:
        prev_team1_name, prev_team2_name, prev_team1_photo, prev_team2_photo = (
            get_team_info(prev_match)
        )
        prev_team1_name = prev_team1_name or "Team 1"
        prev_team2_name = prev_team2_name or "Team 2"
        prev_data = {
            "team1_name": prev_team1_name,
            "team2_name": prev_team2_name,
            "team1_photo": prev_team1_photo,
            "team2_photo": prev_team2_photo,
            "winner": prev_match.match_winner,
        }

    next_data = None
    if next_match:
        next_team1_name, next_team2_name, next_team1_photo, next_team2_photo = (
            get_team_info(next_match)
        )
        next_team1_name = next_team1_name or "Team 1"
        next_team2_name = next_team2_name or "Team 2"
        next_data = {
            "team1_name": next_team1_name,
            "team2_name": next_team2_name,
            "team1_photo": next_team1_photo,
            "team2_photo": next_team2_photo,
        }

    return jsonify(
        {
            "has_active_match": False,
            "prev_match": prev_data,
            "next_match": next_data,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    )


@bp.route("/<tournament_url>/match")
def match_page(tournament_url: str):
    """Return full match data for the match-view SPA page.

    ``GET /_api/<tournament_url>/match?id=<uuid>`` or
    ``GET /_api/<tournament_url>/match?name=<name>``

    Returns match details, scored points, notes (for head refs), penalty
    types, field camera info, and footage links.

    Args:
        tournament_url: Tournament URL slug from the path.

    Query Args:
        id (str): Match UUID.
        name (str): Match name (alternative to *id*).

    Returns:
        JSON match detail object, or error with HTTP 400/403/404.
    """
    match_id = request.args.get("id")
    match_name = request.args.get("name")

    if not match_id and not match_name:
        return jsonify({"success": False, "error": "Match ID or name required"}), 400

    has_access, tournament = check_tournament_access(tournament_url)
    if not has_access or not tournament:
        return jsonify({"success": False, "error": "Access denied"}), 403

    if match_id:
        match = Match.query.filter_by(
            uuid=match_id, event=tournament_url
        ).first_or_404()
    else:
        match = Match.query.filter_by(
            name=match_name, event=tournament_url
        ).first_or_404()

    points = Point.query.filter_by(match=match.uuid).order_by(Point.stamp).all()

    from app.utils.user_helpers import is_player
    from app.services.permission_service import PermissionService

    is_head_ref_flag = (
        can_head_ref_match(tournament_url, current_user.id, match=match)
        if current_user.is_authenticated and is_player(current_user)
        else False
    )

    is_to = (
        PermissionService.is_tournament_organizer(tournament_url, current_user)
        if current_user.is_authenticated
        else False
    )

    # Get match notes and point notes
    match_notes = []
    point_notes_map = {}
    from models import MatchNote
    from app.utils.player_helpers import get_player_display_name

    # Get match-level notes (point_id is None) - only for head refs
    if is_head_ref_flag:
        notes = (
            MatchNote.query.filter_by(match=match.uuid, point_id=None)
            .order_by(MatchNote.created_at.desc())
            .all()
        )
        for note in notes:
            player_name = None
            player_display = None
            if note.player_id:
                player_name, player_display = get_player_display_name(
                    note.player_id, tournament_url
                )
            # Determine team_id if target is TEAM1 or TEAM2
            team_id = None
            if note.target == "team1":
                team_id = match.team1
            elif note.target == "team2":
                team_id = match.team2

            match_notes.append(
                {
                    "text": note.text,
                    "target": note.target,
                    "player_id": note.player_id,
                    "player_name": player_name,
                    "player_display": player_display,
                    "team_id": team_id,
                    "created_at": (
                        note.created_at.isoformat() if note.created_at else None
                    ),
                }
            )

    # Get point-specific notes - point notes (target='match') visible to everyone
    # Team and player notes only visible to head refs
    if points:
        point_ids = [p.uuid for p in points if getattr(p, "uuid", None)]
        if point_ids:
            point_notes_query = (
                MatchNote.query.filter_by(match=match.uuid)
                .filter(MatchNote.point_id.in_(point_ids))
                .order_by(MatchNote.created_at.asc())
            )

            # Filter for non-head-refs: only show 'match' target notes
            if not is_head_ref_flag:
                point_notes_query = point_notes_query.filter_by(target="match")

            point_notes = point_notes_query.all()
            for n in point_notes:
                # Filter: only show point notes (target='match') to everyone
                # Team and player notes are only visible to head refs
                if not is_head_ref_flag and n.target != "match":
                    continue

                player_name = None
                player_display = None
                if n.player_id:
                    player_name, player_display = get_player_display_name(
                        n.player_id, tournament_url
                    )

                # Determine team_id if target is TEAM1 or TEAM2
                team_id = None
                if n.target == "team1":
                    team_id = match.team1
                elif n.target == "team2":
                    team_id = match.team2

                point_notes_map.setdefault(n.point_id, []).append(
                    {
                        "text": n.text,
                        "target": n.target,
                        "player_id": n.player_id,
                        "player_name": player_name,
                        "player_display": player_display,
                        "team_id": team_id,
                        "created_at": (
                            n.created_at.isoformat()
                            if getattr(n, "created_at", None)
                            else None
                        ),
                    }
                )

    # Compute end time for display
    computed_end_time = None
    actual_end_time = match.completed_time
    try:
        if match.nominal_length:
            base_start = match.confirmed_start_time or match.nominal_start_time
            if base_start:
                from datetime import timedelta

                computed_end_time = base_start + timedelta(minutes=match.nominal_length)
    except Exception:
        computed_end_time = None

    # Get all camera URLs and filter to only those active during the match
    camera_url = None
    available_cameras = (
        []
    )  # List of dicts: {index, url, stream_start_time, type, video_path, camera_id, session_id}

    from app.utils.camera_helpers import parse_camera_urls
    from datetime import datetime, timezone
    import json
    import os
    from flask import current_app

    # Get stream start times and recorded videos from match (check even if no field cameras)
    stream_starts = {}
    recorded_videos = []  # List of recorded video sessions
    camera_urls = []

    if match.camera_stream_starts:
        try:
            stream_starts_data = json.loads(match.camera_stream_starts)

            # Parse the new format: camera_id -> recording info (single or list)
            for camera_id, recording_data in stream_starts_data.items():
                # Handle both single recording and list of recordings
                recordings = (
                    recording_data
                    if isinstance(recording_data, list)
                    else [recording_data]
                )

                for recording in recordings:
                    # Check if this is a recorded video (has video_path)
                    if isinstance(recording, dict) and "video_path" in recording:
                        video_path = recording.get("video_path", "")

                        if video_path:
                            # S3: video_path is the object key; resolve to presigned URL
                            if recording.get("storage") == "s3":
                                bucket = current_app.config.get("S3_VIDEO_BUCKET")
                                if bucket:
                                    from app.utils.s3_video import get_presigned_url

                                    region = (
                                        current_app.config.get("AWS_REGION")
                                        or "us-east-1"
                                    )
                                    expiry = current_app.config.get(
                                        "S3_PRESIGNED_EXPIRY_SECONDS", 3600
                                    )
                                    endpoint_url = current_app.config.get(
                                        "S3_ENDPOINT_URL"
                                    )
                                    playable_url = get_presigned_url(
                                        bucket,
                                        video_path,
                                        region=region,
                                        expiry_seconds=expiry,
                                        endpoint_url=endpoint_url,
                                    )
                                    if playable_url:
                                        recorded_videos.append(
                                            {
                                                "camera_id": camera_id,
                                                "video_path": playable_url,
                                                "point_timestamps": recording.get(
                                                    "point_timestamps"
                                                ),
                                                "type": "recorded",
                                            }
                                        )
                            else:
                                # Local: resolve to full path and check file exists
                                if video_path.startswith("static/"):
                                    video_full_path = os.path.join(
                                        current_app.root_path, "..", video_path
                                    )
                                else:
                                    video_full_path = os.path.join(
                                        current_app.root_path, "../static", video_path
                                    )

                                if os.path.exists(video_full_path):
                                    recorded_videos.append(
                                        {
                                            "camera_id": camera_id,
                                            "video_path": video_path,  # Keep relative path for URL
                                            "point_timestamps": recording.get(
                                                "point_timestamps"
                                            ),
                                            "type": "recorded",
                                        }
                                    )

                    # Also handle old format (just stream start time string)
                    elif isinstance(recording, str) or (
                        isinstance(recording, dict)
                        and "start_time" in recording
                        and "video_path" not in recording
                    ):
                        # This is the old format, skip for now (handled below)
                        pass
        except (json.JSONDecodeError, TypeError) as e:
            print(f"Error parsing camera_stream_starts: {e}")
            # Try old format
            try:
                # Old format: index -> stream_start_time string
                stream_starts = (
                    stream_starts_data if isinstance(stream_starts_data, dict) else {}
                )
            except:
                stream_starts = {}

    # Get YouTube cameras from field configuration (if field exists)
    sync_match_field_ref(tournament_url, match)
    field_obj = resolve_match_field_obj(tournament_url, match)
    if field_obj and field_obj.camera:
        camera_urls = parse_camera_urls(field_obj.camera)

        # Include YouTube cameras from field configuration
        if camera_urls:
            for idx, url in enumerate(camera_urls):
                stream_start_str = stream_starts.get(str(idx))  # JSON keys are strings

                # For old format compatibility
                if not stream_start_str and isinstance(stream_starts, dict):
                    stream_start_str = stream_starts.get(str(idx))

                available_cameras.append(
                    {
                        "index": idx,
                        "url": url,
                        "stream_start_time": (
                            stream_start_str if stream_start_str else None
                        ),
                        "type": "youtube",
                    }
                )

    # Add recorded videos whenever we have them (match may be in progress, completed, or not yet started)
    if recorded_videos:
        # Add recorded videos with unique indices (starting after YouTube cameras)
        for idx, recording in enumerate(recorded_videos):
            available_cameras.append(
                {
                    "index": len(camera_urls)
                    + idx,  # Continue indexing after YouTube cameras
                    "url": None,  # No YouTube URL for recorded videos
                    "stream_start_time": recording.get("start_time")
                    or (
                        datetime.fromtimestamp(
                            int(recording.get("start_timestamp")) / 1000
                        ).isoformat()
                        + "Z"
                        if recording.get("start_timestamp")
                        else None
                    ),
                    "type": "recorded",
                    "video_path": recording["video_path"],
                    "camera_id": recording.get("camera_id", "unknown"),
                    "session_id": recording.get("session_id", ""),
                    "point_timestamps": recording.get("point_timestamps"),
                }
            )

    # Use first available camera for backward compatibility
    if available_cameras:
        first_cam = available_cameras[0]
        if first_cam.get("type") == "youtube":
            camera_url = first_cam["url"]

    # Debug: log camera availability
    if not available_cameras and field_obj and field_obj.camera:
        print(
            f"Warning: No cameras available for match {match.uuid} on field {match.field}. Field has {len(camera_urls)} camera(s). Match status: {match.status}"
        )

    return render_template(
        "match_page.html",
        tournament=tournament,
        match=match,
        points=points,
        is_head_ref=is_head_ref_flag,
        is_to=is_to,
        computed_end_time=computed_end_time,
        actual_end_time=actual_end_time,
        match_notes=match_notes,
        point_notes_map=point_notes_map,
        camera_url=camera_url,
        available_cameras=available_cameras,
    )


@bp.route("/<tournament_url>/start-match")
@login_required
def start_match(tournament_url: str):
    """Return setup data needed before starting a match.

    ``GET /_api/<tournament_url>/start-match?id=<uuid>``

    Checks eligibility via
    :func:`~app.services.match_start_eligibility.get_can_start_and_reasons`
    and, if the match can be started, returns roster lists, injury records,
    penalty types, and field/camera information.

    Args:
        tournament_url: Tournament URL slug from the path.

    Query Args:
        id (str): UUID of the match to prepare.

    Returns:
        JSON object with match setup data, or error with HTTP 400/404.
    """
    match_id = request.args.get("id")
    if not match_id:
        return jsonify({"success": False, "error": "Match ID required"}), 400

    match = Match.query.get(match_id)
    if not match or match.event != tournament_url:
        return jsonify({"success": False, "error": "Match not found"}), 404

    from app.services.match_start_eligibility import get_can_start_and_reasons

    can_start, block_reasons, _ = get_can_start_and_reasons(
        tournament_url, match, current_user
    )
    if not can_start:
        error_msg = block_reasons[0] if block_reasons else "Cannot start this match."
        return (
            jsonify({"success": False, "error": error_msg, "reasons": block_reasons}),
            400,
        )

    tournament = Tournament.query.get(tournament_url)

    team1_players = (
        db.session.query(PlayerRegistration, Player)
        .join(Player, PlayerRegistration.player == Player.id)
        .filter(
            PlayerRegistration.event == tournament_url,
            PlayerRegistration.team == match.team1,
            PlayerRegistration.status == RegistrationStatus.CONFIRMED,
        )
        .all()
    )

    team2_players = (
        db.session.query(PlayerRegistration, Player)
        .join(Player, PlayerRegistration.player == Player.id)
        .filter(
            PlayerRegistration.event == tournament_url,
            PlayerRegistration.team == match.team2,
            PlayerRegistration.status == RegistrationStatus.CONFIRMED,
        )
        .all()
    )

    all_players = (
        db.session.query(PlayerRegistration, Player)
        .join(Player, PlayerRegistration.player == Player.id)
        .filter(
            PlayerRegistration.event == tournament_url,
            PlayerRegistration.status == RegistrationStatus.CONFIRMED,
        )
        .all()
    )

    from models import Injury

    injuries_map = {}
    try:
        all_player_ids = set(
            [pr.player for pr, _ in all_players]
            + [pr.player for pr, _ in team1_players]
            + [pr.player for pr, _ in team2_players]
        )
        if all_player_ids:
            active_injuries = Injury.query.filter(
                Injury.player.in_(list(all_player_ids)), Injury.active.is_(True)
            ).all()
            for inj in active_injuries:
                injuries_map.setdefault(inj.player, []).append(inj.message)
    except Exception:
        injuries_map = {}

    return render_template(
        "start_match.html",
        tournament=tournament,
        match=match,
        team1_players=team1_players,
        team2_players=team2_players,
        all_players=all_players,
        injuries_map=injuries_map,
    )


@bp.route("/<tournament_url>/get-selection-notes")
@login_required
def get_selection_notes(tournament_url):
    """Get notes relevant to team and selected players. For league events, includes notes from all matches in the league."""
    match_id = request.args.get("match_id")
    team_side = request.args.get("team")
    player_ids_csv = request.args.get("player_ids", "")

    if not match_id or team_side not in ("team1", "team2"):
        return json_error("match_id and team required")

    tournament = Tournament.query.filter_by(url=tournament_url).first()
    if not tournament:
        return json_error("Tournament not found")
    from app.utils.helpers import match_event_urls_for_penalties

    event_urls = match_event_urls_for_penalties(tournament)

    match = Match.query.get(match_id)
    if not match or match.event not in event_urls:
        return json_error("Match not found")

    if not can_head_ref_match(tournament_url, current_user.id, match=match):
        return json_error("bruh ur not a head ref")

    team_id = match.team1 if team_side == "team1" else match.team2
    if not team_id:
        return json_success({"notes": []})

    selected_player_ids = [
        pid.strip() for pid in player_ids_csv.split(",") if pid.strip()
    ]

    team1_matches = Match.query.filter(
        Match.event.in_(event_urls), Match.team1 == team_id
    ).all()
    team2_matches = Match.query.filter(
        Match.event.in_(event_urls), Match.team2 == team_id
    ).all()
    team1_match_ids = {m.uuid for m in team1_matches}
    team2_match_ids = {m.uuid for m in team2_matches}

    from models import MatchNote

    player_notes = []
    if selected_player_ids:
        # Include notes from matches in this event (or all league events)
        player_notes = (
            db.session.query(MatchNote)
            .join(Match, Match.uuid == MatchNote.match)
            .filter(
                Match.event.in_(event_urls),
                MatchNote.player_id.in_(selected_player_ids),
            )
            .all()
        )

    team_target_notes = (
        MatchNote.query.filter(
            MatchNote.match.in_(list(team1_match_ids | team2_match_ids))
        )
        .filter(MatchNote.target.in_(["team1", "team2"]))
        .all()
    )

    filtered_team_notes = []
    for n in team_target_notes:
        if n.match in team1_match_ids and (n.target == "team1"):
            filtered_team_notes.append(n)
        elif n.match in team2_match_ids and (n.target == "team2"):
            filtered_team_notes.append(n)

    all_notes = {}
    for n in player_notes + filtered_team_notes:
        all_notes[getattr(n, "uuid", id(n))] = n

    penalty_type_ids = {
        getattr(n, "penalty_type_id", None)
        for n in all_notes.values()
        if getattr(n, "penalty_type_id", None)
    }
    pt_map = {}
    if penalty_type_ids:
        for pt in PenaltyType.query.filter(PenaltyType.id.in_(penalty_type_ids)).all():
            pt_map[pt.id] = {
                "name": pt.name,
                "color": pt.color,
                "desc": pt.desc.strip() if pt.desc and pt.desc.strip() else None,
            }

    notes_data = []
    for n in all_notes.values():
        # Get match to determine team_id
        match_obj = Match.query.get(n.match) if n.match else None
        payload = MatchNoteSerializer.to_dict(n, tournament_url, match=match_obj)
        pt_id = payload.get("penalty_type_id")
        pt_info = pt_map.get(pt_id) if pt_id else None
        # Keep response schema stable for this endpoint (subset only).
        notes_data.append(
            {
                "text": payload.get("text"),
                "target": payload.get("target"),
                "player_id": payload.get("player_id"),
                "player_name": payload.get("player_name"),
                "player_display": payload.get("player_display"),
                "team_id": payload.get("team_id"),
                "penalty_type_id": pt_id,
                "penalty_type_name": pt_info["name"] if pt_info else None,
                "penalty_type_color": pt_info["color"] if pt_info else None,
                "penalty_type_desc": pt_info.get("desc") if pt_info else None,
            }
        )

    try:
        notes_data.sort(key=lambda x: x.get("created_at") or "", reverse=True)
    except Exception:
        pass

    return json_success({"notes": notes_data})


@bp.route("/<tournament_url>/start-match", methods=["POST"])
@login_required
def start_match_post(tournament_url):
    """Handle match start form submission."""
    from app.services.match_service import MatchService

    match_id = request.form.get("match_id")
    res = MatchService.start_match(
        tournament_url,
        match_id,
        current_user,
        team1_players_csv=request.form.get("team1_players", ""),
        team2_players_csv=request.form.get("team2_players", ""),
        match_notes=request.form.get("match_notes", ""),
        stones_per_set=request.form.get("stones_per_set"),
    )

    match res:
        case Ok(match_obj):
            return (
                jsonify({"success": True, "message": "Match started successfully!"}),
                200,
            )
        case Err(err):
            return jsonify({"success": False, "error": public_error_message(err)}), 400


@bp.route("/<tournament_url>/run-match")
@login_required
def run_match(tournament_url):
    """Match running page for head refs."""
    match_id = request.args.get("id")
    if not match_id:
        return jsonify({"success": False, "error": "Match ID required"}), 400

    match = Match.query.get(match_id)
    if not match or match.event != tournament_url:
        return jsonify({"success": False, "error": "Match not found"}), 404

    if match.status in (MatchStatus.COMPLETED, MatchStatus.SKIPPED):
        return (
            jsonify(
                {
                    "success": False,
                    "error": "This match has already been completed or skipped",
                }
            ),
            400,
        )

    if not can_head_ref_match(tournament_url, current_user.id, match=match):
        return (
            jsonify(
                {
                    "success": False,
                    "error": "You are not authorized to run matches for this tournament",
                }
            ),
            403,
        )

    tournament = Tournament.query.get(tournament_url)
    points = Point.query.filter_by(match=match.uuid).order_by(Point.stamp).all()

    team1_players = []
    team2_players = []
    for pid in read_match_roster(match, "team1"):
        pr = PlayerRegistration.query.filter_by(
            event=tournament_url,
            player=pid,
            status=RegistrationStatus.CONFIRMED,
        ).first()
        if pr:
            player = Player.query.get(pid)
            if player:
                team1_players.append((pr, player))

    for pid in read_match_roster(match, "team2"):
        pr = PlayerRegistration.query.filter_by(
            event=tournament_url,
            player=pid,
            status=RegistrationStatus.CONFIRMED,
        ).first()
        if pr:
            player = Player.query.get(pid)
            if player:
                team2_players.append((pr, player))

    # Build match_players for player autocomplete in notes modal
    match_players = []
    for pr, player in team1_players + team2_players:
        display = get_player_display_from_registration(player, pr)
        match_players.append(
            {"player_id": player.id, "name": player.name, "display": display}
        )

    return render_template(
        "run_match.html",
        tournament=tournament,
        match=match,
        points=points,
        team1_players=team1_players,
        team2_players=team2_players,
        match_players=match_players,
    )


@bp.route("/<tournament_url>/finalize-match")
@login_required
def finalize_match(tournament_url):
    """Match finalization page."""
    match_id = request.args.get("id")
    if not match_id:
        return jsonify({"success": False, "error": "Match ID required"}), 400

    match = Match.query.get(match_id)
    if not match or match.event != tournament_url:
        return jsonify({"success": False, "error": "Match not found"}), 404

    if match.status in (MatchStatus.COMPLETED, MatchStatus.SKIPPED):
        return (
            jsonify(
                {
                    "success": False,
                    "error": "This match has already been completed/skipped",
                }
            ),
            400,
        )

    if not can_head_ref_match(tournament_url, current_user.id, match=match):
        return (
            jsonify(
                {
                    "success": False,
                    "error": "You are not authorized to finalize matches for this tournament",
                }
            ),
            403,
        )

    tournament = Tournament.query.get(tournament_url)
    points = Point.query.filter_by(match=match.uuid).order_by(Point.stamp).all()

    from models import MatchNote

    point_notes_map = {}
    stones_elapsed_map = {}

    def compute_stones_elapsed(start_dt, end_dt):
        try:
            if not start_dt or not end_dt:
                return 0
            start_epoch = start_dt.timestamp()
            end_epoch = end_dt.timestamp()
            start_count = int(start_epoch // 1.5)
            end_count = int(end_epoch // 1.5)
            val = end_count - start_count
            return val if val >= 0 else 0
        except Exception:
            return 0

    if points:
        point_ids = [p.uuid for p in points if getattr(p, "uuid", None)]
        for p in points:
            stones_elapsed_map[p.uuid] = compute_stones_elapsed(
                getattr(p, "stamp", None), getattr(p, "end_stamp", None)
            )
        if point_ids:
            notes = (
                MatchNote.query.filter_by(match=match.uuid)
                .filter(MatchNote.point_id.in_(point_ids))
                .order_by(MatchNote.created_at.asc())
                .all()
            )
            for n in notes:
                payload = MatchNoteSerializer.to_dict(n, tournament_url, match=match)
                point_notes_map.setdefault(n.point_id, []).append(
                    {
                        "text": payload.get("text"),
                        "target": payload.get("target"),
                        "player_id": payload.get("player_id"),
                        "player_name": payload.get("player_name"),
                        "player_display": payload.get("player_display"),
                        "team_id": payload.get("team_id"),
                        "created_at": payload.get("created_at"),
                    }
                )

    team1_score = sum(1 for p in points if p.winner == "TEAM1" and not p.rerolled)
    team2_score = sum(1 for p in points if p.winner == "TEAM2" and not p.rerolled)

    return render_template(
        "finalize_match.html",
        tournament=tournament,
        match=match,
        points=points,
        point_notes_map=point_notes_map,
        stones_elapsed_map=stones_elapsed_map,
        team1_score=team1_score,
        team2_score=team2_score,
    )


@bp.route("/<tournament_url>/finalize-match", methods=["POST"])
@login_required
def finalize_match_post(tournament_url):
    """Handle match finalization."""
    match_id = request.form.get("match_id")
    if not match_id:
        return jsonify({"success": False, "error": "Match ID required"}), 400

    match = Match.query.get(match_id)
    if not match or match.event != tournament_url:
        return jsonify({"success": False, "error": "Match not found"}), 404

    if not can_head_ref_match(tournament_url, current_user.id, match=match):
        return (
            jsonify(
                {
                    "success": False,
                    "error": "You are not authorized to finalize matches for this tournament",
                }
            ),
            403,
        )

    match.status = MatchStatus.COMPLETED
    # Note: end_time may need to be added to Match model if not present

    match_winner = request.form.get("match_winner")
    if not match_winner:
        return jsonify({"success": False, "error": "Please select a match winner"}), 400

    # Record completion time on the match using UTC
    match.completed_time = datetime.now(timezone.utc).replace(tzinfo=None)
    match.finalized_by = current_user.id
    match.final_notes = request.form.get("final_notes", "")
    match.match_winner = match_winner
    match.finalized_at = datetime.now(timezone.utc).replace(tzinfo=None)

    # Refresh camera stream start times when match ends (in case streams started late)
    sync_match_field_ref(tournament_url, match)
    field_obj = resolve_match_field_obj(tournament_url, match)
    if field_obj and field_obj.camera:
        from app.utils.camera_helpers import get_all_camera_stream_starts

        stream_starts = get_all_camera_stream_starts(field_obj)
        if stream_starts:
            # Merge with existing stream starts (don't overwrite if already set)
            existing_starts = read_match_stream_starts(match)
            # Update with any new stream starts
            existing_starts.update(stream_starts)
            replace_match_stream_starts(match, existing_starts)

    team1_signature = request.form.get("team1_signature")
    team2_signature = request.form.get("team2_signature")
    if team1_signature:
        match.team1_signature = team1_signature
    if team2_signature:
        match.team2_signature = team2_signature
    db.session.commit()

    try:
        apply_match_dependencies(tournament_url, match)
    except Exception as e:
        print(f"Dependency update error for match {match.name}: {e}")

    # Recompute all match times (MatchGraph-based scheduler)
    try:
        from app.utils.scheduling import recompute_all_match_times

        recompute_all_match_times(tournament_url)
        db.session.commit()
    except Exception as e:
        print(f"Error recomputing match times: {e}")

    return jsonify({"success": True, "message": "Match finalized successfully!"}), 200


@bp.route("/<tournament_url>/get-points")
@login_required
def get_points(tournament_url):
    """Get points for a match."""
    match_id = request.args.get("match_id")
    if not match_id:
        return json_error("Match ID required")

    from app.services.match_actions_service import MatchActionsService

    res = MatchActionsService.get_points(
        tournament_url, current_user.id, match_id=match_id
    )
    # Preserve historical behavior: errors return 200 for this endpoint.
    return json_from_result(res, ok_to_payload=lambda d: d, err_status_code=200)


@bp.route("/<tournament_url>/match-state")
def match_state(tournament_url):
    """Get current match state for polling. Public endpoint."""
    match_id = request.args.get("id")
    if not match_id:
        return jsonify({"error": "Match ID required"}), 400

    match = Match.query.filter_by(uuid=match_id, event=tournament_url).first()
    if not match:
        return jsonify({"error": "Match not found"}), 404

    points = Point.query.filter_by(match=match.uuid).order_by(Point.stamp).all()

    # Calculate scores
    team1_score = sum(1 for p in points if p.winner == "TEAM1" and not p.rerolled)
    team2_score = sum(1 for p in points if p.winner == "TEAM2" and not p.rerolled)

    # Scores by set
    sets = sorted(set(p.set_number for p in points))
    scores_by_set = {}
    for set_num in sets:
        set_points = [p for p in points if p.set_number == set_num]
        scores_by_set[set_num] = {
            "team1_score": sum(
                1 for p in set_points if p.winner == "TEAM1" and not p.rerolled
            ),
            "team2_score": sum(
                1 for p in set_points if p.winner == "TEAM2" and not p.rerolled
            ),
        }

    # Build points data
    points_data = []
    for p in points:
        # Ensure timestamps are timezone-aware UTC for proper JavaScript parsing
        # (timezone is already imported at top of file)
        stamp_iso = None
        end_stamp_iso = None

        stamp_iso = to_iso_z(p.stamp).unwrap_or(None)
        end_stamp_iso = to_iso_z(p.end_stamp).unwrap_or(None)

        points_data.append(
            {
                "uuid": p.uuid,
                "set_number": p.set_number,
                "winner": p.winner,
                "rerolled": p.rerolled,
                "stamp": stamp_iso,
                "end_stamp": end_stamp_iso,
                "stones_at_start": (
                    p.stones_at_start if match.set_type == "STONES" else None
                ),
            }
        )

    # Get finalized_at if match is completed or skipped
    finalized_at = None
    if (
        match.status in (MatchStatus.COMPLETED, MatchStatus.SKIPPED)
        and match.finalized_at
    ):
        finalized_at = match.finalized_at.isoformat()

    return jsonify(
        {
            "match_id": match.uuid,
            "status": match.status,
            "team1_score": team1_score,
            "team2_score": team2_score,
            "scores_by_set": scores_by_set,
            "points": points_data,
            "finalized_at": finalized_at,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    )


@bp.route("/<tournament_url>/match-actions/add-point", methods=["POST"])
@login_required
def add_point(tournament_url):
    """Add a new point to a match."""
    payload = request.json or {}
    match_id = (payload.get("match_id") or "").strip()
    set_number = payload.get("set_number", 1)
    timestamp = payload.get("timestamp")
    stones_at_start = payload.get("stones_at_start")  # Client-computed value

    from app.services.match_actions_service import MatchActionsService

    res = MatchActionsService.add_point(
        tournament_url,
        current_user.id,
        match_id=match_id,
        set_number=set_number,
        timestamp_ms=timestamp,
        stones_at_start=stones_at_start,
    )
    return json_from_result(res, ok_to_payload=lambda d: d)


@bp.route("/<tournament_url>/match-actions/update-point", methods=["POST"])
@login_required
def update_point(tournament_url):
    """Update a point."""
    payload = request.json or {}
    point_id = (payload.get("point_id") or "").strip()

    from app.services.match_actions_service import MatchActionsService

    res = MatchActionsService.update_point(
        tournament_url,
        current_user.id,
        point_id=point_id,
        data=payload,
    )
    return json_from_result(res, ok_to_payload=lambda d: d)


@bp.route("/<tournament_url>/match-actions/delete-point", methods=["POST"])
@login_required
def delete_point_action(tournament_url):
    """Delete a point."""
    payload = request.json or {}
    point_id = (payload.get("point_id") or "").strip()

    from app.services.match_actions_service import MatchActionsService

    res = MatchActionsService.delete_point(
        tournament_url, current_user.id, point_id=point_id
    )
    return json_from_result(res, ok_to_payload=lambda d: d)


@bp.route("/<tournament_url>/match-actions/update-stones", methods=["POST"])
@login_required
def update_stones(tournament_url):
    """Update stones remaining."""
    payload = request.json or {}
    match_id = (payload.get("match_id") or "").strip()
    stones_remaining = payload.get("stones_remaining")

    from app.services.match_actions_service import MatchActionsService

    res = MatchActionsService.update_stones(
        tournament_url,
        current_user.id,
        match_id=match_id,
        stones_remaining=stones_remaining,
    )
    return json_from_result(res, ok_to_payload=lambda d: d)


@bp.route("/<tournament_url>/match-actions/update-set", methods=["POST"])
@login_required
def update_set(tournament_url):
    """Update set number for a point."""
    payload = request.json or {}
    point_id = (payload.get("point_id") or "").strip()
    set_number = payload.get("set_number")

    from app.services.match_actions_service import MatchActionsService

    res = MatchActionsService.update_set(
        tournament_url, current_user.id, point_id=point_id, set_number=set_number
    )
    return json_from_result(res, ok_to_payload=lambda d: d)


@bp.route("/<tournament_url>/match-actions/complete-match", methods=["POST"])
@login_required
def complete_match(tournament_url):
    """Mark a match as completed."""
    payload = request.json or {}
    match_id = (payload.get("match_id") or "").strip()

    from app.services.match_actions_service import MatchActionsService

    res = MatchActionsService.complete_match(
        tournament_url, current_user.id, match_id=match_id
    )
    return json_from_result(res, ok_to_payload=lambda d: d)


@bp.route("/stones")
def stones_player():
    """Stones audio player page with server time synchronization."""
    import os
    from flask import current_app
    from flask_login import current_user

    # Hardcoded list of usernames that can see all audio files
    ALLOWED_USERS = os.environ.get("SILLY_USERS", "").split(":")  # Add usernames here

    # Get the static folder path
    static_folder = current_app.static_folder
    stones_dir = os.path.join(static_folder, "stones")

    # List all MP3 files in the stones directory
    import re

    mp3_files = []
    if os.path.exists(stones_dir) and os.path.isdir(stones_dir):
        for filename in os.listdir(stones_dir):
            if filename.lower().endswith(".mp3"):
                # Remove extension
                name_without_ext = os.path.splitext(filename)[0]
                # Remove numeric prefix (e.g., "1_", "2_", etc.) for display name
                display_name = re.sub(r"^\d+_", "", name_without_ext)
                # Extract numeric prefix for sorting (default to 0 if no prefix)
                match = re.match(r"^(\d+)_", name_without_ext)
                sort_order = int(match.group(1)) if match else 999999
                # URL-encode the filename for use in URLs
                from urllib.parse import quote

                filename_encoded = quote(filename, safe="")

                mp3_files.append(
                    {
                        "filename": filename,
                        "filename_encoded": filename_encoded,
                        "display_name": display_name,
                        "sort_order": sort_order,
                    }
                )
        # Sort by numeric prefix (sort_order), then by filename for consistent ordering
        mp3_files.sort(key=lambda x: (x["sort_order"], x["filename"]))

    # Filter files based on user permissions
    # Only show "Classic" and "Snare" unless user is in the allowed list
    user_can_see_all = (
        current_user.is_authenticated and current_user.id in ALLOWED_USERS
    )

    if not user_can_see_all:
        # Filter to only show "Classic" and "Snare" (case-insensitive)
        mp3_files = [
            f for f in mp3_files if f["display_name"].lower() in ["classic", "snare"]
        ]

    return render_template("stones_player.html", mp3_files=mp3_files)


@bp.route("/server-time")
def server_time():
    """Return current server time in unix timestamp format."""
    import time

    return jsonify(
        {
            "server_time": time.time(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    )


@bp.route("/youtube-stream-start")
def youtube_stream_start():
    """Get YouTube live stream start time."""
    import re
    import os
    import requests

    video_id = request.args.get("video_id")
    if not video_id:
        return jsonify({"error": "video_id required"}), 400

    # Extract video ID if full URL provided
    patterns = [
        r"(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/embed/|youtube\.com/v/)([^&\n?#]+)",
        r"^([a-zA-Z0-9_-]{11})$",
    ]
    for pattern in patterns:
        match = re.search(pattern, video_id)
        if match:
            video_id = match.group(1)
            break

    # Try to get stream start time from YouTube Data API v3
    api_key = os.environ.get("YOUTUBE_API_KEY")
    if not api_key:
        # If no API key, return null (client will handle gracefully)
        return jsonify({"start_time": None, "error": "YouTube API key not configured"})

    try:
        # Get video details from YouTube Data API v3
        url = f"https://www.googleapis.com/youtube/v3/videos"
        params = {
            "id": video_id,
            "part": "liveStreamingDetails,snippet",
            "key": api_key,
        }
        response = requests.get(url, params=params, timeout=5)
        response.raise_for_status()
        data = response.json()

        if not data.get("items"):
            return jsonify({"start_time": None, "error": "Video not found"})

        video = data["items"][0]
        live_details = video.get("liveStreamingDetails", {})

        # Get actual start time if available
        actual_start_time = live_details.get("actualStartTime")
        if actual_start_time:
            # YouTube API returns time in ISO 8601 format with 'Z' (UTC)
            # Parse it and ensure it's timezone-aware
            # (datetime and timezone are already imported at top of file)
            start_dt = datetime.fromisoformat(actual_start_time.replace("Z", "+00:00"))
            # Ensure it's UTC timezone-aware
            if start_dt.tzinfo is None:
                start_dt = start_dt.replace(tzinfo=timezone.utc)
            # Return in ISO format with timezone info (ensures 'Z' suffix for UTC)
            return jsonify(
                {
                    "start_time": start_dt.isoformat().replace("+00:00", "Z"),
                    "video_id": video_id,
                    "timezone": "UTC",
                }
            )

        # Stream not started yet
        return jsonify({"start_time": None, "error": "Stream has not started"})

    except requests.exceptions.RequestException as e:
        return (
            jsonify(
                {"start_time": None, "error": f"Error fetching stream info: {str(e)}"}
            ),
            500,
        )
    except Exception as e:
        return (
            jsonify({"start_time": None, "error": f"Unexpected error: {str(e)}"}),
            500,
        )
