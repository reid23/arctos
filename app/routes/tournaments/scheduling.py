"""Tournament scheduling routes.

Recompute, import/export, add/update matches, push-back, autocomplete,
DSL validation. Part of the ``tournaments`` blueprint.
"""

from flask import (
    request,
    jsonify,
    send_file,
)
from flask_login import login_required, current_user
from sqlalchemy.orm.attributes import flag_modified

from datetime import datetime, timedelta, timezone
import io

from models import (
    Tournament,
    Match,
    MatchNote,
    Tag,
    Point,
    Injury,
    Player,
    db,
)
from app.error_values import Ok, Err
from app.services.dual_write import (
    clear_match_referees,
    get_match_referee_rows,
    get_match_refs_csv,
    get_match_refs_initial_csv,
    set_match_referees_from_csv,
)
from app.services.match_service import MatchService
from app.services.match_start_eligibility import (
    get_can_start_and_reasons,
    get_conflicting_match_on_field,
)
from app.services.schedule_import_export_service import ScheduleImportExportService
from app.utils.dependencies import apply_match_dependencies
from app.utils.helpers import (
    can_head_ref_match,
    resolve_team_name_to_id,
    resolve_tag_to_team,
)
from app.utils.match_ref_resolution import (
    resolve_refs_slots,
    resolve_team_slot,
)
from app.utils.scheduling import (
    compute_dynamic_match_nominal_start_time,
    validate_match_input,
    validate_match_warnings,
    recompute_all_match_times,
    recompute_scheduled_and_nominal_times,
)
from app.utils.datetime_helpers import now_utc_naive, parse_datetime_local_to_utc
from app.utils.name_validation import match_name_char_error
from app.utils.decorators import require_tournament_organizer
from app.utils.responses import json_error
from app.utils.result_helpers import json_from_result, public_error_message
from app.utils.user_helpers import is_player


from app.domain.enums import (
    MatchStatus,
    RegistrationStatus,
    ScheduleType,
    SetType,
    WinnerSide,
)
from app.serializers.match_note_serializer import MatchNoteSerializer
from app.serializers.tournament_serializer import (
    team_name_for_match,
    tournament_to_dict,
)
from app.services.registration_resolver import (
    player_registrations_for_tournament,
    team_registrations_for_tournament,
)
from app.services.permission_service import PermissionService

from . import bp, detach_match_from_chain, update_match_previous_link


def _check_to(tournament_url):
    if not current_user.is_authenticated:
        return False
    return PermissionService.is_tournament_organizer(tournament_url, current_user)


@bp.route("/<tournament_url>/schedule-warnings", methods=["GET"])
@require_tournament_organizer("Only tournament organizers can access this page")
def schedule_warnings(tournament_url):
    """Soft-validation warnings for the entire schedule.

    Covers nonexistent teams, cyclic dependencies, missing match references,
    and double-booked teams. These are *not* enforced at create/edit time —
    the operator can fix them in any order from the schedule edit toolbar.
    """
    try:
        warnings = validate_match_warnings(tournament_url)
        return jsonify({"success": True, "warnings": warnings}), 200
    except Exception as e:
        return jsonify({"success": False, "error": f"Schedule warnings failed: {e}"}), 500


@bp.route("/<tournament_url>/recompute-schedule", methods=["POST"])
@require_tournament_organizer("Only tournament organizers can access this page")
def recompute_schedule(tournament_url):
    """Force full recompute of match times as if a match were just edited (TO only)."""
    try:
        recompute_scheduled_and_nominal_times(tournament_url)
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
            return json_error(
                public_error_message(err),
                status_code=err.status_code if hasattr(err, "status_code") else 400,
            )


@bp.route("/<tournament_url>/import-schedule", methods=["POST"])
@require_tournament_organizer("You must be a tournament organizer to import schedules")
def import_schedule(tournament_url):
    """Import schedule from uploaded TOML file."""
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


@bp.route("/<tournament_url>/add-match", methods=["POST"])
@require_tournament_organizer("Only tournament organizers can access this page")
def add_match(tournament_url):
    """Add a match to tournament."""
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
        existing_match = Match.query.filter_by(event=tournament_url, name=match_name).first()
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

    # For refs, populate explicit team IDs and resolve tag references maintaining index structure.
    # The ref slot rows are inserted after the match has been flushed so they can reference its uuid.
    refs_resolved_csv: str | None = None
    if refs_initial:
        refs_initial_list = [r.strip() for r in refs_initial.split(",")]
        refs_list = [""] * len(refs_initial_list)
        for i, initial_ref in enumerate(refs_initial_list):
            if not initial_ref:
                continue
            if is_explicit_team_id(initial_ref):
                refs_list[i] = initial_ref
            else:
                resolved_team = resolve_tag_to_team(initial_ref, tournament_url)
                if resolved_team:
                    refs_list[i] = resolved_team
        refs_resolved_csv = ",".join(refs_list)

    # Skip condition only for SAFE and FAST; clear for STATIC, BREAK, and JOIN
    skip_condition_raw = request.form.get("skip_condition", "").strip() or None
    skip_condition = skip_condition_raw if schedule_type in (ScheduleType.SAFE, ScheduleType.FAST) else None

    match = Match(
        name=match_name,
        event=tournament_url,
        field=request.form.get("field", ""),
        team1=final_team1,
        team1_initial=team1_name,
        team2=final_team2,
        team2_initial=team2_name,
        schedule_type=schedule_type,
        set_type=set_type,
        ribbon=ribbon,
        nsets=(
            int(request.form.get("nsets", 3)) if schedule_type not in (ScheduleType.BREAK, ScheduleType.JOIN) else None
        ),
        nominal_length=nominal_length,
        stones_per_set=stones_per_set_value,
        skip_condition=skip_condition,
    )

    db.session.add(match)
    db.session.flush()  # Flush to get UUID before updating links

    if refs_initial:
        set_match_referees_from_csv(match, refs_resolved_csv, refs_initial)

    # For dynamic matches, set previous_match from form and compute start time from it
    # For static matches, use the provided start_time
    if schedule_type != ScheduleType.STATIC:
        # Get previous_match from form
        prev_match_id = request.form.get("previous_match", "")
        if prev_match_id:
            # Update doubly linked list: insert this match after prev_match
            update_match_previous_link(match, prev_match_id, tournament_url, is_new=True)
        else:
            match.previous_match = None
        match.nominal_start_time = compute_dynamic_match_nominal_start_time(match, tournament_url)
    else:
        # Static matches can have manual start time
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
                    match.nominal_start_time = parse_datetime_local_to_utc(request.form["start_time"])
        elif request.form.get("start_time"):
            # Old format: datetime-local (assumed server-local), convert to UTC
            match.nominal_start_time = parse_datetime_local_to_utc(request.form["start_time"])

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
        recompute_scheduled_and_nominal_times(tournament_url)
    except Exception:
        pass

    return jsonify({"success": True, "message": "Match added successfully!"}), 200


@bp.route("/<tournament_url>/update-match", methods=["POST"])
@require_tournament_organizer("Only tournament organizers can access this page")
def update_match(tournament_url):
    """Update match."""
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
    allowed = _ALLOWED_SCHEDULE_TYPE_TRANSITIONS.get(current_schedule_type, (current_schedule_type,))
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
            existing_match = Match.query.filter_by(event=tournament_url, name=new_match_name).first()
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
    match.field = request.form.get("field", "")

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
        match.nominal_length = int(request.form.get("length", match.nominal_length or 60))
    else:
        match.nominal_length = int(request.form.get("length", match.nominal_length or 60))

    # Update skip_condition (only for SAFE, FAST; clear for STATIC, BREAK, and JOIN)
    skip_condition_raw = request.form.get("skip_condition", "").strip() or None
    match.skip_condition = skip_condition_raw if schedule_type in (ScheduleType.SAFE, ScheduleType.FAST) else None

    # If refs_initial changed, repopulate referee slots with explicit team IDs and resolved tag references
    old_refs_initial = get_match_refs_initial_csv(match)
    if old_refs_initial != (refs_initial or ""):
        if refs_initial:
            refs_initial_list = [r.strip() for r in refs_initial.split(",")]
            refs_list = [""] * len(refs_initial_list)
            for i, initial_ref in enumerate(refs_initial_list):
                if not initial_ref:
                    continue
                if is_explicit_team_id(initial_ref):
                    refs_list[i] = initial_ref
                else:
                    resolved_team = resolve_tag_to_team(initial_ref, tournament_url)
                    if resolved_team:
                        refs_list[i] = resolved_team
            set_match_referees_from_csv(match, ",".join(refs_list), refs_initial)
        else:
            clear_match_referees(match)

    # For dynamic matches, set previous_match from form and compute start time from it
    # For static matches, ensure previous_match is cleared and use provided start_time
    if schedule_type != ScheduleType.STATIC:
        # Get previous_match from form
        prev_match_id = request.form.get("previous_match", "")
        if prev_match_id:
            # Update doubly linked list: insert this match after prev_match
            update_match_previous_link(match, prev_match_id, tournament_url, is_new=False)
        else:
            # User cleared the previous-match selector: detach so the chain closes up.
            detach_match_from_chain(match, tournament_url)
        match.nominal_start_time = compute_dynamic_match_nominal_start_time(match, tournament_url)
    else:
        # Static matches have no chain links — fully detach so neighbours close up.
        detach_match_from_chain(match, tournament_url)
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
                    match.nominal_start_time = parse_datetime_local_to_utc(request.form["start_time"])
                else:
                    match.nominal_start_time = None
        elif request.form.get("start_time"):
            # Old format: datetime-local (assumed server-local), convert to UTC
            match.nominal_start_time = parse_datetime_local_to_utc(request.form["start_time"])
        else:
            match.nominal_start_time = None

    # Validate inputs and constraints
    ok, err = validate_match_input(match, tournament_url)
    if not ok:
        db.session.rollback()
        return jsonify({"success": False, "error": err}), 400

    db.session.flush()  # Flush before updating sequence

    # Recompute all match times (for all dynamic matches that depend on this one)
    recompute_scheduled_and_nominal_times(tournament_url)

    db.session.commit()
    return jsonify({"success": True, "message": "Match updated successfully!"}), 200


@bp.route("/<tournament_url>/update-all-references", methods=["POST"])
@require_tournament_organizer("Only tournament organizers can access this page")
def update_all_references(tournament_url):
    """Update all match references (winner/loser) for troubleshooting."""
    # Get all completed matches (have a winner; skipped matches are excluded)
    completed_matches = Match.query.filter_by(event=tournament_url, status=MatchStatus.COMPLETED).all()

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
@require_tournament_organizer("Only tournament organizers can access this page")
def push_back_matches(tournament_url):
    """Push all non-started matches backwards by a specified amount of time (in minutes)."""
    try:
        minutes = int(request.form.get("minutes", 0))
    except (ValueError, TypeError):
        return jsonify({"success": False, "error": "Invalid number of minutes"}), 400

    non_started_matches = (
        Match.query.filter_by(event=tournament_url)
        .filter(~Match.status.in_([MatchStatus.IN_PROGRESS, MatchStatus.COMPLETED, MatchStatus.SKIPPED]))
        .all()
    )

    updated_count = 0
    for match in non_started_matches:
        # Push back nominal_start_time if it exists
        if match.nominal_start_time:
            match.nominal_start_time = match.nominal_start_time + timedelta(minutes=minutes)
            updated_count += 1

        # Also push back confirmed_start_time if it exists (even when start time is already finalized)
        if match.confirmed_start_time:
            match.confirmed_start_time = match.confirmed_start_time + timedelta(minutes=minutes)

    db.session.commit()

    if updated_count > 0:
        msg = f"Pushed back {updated_count} non-started match(es) by {minutes} minute(s)"
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
                    "shortname": reg.shortname,
                    "id": reg.team,
                }
            )

    # Tags for this tournament (by name, surfaced as tag::TAG_NAME values)
    tags = Tag.query.filter_by(event=tournament_url).all() if "Tag" in globals() or True else []
    try:
        tags = Tag.query.filter_by(event=tournament_url).all()
    except Exception:
        tags = []
    for t in tags:
        name = (t.name or "").strip()
        if not query or query in name.lower():
            tag_ref = f"tag::{name}"
            suggestions.append({"type": "tag", "value": tag_ref, "label": tag_ref, "id": t.id})

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
        Nil,
        _format_dsl_value,
        _infer_types,
    )

    def serialize_value(value):
        """Convert the interpreted value to a JSON-serializable format."""
        if isinstance(value, Nil):
            return None
        if isinstance(value, (int, bool)):
            return value
        elif isinstance(value, list):
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

    data = request.get_json()
    expression = data.get("expression", "").strip()

    if not expression:
        return jsonify({"valid": True, "value": None, "simplified": None, "error": None})

    try:
        parser = get_parser(tournament_url)
        warnings = parser.static_check(expression)
        result = parser.parse(expression)

        # Serialize the full value for JSON response
        serialized_value = serialize_value(result)

        # Create string representation. We always send it back so the frontend can render
        # the simplified form (with chips). Suppressing on equality hid useful previews
        # for expressions whose literal text already happens to be in canonical form
        # (e.g. anything containing an unset [tag::Foo] that stays symbolic).
        simplified = _format_dsl_value(result)

        # Treat unresolvable team/match references as errors so the user notices typos.
        if warnings:
            return jsonify(
                {
                    "valid": False,
                    "value": None,
                    "simplified": None,
                    "error": "; ".join(warnings),
                    "warnings": warnings,
                }
            )

        return jsonify(
            {
                "valid": True,
                "value": serialized_value,
                "simplified": simplified,
                "error": None,
                "warnings": [],
                "result_type": sorted(_infer_types(result)),
            }
        )
    except DSLValidationError as e:
        return jsonify({"valid": False, "value": None, "simplified": None, "error": str(e)})
    except Exception as e:
        return jsonify(
            {
                "valid": False,
                "value": None,
                "simplified": None,
                "error": f"Parse error: {str(e)}",
            }
        )


@bp.route("/tournaments/<tournament_url>/start-match", methods=["GET"])
@login_required
def start_match_data_api(tournament_url):
    match_id = request.args.get("match_id")
    if not match_id:
        return jsonify({"error": "Match ID required"}), 400

    match = Match.query.get(match_id)
    if not match or match.event != tournament_url:
        return jsonify({"error": "Match not found"}), 404

    can_start, block_reasons, _ = get_can_start_and_reasons(tournament_url, match, current_user)
    if not can_start:
        error_msg = block_reasons[0] if block_reasons else "Cannot start this match."
        return jsonify({"error": error_msg, "reasons": block_reasons}), 400

    tournament = Tournament.query.get(tournament_url)

    all_prs = player_registrations_for_tournament(tournament, statuses=[RegistrationStatus.CONFIRMED])
    team1_prs = [pr for pr in all_prs if pr.team == match.team1]
    team2_prs = [pr for pr in all_prs if pr.team == match.team2]
    all_prs_list = all_prs

    team1_players = [(pr, Player.query.get(pr.player)) for pr in team1_prs]
    team2_players = [(pr, Player.query.get(pr.player)) for pr in team2_prs]
    all_players = [(pr, Player.query.get(pr.player)) for pr in all_prs_list]
    team1_players = [(pr, p) for pr, p in team1_players if p]
    team2_players = [(pr, p) for pr, p in team2_players if p]
    all_players = [(pr, p) for pr, p in all_players if p]

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

    def _player_item(pr, player):
        return {
            "id": player.id,
            "name": player.name,
            "jersey_name": pr.jersey_name,
            "jersey_number": pr.jersey_number,
            "team": pr.team,
            "paid": bool(pr.paid),
            "injuries": injuries_map.get(player.id, []),
        }

    return jsonify(
        {
            "tournament": tournament_to_dict(tournament),
            "match_info": {
                "uuid": match.uuid,
                "name": match.name,
                "field": match.field,
                "set_type": match.set_type.value if match.set_type else None,
                "refs": get_match_refs_csv(match) or None,
                "team1_name": team_name_for_match(tournament, match, "team1"),
                "team2_name": team_name_for_match(tournament, match, "team2"),
            },
            "team1_players": [_player_item(pr, p) for pr, p in team1_players],
            "team2_players": [_player_item(pr, p) for pr, p in team2_players],
            "all_players": [_player_item(pr, p) for pr, p in all_players],
        }
    )


@bp.route("/tournaments/<tournament_url>/start-match", methods=["POST"])
@login_required
def start_match_post_api(tournament_url):
    data = request.get_json() or {}
    match_id = data.get("match_id")
    if not match_id:
        return jsonify({"error": "Match ID required"}), 400

    from app.utils.result_helpers import json_from_result

    team1_players = ",".join(data.get("team1_players") or [])
    team2_players = ",".join(data.get("team2_players") or [])
    match_notes = data.get("match_notes") or ""
    stones_per_set = data.get("stones_per_set")

    res = MatchService.start_match(
        tournament_url,
        match_id,
        current_user,
        team1_players_csv=team1_players,
        team2_players_csv=team2_players,
        match_notes=match_notes,
        stones_per_set=stones_per_set,
    )
    return json_from_result(
        res,
        ok_to_payload=lambda v: {"match_id": v.uuid},
        err_status_code=400,
    )


@bp.route("/tournaments/<tournament_url>/finalize-match", methods=["GET"])
@login_required
def finalize_match_data_api(tournament_url):
    match_id = request.args.get("match_id")
    if not match_id:
        return jsonify({"error": "Match ID required"}), 400

    match = Match.query.get(match_id)
    if not match or match.event != tournament_url:
        return jsonify({"error": "Match not found"}), 404

    if not can_head_ref_match(tournament_url, current_user.id, match=match):
        return jsonify({"error": "Forbidden"}), 403

    tournament = Tournament.query.get(tournament_url)
    points = Point.query.filter_by(match=match.uuid).order_by(Point.stamp).all()

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

    return jsonify(
        {
            "tournament": tournament_to_dict(tournament),
            "match_info": {
                "uuid": match.uuid,
                "name": match.name,
                "team1_name": team_name_for_match(tournament, match, "team1"),
                "team2_name": team_name_for_match(tournament, match, "team2"),
            },
            "points": [
                {
                    "uuid": p.uuid,
                    "set_number": p.set_number,
                    "winner": p.winner,
                    "rerolled": p.rerolled,
                }
                for p in points
            ],
            "point_notes_map": point_notes_map,
            "stones_elapsed_map": stones_elapsed_map,
            "team1_score": team1_score,
            "team2_score": team2_score,
        }
    )


@bp.route("/tournaments/<tournament_url>/finalize-match", methods=["POST"])
@login_required
def finalize_match_post_api(tournament_url):
    data = request.get_json() or {}
    match_id = data.get("match_id")
    if not match_id:
        return jsonify({"error": "Match ID required"}), 400

    match = Match.query.get(match_id)
    if not match or match.event != tournament_url:
        return jsonify({"error": "Match not found"}), 404

    if not can_head_ref_match(tournament_url, current_user.id, match=match):
        return jsonify({"error": "Forbidden"}), 403

    match.status = MatchStatus.COMPLETED
    match_winner = data.get("match_winner")
    if not match_winner:
        return jsonify({"error": "Match winner required"}), 400

    match.completed_time = now_utc_naive()
    match.finalized_by = current_user.id
    match.final_notes = data.get("final_notes") or ""
    match.match_winner = match_winner
    match.finalized_at = now_utc_naive()

    team1_signature = data.get("team1_signature")
    team2_signature = data.get("team2_signature")
    if team1_signature:
        match.team1_signature = team1_signature
    if team2_signature:
        match.team2_signature = team2_signature
    db.session.commit()

    try:
        apply_match_dependencies(tournament_url, match)
    except Exception as e:
        print(f"Dependency update error for match {match.name}: {e}")

    try:
        from app.utils.scheduling import recompute_all_match_times

        recompute_all_match_times(tournament_url)
        db.session.commit()
    except Exception as e:
        print(f"Error recomputing match times: {e}")

    return jsonify({"ok": True})


@bp.route(
    "/tournaments/<tournament_url>/matches/<match_id>/force-start",
    methods=["POST"],
)
@login_required
def force_start_match_api(tournament_url, match_id):
    """Force-start a match: resolve teams/refs, handle conflicting match, convert to static."""

    match = Match.query.filter_by(uuid=match_id, event=tournament_url).first_or_404()
    data = request.get_json()
    if not data:
        return jsonify({"error": "Invalid JSON"}), 400

    # Auth: require head ref
    if not current_user.is_authenticated:
        return jsonify({"error": "Must be logged in"}), 401
    if not is_player(current_user):
        return jsonify({"error": "Only player accounts can force start matches"}), 403
    if not can_head_ref_match(tournament_url, current_user.id, match=match):
        return jsonify({"error": "You are not allowed to head ref this match"}), 403

    team1_input = str(data.get("team1") or "").strip()
    team2_input = str(data.get("team2") or "").strip()
    refs_list = data.get("refs") or []
    if not isinstance(refs_list, list):
        refs_list = []
    conflicting_action = (data.get("conflicting_match_action") or "").strip()
    conflicting_winner = (data.get("conflicting_match_winner") or "").strip()

    # 1. Handle conflicting match (if any)
    other_match = get_conflicting_match_on_field(tournament_url, match)
    if other_match:
        if not conflicting_action:
            return (
                jsonify({"error": "Another match is in progress on this field. Choose SKIP or COMPLETE."}),
                400,
            )
        if conflicting_action == "COMPLETE" and conflicting_winner not in (
            "TEAM1",
            "TEAM2",
        ):
            return (
                jsonify({"error": "When marking as COMPLETE, choose TEAM1 or TEAM2 as winner."}),
                400,
            )

        now = now_utc_naive()
        # Close unfinished points on the conflicting match
        for pt in Point.query.filter_by(match=other_match.uuid).all():
            if pt.end_stamp is None:
                pt.end_stamp = now
        if conflicting_action == "SKIP":
            other_match.status = MatchStatus.SKIPPED
            other_match.match_winner = None
        else:
            other_match.status = MatchStatus.COMPLETED
            other_match.match_winner = WinnerSide.TEAM1 if conflicting_winner == "TEAM1" else WinnerSide.TEAM2
        other_match.finalized_at = now

    # 2. Update target match
    t1_id, t1_initial = resolve_team_slot(team1_input, tournament_url)
    t2_id, t2_initial = resolve_team_slot(team2_input, tournament_url)
    if not t1_id or not t2_id:
        return jsonify({"error": "Team 1 and Team 2 are required"}), 400

    match.team1 = t1_id
    match.team1_initial = t1_initial or team1_input
    match.team2 = t2_id
    match.team2_initial = t2_initial or team2_input

    # Refs: preserve slot count (registration, explicit id, tag)
    r_csv, i_csv = resolve_refs_slots(refs_list, tournament_url)
    set_match_referees_from_csv(match, r_csv, i_csv)

    # Convert to static
    match.schedule_type = ScheduleType.STATIC
    match.nominal_start_time = now_utc_naive()
    match.status = MatchStatus.READY_TO_START

    # The match is being converted to STATIC and started — fully splice it out of
    # its per-field chain so the surrounding nodes close up.
    detach_match_from_chain(match, tournament_url)
    flag_modified(match, "previous_match")
    flag_modified(match, "next_match")

    db.session.flush()
    db.session.commit()
    recompute_all_match_times(tournament_url)

    return jsonify({"success": True})


@bp.route("/tournaments/<tournament_url>/recompute-schedule", methods=["POST"])
@login_required
def recompute_schedule_api(tournament_url):
    if not _check_to(tournament_url):
        return jsonify({"error": "Forbidden"}), 403

    recompute_scheduled_and_nominal_times(tournament_url)
    return jsonify({"success": True})


@bp.route("/tournaments/<tournament_url>/update-all-references", methods=["POST"])
@login_required
def update_all_references_api(tournament_url):
    if not _check_to(tournament_url):
        return jsonify({"error": "Forbidden"}), 403

    completed = (
        Match.query.filter_by(event=tournament_url)
        .filter(Match.status.in_([MatchStatus.COMPLETED, MatchStatus.SKIPPED]))
        .all()
    )
    for m in completed:
        apply_match_dependencies(tournament_url, m)

    return jsonify({"success": True})


@bp.route("/tournaments/<tournament_url>/push-back-matches", methods=["POST"])
@login_required
def push_back_matches_api(tournament_url):
    if not _check_to(tournament_url):
        return jsonify({"error": "Forbidden"}), 403

    data = request.get_json()
    minutes = int(data.get("minutes", 0))
    if not minutes:
        return jsonify({"success": True})

    matches = (
        Match.query.filter_by(event=tournament_url)
        .filter(Match.status.in_([MatchStatus.NOT_STARTED, MatchStatus.TIME_FINALIZED]))
        .all()
    )
    from datetime import timedelta

    for m in matches:
        if m.schedule_type == ScheduleType.STATIC and m.nominal_start_time:
            m.nominal_start_time += timedelta(minutes=minutes)

    db.session.commit()
    recompute_all_match_times(tournament_url)
    return jsonify({"success": True})


@bp.route("/tournaments/<tournament_url>/update-tags", methods=["POST"])
@login_required
def update_tags_api(tournament_url):
    if not _check_to(tournament_url):
        return jsonify({"error": "Forbidden"}), 403

    data = request.get_json()
    tag_id = data.get("tag_id")
    team_id = data.get("team_id")

    if not tag_id:
        return jsonify({"error": "Tag required"}), 400

    tag = Tag.query.filter_by(id=tag_id, event=tournament_url).first_or_404()
    tag.team = team_id if team_id else None
    db.session.commit()

    # Update matches
    matches = Match.query.filter_by(event=tournament_url).all()
    tag_ref = f"tag::{tag.name}"

    for m in matches:
        if m.status in (
            MatchStatus.COMPLETED,
            MatchStatus.SKIPPED,
            MatchStatus.IN_PROGRESS,
        ):
            continue
        if m.team1_initial == tag_ref:
            m.team1 = team_id
        if m.team2_initial == tag_ref:
            m.team2 = team_id

        for row in get_match_referee_rows(m):
            if (row.initial or "").strip() == tag_ref:
                row.team_id = team_id

    db.session.commit()
    recompute_all_match_times(tournament_url)
    return jsonify({"success": True})


@bp.route("/tournaments/<tournament_url>/export-schedule", methods=["GET"])
@login_required
def export_schedule_api(tournament_url):
    if not _check_to(tournament_url):
        return jsonify({"error": "Forbidden"}), 403

    from app.services.schedule_import_export_service import ScheduleImportExportService
    from app.utils.result_helpers import json_from_result

    res = ScheduleImportExportService.export_schedule(tournament_url)
    return json_from_result(res, ok_to_payload=lambda v: {"toml": v})


@bp.route("/tournaments/<tournament_url>/import-schedule", methods=["POST"])
@login_required
def import_schedule_api(tournament_url):
    if not _check_to(tournament_url):
        return jsonify({"error": "Forbidden"}), 403

    data = request.get_json()
    toml_content = data.get("toml")
    if not toml_content:
        return jsonify({"error": "TOML content required"}), 400

    from app.services.schedule_import_export_service import ScheduleImportExportService
    from app.utils.result_helpers import json_from_result

    def _ok_payload(_):
        recompute_scheduled_and_nominal_times(tournament_url)
        return {}

    res = ScheduleImportExportService.import_schedule(tournament_url, toml_content)
    return json_from_result(res, ok_to_payload=_ok_payload)
