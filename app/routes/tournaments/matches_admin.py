"""Tournament match / field / tag administrative CRUD endpoints.

Part of the ``tournaments`` blueprint. Uses the same Blueprint object
defined in :mod:`app.routes.tournaments.__init__`.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json

from flask import jsonify, request
from flask_login import current_user, login_required
from sqlalchemy.orm.attributes import flag_modified

from app.domain.enums import (
    STRUCTURAL_SCHEDULE_TYPES,
    MatchStatus,
    ScheduleType,
    SetType,
)
from app.services.dual_write import (
    clear_match_referees,
    get_match_ref_initials,
    set_match_referees_from_csv,
)
from app.services.permission_service import PermissionService
from app.utils.match_ref_resolution import (
    refs_string_to_tokens,
    resolve_refs_slots,
)
from app.utils.helpers import (
    resolve_match_winner_loser_ref,
    resolve_team_name_to_id,
    resolve_tag_to_team,
)
from app.utils.name_validation import match_name_char_error
from app.utils.scheduling import (
    compute_dynamic_match_nominal_start_time,
    recompute_scheduled_and_nominal_times,
    validate_match_input,
)
from models import (
    Field,
    Match,
    Point,
    Tag,
    db,
)

from . import (
    bp,
    delete_matches_with_children,
    detach_match_from_chain,
    update_match_previous_link,
)


def _check_to(tournament_url):
    if not current_user.is_authenticated:
        return False
    return PermissionService.is_tournament_organizer(tournament_url, current_user)


def _tag_usage(tournament_url, tag_name):
    """Return list of human-readable strings describing where tag is used, or empty if not used."""
    tag_ref = f"tag::{tag_name}"
    used = []
    for m in Match.query.filter_by(event=tournament_url).all():
        if m.team1_initial and m.team1_initial.strip() == tag_ref:
            used.append(f'Team 1 of match "{m.name}"')
        if m.team2_initial and m.team2_initial.strip() == tag_ref:
            used.append(f'Team 2 of match "{m.name}"')
        if any(initial == tag_ref for initial in get_match_ref_initials(m)):
            used.append(f'Refs of match "{m.name}"')
        if m.skip_condition and (tag_ref in m.skip_condition or tag_name in m.skip_condition):
            used.append(f'Skip condition of match "{m.name}"')
    return used


# Statuses where a match has already started and is no longer editable.
_LOCKED_STATUSES = (MatchStatus.IN_PROGRESS, MatchStatus.COMPLETED, MatchStatus.SKIPPED)


def _is_explicit_team_id(val: str) -> bool:
    """True if *val* looks like a literal team id (not a tag:: or Match::winner ref)."""
    if not val or not val.strip():
        return False
    val = val.strip()
    if val.lower().startswith("tag::"):
        return False
    lower = val.lower()
    if "::winner" in lower or "::loser" in lower:
        return False
    return True


def _resolve_initial_to_cached_team(initial: str, tournament_url: str) -> str | None:
    """Resolve a team-slot ``_initial`` token to a concrete team id, if possible.

    Tries, in order: ``MatchName::winner`` / ``::loser`` (when the referenced
    match's outcome is already decided), ``tag::Foo``, registered name /
    pseudonym, and bare team id. Returns ``None`` for anything not currently
    resolvable — for unfinished match refs, the cache is filled in later by
    ``apply_match_dependencies`` when the source match completes.
    """
    if not initial:
        return None
    initial = initial.strip()
    if not initial:
        return None
    winner_loser = resolve_match_winner_loser_ref(initial, tournament_url)
    if winner_loser is not None:
        return winner_loser
    if initial.lower().startswith("tag::"):
        return resolve_tag_to_team(initial, tournament_url)
    team_id, _ = resolve_team_name_to_id(initial, tournament_url)
    if team_id:
        return team_id
    if _is_explicit_team_id(initial):
        return initial
    return None


@bp.route("/tournaments/<tournament_url>/matches/<match_id>", methods=["PUT"])
@login_required
def update_match_api(tournament_url, match_id):
    if not _check_to(tournament_url):
        return jsonify({"error": "Forbidden"}), 403

    match = Match.query.filter_by(uuid=match_id, event=tournament_url).first_or_404()
    # STATBREAK status is time-derived (nobody "starts" them): locked once the
    # effective status says the break has happened, editable before that.
    if match.schedule_type == ScheduleType.STATBREAK:
        if match.effective_status == MatchStatus.COMPLETED:
            return (
                jsonify({"error": "Static break cannot be edited once its start time has passed."}),
                409,
            )
    elif match.status in _LOCKED_STATUSES:
        return (
            jsonify({"error": (f"Match cannot be edited once it has started (current status: {match.status.value}).")}),
            409,
        )
    data = request.get_json()
    if not data:
        return jsonify({"error": "Invalid JSON"}), 400

    # Allowed schedule type transitions when editing (only these target types are allowed from each source)
    _ALLOWED_SCHEDULE_TYPE_TRANSITIONS = {
        ScheduleType.STATIC: (
            ScheduleType.STATIC,
            ScheduleType.SAFE,
            ScheduleType.FAST,
        ),
        ScheduleType.SAFE: (ScheduleType.SAFE, ScheduleType.FAST),
        ScheduleType.FAST: (ScheduleType.FAST,),
        # Breaks and joins interconvert (both are dynamic field reservations);
        # nothing converts TO a STATBREAK — create one deliberately, with a
        # start time, instead of mutating a dynamic break into a static one.
        ScheduleType.BREAK: (ScheduleType.BREAK, ScheduleType.JOIN),
        ScheduleType.STATBREAK: (ScheduleType.STATBREAK, ScheduleType.BREAK),
        ScheduleType.JOIN: (ScheduleType.JOIN, ScheduleType.BREAK),
    }

    # Extract fields. `name` is intentionally not extracted — match names are immutable
    # after creation; pretend any client-supplied `name` doesn't exist.
    field = data.get("field")
    schedule_type_str = data.get("schedule_type")  # STATIC, SAFE, FAST, BREAK, JOIN
    length = data.get("length")
    start_time_str = data.get("start_time")
    previous_match_id = data.get("previous_match_id")
    refs = data.get("refs")  # list of strings
    team1_input = data.get("team1")
    team2_input = data.get("team2")
    set_type_str = data.get("set_type")  # SETS, STONES
    nsets = data.get("nsets")
    stones_per_set = data.get("stones_per_set")
    ribbon = data.get("ribbon")
    skip_condition = data.get("skip_condition")

    # Schedule Type (apply first so name uniqueness uses the new type)
    if schedule_type_str:
        try:
            new_schedule_type = ScheduleType(schedule_type_str)
            current_schedule_type = match.schedule_type
            allowed = _ALLOWED_SCHEDULE_TYPE_TRANSITIONS.get(current_schedule_type, (current_schedule_type,))
            if new_schedule_type not in allowed:
                return (
                    jsonify(
                        {
                            "error": f"Match type cannot be changed from {current_schedule_type.value} to {new_schedule_type.value}. "
                            "Allowed changes: Static→Safe/Fast, Safe→Fast, Break↔Join, Static Break→Break."
                        }
                    ),
                    400,
                )
            # Structural rows sharing a name form one logical group; converting a
            # single row of a multi-row group would leave it mixed-type. Convert
            # the whole group at once via the break-groups endpoint instead.
            if (
                new_schedule_type != current_schedule_type
                and current_schedule_type in STRUCTURAL_SCHEDULE_TYPES
                and Match.query.filter_by(event=tournament_url, name=match.name)
                .filter(Match.schedule_type.in_(STRUCTURAL_SCHEDULE_TYPES), Match.uuid != match.uuid)
                .count()
                > 0
            ):
                return (
                    jsonify(
                        {
                            "error": "This break/join spans multiple fields; convert the whole group via the group editor."
                        }
                    ),
                    400,
                )
            match.schedule_type = new_schedule_type
        except ValueError:
            pass  # Ignore invalid enum

    # Validate inputs
    if field is not None:  # field can be empty string/null
        match.field = field

    # Match-name uniqueness check (still useful when only `field` changes for
    # structural matches, whose names are unique per field across BREAK/STATBREAK/JOIN).
    if field is not None:
        effective_name = (match.name or "").strip()
        effective_field = (match.field or "").strip()
        if effective_name and match.schedule_type in STRUCTURAL_SCHEDULE_TYPES:
            existing_name = (
                Match.query.filter_by(
                    event=tournament_url,
                    name=effective_name,
                    field=effective_field,
                )
                .filter(
                    Match.schedule_type.in_(STRUCTURAL_SCHEDULE_TYPES),
                    Match.uuid != match.uuid,
                )
                .first()
            )
            if existing_name:
                return (
                    jsonify(
                        {"error": f"A {match.schedule_type.value} match with this name already exists on this field."}
                    ),
                    400,
                )

    # Structural matches (BREAK/STATBREAK/JOIN) never have playing teams or
    # refs — they only occupy fields.
    if match.schedule_type in STRUCTURAL_SCHEDULE_TYPES:
        match.team1 = None
        match.team1_initial = None
        match.team2 = None
        match.team2_initial = None
        clear_match_referees(match)
    else:
        # When _initial fields change, write through to the resolved team cache
        # (team1 / team2). _resolve_initial_to_cached_team returns None for
        # unresolvable forms (e.g. MatchName::winner before that match finishes).
        if team1_input is not None:
            team1_name = str(team1_input).strip()
            match.team1_initial = team1_name or None
            match.team1 = _resolve_initial_to_cached_team(team1_name, tournament_url)

        if team2_input is not None:
            team2_name = str(team2_input).strip()
            match.team2_initial = team2_name or None
            match.team2 = _resolve_initial_to_cached_team(team2_name, tournament_url)

        # Refs: parallel refs / refs_initial (same slot count). resolve_refs_slots
        # already handles the cached-resolution side, so the join-table writer
        # both updates _initial and the resolved team_id together.
        if refs is not None:
            if isinstance(refs, list):
                r_csv, i_csv = resolve_refs_slots(refs, tournament_url)
            else:
                toks = refs_string_to_tokens(refs)
                r_csv, i_csv = resolve_refs_slots(toks, tournament_url)
            set_match_referees_from_csv(match, r_csv, i_csv)

    # Set Type
    if set_type_str:
        try:
            match.set_type = SetType(set_type_str)
        except ValueError:
            pass

    if nsets is not None:
        match.nsets = int(nsets)

    if stones_per_set is not None:
        match.stones_per_set = int(stones_per_set)

    if ribbon is not None:
        match.ribbon = bool(ribbon)

    # Length
    if match.schedule_type == ScheduleType.JOIN:
        match.nominal_length = 0
    elif length is not None:
        match.nominal_length = int(length)

    # Skip Condition (only for SAFE/FAST)
    if skip_condition is not None:
        match.skip_condition = (
            (skip_condition.strip() if skip_condition.strip() else None)
            if match.schedule_type in (ScheduleType.SAFE, ScheduleType.FAST)
            else None
        )

    # Clear stones_per_set for non-STONES
    if match.set_type != SetType.STONES:
        match.stones_per_set = None

    # BREAK, JOIN, FAST, SAFE require non-empty previous_match on same field
    if match.schedule_type in (
        ScheduleType.BREAK,
        ScheduleType.JOIN,
        ScheduleType.FAST,
        ScheduleType.SAFE,
    ):
        prev_id = (previous_match_id or "").strip() if previous_match_id is not None else ""
        if not prev_id:
            return (
                jsonify({"error": "Previous match is required for Break, Join, Fast, and Safe matches."}),
                400,
            )
        effective_field = match.field or ""
        if not effective_field:
            return (
                jsonify({"error": "Field is required when using a previous match."}),
                400,
            )
        prev_match = Match.query.filter_by(uuid=prev_id, event=tournament_url).first()
        if not prev_match:
            return jsonify({"error": "Previous match not found."}), 400
        prev_field = (prev_match.field or "").strip()
        if prev_field != effective_field.strip():
            return jsonify({"error": "Previous match must be on the same field."}), 400

    # Scheduling Logic
    from datetime import datetime, timezone

    if match.schedule_type == ScheduleType.STATIC:
        if start_time_str:
            try:
                # Handle ISO format (potentially with Z or offset)
                dt = datetime.fromisoformat(start_time_str.replace("Z", "+00:00"))
                # Ensure naive UTC
                if dt.tzinfo:
                    dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
                match.nominal_start_time = dt
                # User explicitly chose a start time — keep scheduled anchor in sync.
                match.scheduled_start_time = dt
            except ValueError:
                pass

        # STATIC matches have no chain links — fully detach so neighbours close up.
        detach_match_from_chain(match, tournament_url)
        flag_modified(match, "previous_match")
        flag_modified(match, "next_match")
    elif match.schedule_type == ScheduleType.STATBREAK:
        # Statically-scheduled break: user-supplied start time is written to both
        # timelines and the solver never moves it. Unlike STATIC it may sit in a
        # field chain (matches chained after it wait for its end), so we only
        # touch chain links when the client explicitly supplies one.
        if start_time_str:
            try:
                dt = datetime.fromisoformat(start_time_str.replace("Z", "+00:00"))
                if dt.tzinfo:
                    dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
                match.nominal_start_time = dt
                match.scheduled_start_time = dt
            except ValueError:
                pass
        if previous_match_id:
            update_match_previous_link(match, previous_match_id, tournament_url)
    else:
        # Dynamic (BREAK, JOIN, FAST, SAFE)
        match.nominal_start_time = compute_dynamic_match_nominal_start_time(match, tournament_url)
        # If we don't yet have a scheduled anchor, seed it from the freshly-computed
        # nominal so subsequent recomputations don't drag dependency edges around.
        if match.scheduled_start_time is None and match.nominal_start_time is not None:
            match.scheduled_start_time = match.nominal_start_time
        if previous_match_id:
            update_match_previous_link(match, previous_match_id, tournament_url)
        else:
            # User cleared the previous-match selector: detach so the chain closes up
            # rather than leaving stale pointers in either direction.
            detach_match_from_chain(match, tournament_url)

    ok, err = validate_match_input(match, tournament_url)
    if not ok:
        db.session.rollback()
        return jsonify({"error": err}), 400

    db.session.flush()  # Emit UPDATE for previous_match etc. before commit
    db.session.commit()

    # Recompute all times
    recompute_scheduled_and_nominal_times(tournament_url)

    return jsonify({"success": True})


@bp.route("/tournaments/<tournament_url>/fields/<int:field_id>", methods=["PUT"])
@login_required
def update_field_api(tournament_url, field_id):
    if not _check_to(tournament_url):
        return jsonify({"error": "Forbidden"}), 403

    field = Field.query.filter_by(id=field_id, event=tournament_url).first_or_404()
    data = request.get_json()
    if not data:
        return jsonify({"error": "Invalid JSON"}), 400

    new_field_name = data.get("name", "").strip()
    if not new_field_name:
        return jsonify({"error": "Field name required"}), 400

    old_field_name = field.name
    field.name = new_field_name

    camera_urls = [url for url in data.get("camera_urls", []) if url.strip()]
    old_camera_urls = []
    try:
        if field.camera:
            loaded = json.loads(field.camera)
            if isinstance(loaded, list):
                old_camera_urls = loaded
            else:
                old_camera_urls = [field.camera]
    except:
        if field.camera:
            old_camera_urls = [field.camera]

    field.camera = json.dumps(camera_urls) if camera_urls else ""

    # Update matches and points (logic copied from tournaments.py)
    field_name_for_query = old_field_name if old_field_name != new_field_name else new_field_name
    matches_to_update = Match.query.filter_by(event=tournament_url, field=field_name_for_query).all()

    camera_urls_changed = old_camera_urls != camera_urls

    if camera_urls_changed:
        old_to_new_index_map = {}
        for new_idx, new_url in enumerate(camera_urls):
            try:
                old_idx = old_camera_urls.index(new_url)
                old_to_new_index_map[str(old_idx)] = str(new_idx)
            except ValueError:
                pass

        for match in matches_to_update:
            if match.camera_stream_starts:
                try:
                    stream_starts = json.loads(match.camera_stream_starts)
                    new_stream_starts = {}
                    for old_idx_str, start_time in stream_starts.items():
                        if old_idx_str in old_to_new_index_map:
                            new_idx_str = old_to_new_index_map[old_idx_str]
                            new_stream_starts[new_idx_str] = start_time
                    match.camera_stream_starts = json.dumps(new_stream_starts) if new_stream_starts else None
                except:
                    match.camera_stream_starts = None

        from app.utils.camera_helpers import calculate_stream_timestamp

        for match in matches_to_update:
            points = Point.query.filter_by(match=match.uuid).all()
            stream_starts = {}
            if match.camera_stream_starts:
                try:
                    stream_starts = json.loads(match.camera_stream_starts)
                except:
                    pass

            for point in points:
                if point.camera_index is not None:
                    old_idx_str = str(point.camera_index)
                    if old_idx_str in old_to_new_index_map:
                        point.camera_index = int(old_to_new_index_map[old_idx_str])
                    else:
                        # Try to find by URL
                        if point.camera_index < len(old_camera_urls):
                            old_url = old_camera_urls[point.camera_index]
                            try:
                                new_idx = camera_urls.index(old_url)
                                point.camera_index = new_idx
                            except ValueError:
                                point.camera_index = None
                                point.stream_timestamp = None
                        else:
                            point.camera_index = None
                            point.stream_timestamp = None

                if point.camera_index is not None and point.stamp:
                    camera_idx_str = str(point.camera_index)
                    if camera_idx_str in stream_starts:
                        new_ts = calculate_stream_timestamp(point.stamp, stream_starts[camera_idx_str])
                        if new_ts is not None:
                            point.stream_timestamp = new_ts

    if old_field_name != new_field_name:
        for match in matches_to_update:
            match.field = new_field_name

    # Optional: set stream start times for cameras (e.g. from YouTube API or user input).
    # Merge with existing: only update indices present in the request; never remove other keys.
    stream_start_times = data.get("stream_start_times")
    if stream_start_times is not None and isinstance(stream_start_times, list):
        from app.utils.camera_helpers import calculate_stream_timestamp

        for match in matches_to_update:
            stream_starts = {}
            if match.camera_stream_starts:
                try:
                    loaded = json.loads(match.camera_stream_starts)
                    if isinstance(loaded, dict):
                        stream_starts = dict(loaded)
                except (TypeError, ValueError):
                    pass
            for idx, val in enumerate(stream_start_times):
                if idx >= len(camera_urls):
                    break
                if val is not None and isinstance(val, str) and val.strip():
                    stream_starts[str(idx)] = val.strip()
                elif str(idx) in stream_starts:
                    del stream_starts[str(idx)]
            match.camera_stream_starts = json.dumps(stream_starts) if stream_starts else None
        # Recompute point stream_timestamp for matches we updated
        for match in matches_to_update:
            points = Point.query.filter_by(match=match.uuid).all()
            stream_starts = {}
            if match.camera_stream_starts:
                try:
                    stream_starts = json.loads(match.camera_stream_starts)
                except (TypeError, ValueError):
                    pass
            for point in points:
                if point.camera_index is not None and point.stamp and str(point.camera_index) in stream_starts:
                    new_ts = calculate_stream_timestamp(point.stamp, stream_starts[str(point.camera_index)])
                    if new_ts is not None:
                        point.stream_timestamp = new_ts

    db.session.commit()
    return jsonify({"success": True})


@bp.route("/tournaments/<tournament_url>/matches", methods=["POST"])
@login_required
def create_match_api(tournament_url):
    if not _check_to(tournament_url):
        return jsonify({"error": "Forbidden"}), 403

    data = request.get_json()
    if not data:
        return jsonify({"error": "Invalid JSON"}), 400

    name = data.get("name")
    if not name:
        return jsonify({"error": "Match name is required"}), 400
    mn_err = match_name_char_error(name.strip())
    if mn_err:
        return jsonify({"error": mn_err}), 400

    # Parse schedule type and field for name-uniqueness scope (BREAK/JOIN are unique per field)
    schedule_type_str = data.get("schedule_type")
    schedule_type = ScheduleType.STATIC
    if schedule_type_str:
        try:
            schedule_type = ScheduleType(schedule_type_str)
        except ValueError:
            pass
    effective_field = (data.get("field") or "").strip()

    # Name uniqueness: structural matches (BREAK/STATBREAK/JOIN) are unique per
    # (name, event, field) across the structural group; others globally in tournament.
    if schedule_type in STRUCTURAL_SCHEDULE_TYPES:
        existing = (
            Match.query.filter_by(
                event=tournament_url,
                name=name.strip(),
                field=effective_field,
            )
            .filter(Match.schedule_type.in_(STRUCTURAL_SCHEDULE_TYPES))
            .first()
        )
    else:
        existing = Match.query.filter_by(event=tournament_url, name=name.strip()).first()
    if existing:
        if schedule_type in STRUCTURAL_SCHEDULE_TYPES:
            return (
                jsonify({"error": f"A {schedule_type.value} match with this name already exists on this field."}),
                400,
            )
        return jsonify({"error": "Match name already exists"}), 400

    match = Match(event=tournament_url, name=name)
    match.field = data.get("field")
    match.nominal_length = int(data.get("length")) if data.get("length") is not None else None
    match.schedule_type = schedule_type

    # BREAK, JOIN, FAST, SAFE require non-empty previous_match on same field
    if match.schedule_type in (
        ScheduleType.BREAK,
        ScheduleType.JOIN,
        ScheduleType.FAST,
        ScheduleType.SAFE,
    ):
        prev_id = (data.get("previous_match_id") or "").strip()
        if not prev_id:
            return (
                jsonify({"error": "Previous match is required for Break, Join, Fast, and Safe matches."}),
                400,
            )
        effective_field = (match.field or "").strip()
        if not effective_field:
            return (
                jsonify({"error": "Field is required when using a previous match."}),
                400,
            )
        prev_match = Match.query.filter_by(uuid=prev_id, event=tournament_url).first()
        if not prev_match:
            return jsonify({"error": "Previous match not found."}), 400
        prev_field = (prev_match.field or "").strip()
        if prev_field != effective_field:
            return jsonify({"error": "Previous match must be on the same field."}), 400

    if match.schedule_type in (ScheduleType.STATIC, ScheduleType.STATBREAK):
        start_time_str = data.get("start_time")
        if not start_time_str and match.schedule_type == ScheduleType.STATBREAK:
            return jsonify({"error": "Start time is required for Static Break matches."}), 400
        if start_time_str:
            try:
                dt = datetime.fromisoformat(start_time_str.replace("Z", "+00:00"))
                if dt.tzinfo:
                    dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
                match.nominal_start_time = dt
                match.scheduled_start_time = dt
            except ValueError:
                if match.schedule_type == ScheduleType.STATBREAK:
                    return jsonify({"error": "Invalid start time for Static Break match."}), 400

    # Team handling
    team1_input = data.get("team1") or ""
    team2_input = data.get("team2") or ""
    if match.schedule_type not in STRUCTURAL_SCHEDULE_TYPES:
        team1_name = str(team1_input).strip()
        team2_name = str(team2_input).strip()
        match.team1_initial = team1_name or None
        match.team2_initial = team2_name or None
        match.team1 = _resolve_initial_to_cached_team(team1_name, tournament_url)
        match.team2 = _resolve_initial_to_cached_team(team2_name, tournament_url)

    # Refs: parallel refs / refs_initial (same slot count). Resolved here but
    # written below after the flush so the match has a uuid the join-table
    # rows can reference. Never allowed on structural types (BREAK/STATBREAK/JOIN).
    refs = data.get("refs")
    refs_csv_pair: tuple[str, str] | None = None
    if refs and isinstance(refs, list) and match.schedule_type not in STRUCTURAL_SCHEDULE_TYPES:
        refs_csv_pair = resolve_refs_slots(refs, tournament_url)

    # Format
    set_type_str = data.get("set_type")
    if set_type_str:
        try:
            match.set_type = SetType(set_type_str)
        except ValueError:
            pass

    if data.get("nsets") is not None:
        match.nsets = int(data.get("nsets"))
    if match.set_type == SetType.STONES and data.get("stones_per_set") is not None:
        match.stones_per_set = int(data.get("stones_per_set"))

    if data.get("ribbon") is not None:
        match.ribbon = bool(data.get("ribbon"))

    match.skip_condition = data.get("skip_condition")

    db.session.add(match)
    db.session.flush()  # Ensure uuid exists before link updates and validation.

    if refs_csv_pair is not None:
        set_match_referees_from_csv(match, refs_csv_pair[0], refs_csv_pair[1])

    # Handle linked list insert (STATBREAK may optionally sit in a chain so
    # downstream matches wait for its end).
    prev_match_id = (
        data.get("previous_match_id")
        if match.schedule_type
        in (
            ScheduleType.SAFE,
            ScheduleType.FAST,
            ScheduleType.STATIC,
            ScheduleType.BREAK,
            ScheduleType.STATBREAK,
            ScheduleType.JOIN,
        )
        else None
    )
    if prev_match_id:
        update_match_previous_link(match, prev_match_id, tournament_url, is_new=True)

    # Dynamic time compute (STATIC and STATBREAK keep their user-supplied anchor)
    if match.schedule_type not in (ScheduleType.STATIC, ScheduleType.STATBREAK):
        match.nominal_start_time = compute_dynamic_match_nominal_start_time(match, tournament_url)
        # Seed the scheduled anchor from the freshly-computed nominal so future
        # recomputations of nominal don't drag time-based dependency edges around.
        if match.nominal_start_time is not None and match.scheduled_start_time is None:
            match.scheduled_start_time = match.nominal_start_time

    ok, err = validate_match_input(match, tournament_url)
    if not ok:
        db.session.rollback()
        return jsonify({"error": err}), 400

    db.session.commit()

    # Recompute
    recompute_scheduled_and_nominal_times(tournament_url)

    return jsonify({"success": True, "uuid": match.uuid})


@bp.route("/tournaments/<tournament_url>/matches/<match_id>", methods=["DELETE"])
@login_required
def delete_match_api(tournament_url, match_id):
    if not _check_to(tournament_url):
        return jsonify({"error": "Forbidden"}), 403

    match = Match.query.filter_by(uuid=match_id, event=tournament_url).first_or_404()

    # Splice this match out of its per-field chain so its neighbours remain linked,
    # then flush so the reconnection persists before we hard-delete the row.
    detach_match_from_chain(match, tournament_url)
    db.session.flush()

    delete_matches_with_children([match_id])
    db.session.commit()
    recompute_scheduled_and_nominal_times(tournament_url)

    return jsonify({"success": True})


@bp.route("/tournaments/<tournament_url>/matches/bulk-length", methods=["POST"])
@login_required
def bulk_match_length_api(tournament_url):
    """Set ``nominal_length`` on many matches at once.

    Body: ``{"match_ids": [...], "length": <minutes>}``. Applies the shared
    length to every editable match, skipping JOINs (structurally zero-length)
    and matches locked by having started (same rule as single-match edit:
    STATBREAKs stay editable even when COMPLETED). Single commit, one
    recompute, per-match results in the response.
    """
    if not _check_to(tournament_url):
        return jsonify({"error": "Forbidden"}), 403

    data = request.get_json()
    if not data:
        return jsonify({"error": "Invalid JSON"}), 400

    match_ids = data.get("match_ids")
    if not isinstance(match_ids, list) or not match_ids:
        return jsonify({"error": "match_ids must be a non-empty list."}), 400

    try:
        length = int(data.get("length"))
    except (TypeError, ValueError):
        return jsonify({"error": "Length must be a number of minutes."}), 400
    if length <= 0:
        return jsonify({"error": "Length must be greater than zero."}), 400

    # Dedup while preserving order so results line up with the request.
    seen: set[str] = set()
    unique_ids: list[str] = []
    for mid in match_ids:
        mid = str(mid or "").strip()
        if mid and mid not in seen:
            seen.add(mid)
            unique_ids.append(mid)
    if not unique_ids:
        return jsonify({"error": "match_ids must be a non-empty list."}), 400

    results = []
    updated = 0
    for mid in unique_ids:
        match = Match.query.filter_by(uuid=mid, event=tournament_url).first()
        if not match:
            results.append({"match_id": mid, "status": "not_found"})
            continue
        if match.schedule_type == ScheduleType.JOIN:
            results.append({"match_id": mid, "status": "skipped_join"})
            continue
        if match.status in _LOCKED_STATUSES and match.schedule_type != ScheduleType.STATBREAK:
            results.append({"match_id": mid, "status": "skipped_locked"})
            continue
        match.nominal_length = length
        updated += 1
        results.append({"match_id": mid, "status": "updated"})

    db.session.commit()
    if updated:
        recompute_scheduled_and_nominal_times(tournament_url)

    return jsonify({"success": True, "updated": updated, "results": results})


# ---------------------------------------------------------------------------
# Structural groups ("break-groups" API): same-name BREAK/STATBREAK/JOIN rows
# across multiple fields, created and edited as one unit (shared name / length /
# start time; JOINs carry only a name — length is always 0). Structural rows
# only occupy fields; they never have teams or refs.
# ---------------------------------------------------------------------------


def _parse_start_time_utc(start_time_str: str | None) -> datetime | None:
    """Parse an ISO datetime (optionally with Z/offset) into naive UTC, or ``None``."""
    if not start_time_str:
        return None
    try:
        dt = datetime.fromisoformat(str(start_time_str).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _break_group_rows(tournament_url: str, name: str) -> list[Match]:
    """All BREAK/STATBREAK/JOIN rows sharing *name* in this tournament."""
    return (
        Match.query.filter_by(event=tournament_url, name=name)
        .filter(Match.schedule_type.in_(STRUCTURAL_SCHEDULE_TYPES))
        .all()
    )


def _field_chain_tail(tournament_url: str, field_name: str, exclude_uuids: set[str] | None = None) -> Match | None:
    """Last match in *field_name*'s doubly-linked chain (no next_match).

    Detached matches (e.g. STATIC rows) also have no next_match; among all
    candidates we pick the one with the latest planned/nominal anchor so a new
    break appended "at the tail" lands after everything currently scheduled.
    """
    exclude_uuids = exclude_uuids or set()
    candidates = [
        m
        for m in Match.query.filter_by(event=tournament_url, field=field_name).all()
        if not m.next_match and m.uuid not in exclude_uuids
    ]
    if not candidates:
        return None

    def sort_key(m: Match):
        anchor = m.scheduled_start_time or m.nominal_start_time
        return (anchor is not None, anchor or datetime.min, m.uuid)

    return max(candidates, key=sort_key)


def _structural_name_collision(tournament_url: str, name: str, field_name: str) -> Match | None:
    """Existing structural match blocking (name, event, field), or ``None``."""
    return (
        Match.query.filter_by(event=tournament_url, name=name, field=field_name)
        .filter(Match.schedule_type.in_(STRUCTURAL_SCHEDULE_TYPES))
        .first()
    )


@bp.route("/tournaments/<tournament_url>/break-groups", methods=["POST"])
@login_required
def create_break_group_api(tournament_url):
    """Create one BREAK/STATBREAK/JOIN row per field, sharing name/length.

    JOIN rows always have ``nominal_length`` 0. Structural rows never carry
    teams — they only occupy fields.
    """
    if not _check_to(tournament_url):
        return jsonify({"error": "Forbidden"}), 403

    data = request.get_json()
    if not data:
        return jsonify({"error": "Invalid JSON"}), 400

    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "Name is required"}), 400
    mn_err = match_name_char_error(name)
    if mn_err:
        return jsonify({"error": mn_err}), 400

    try:
        schedule_type = ScheduleType(data.get("schedule_type") or "BREAK")
    except ValueError:
        schedule_type = None
    if schedule_type not in STRUCTURAL_SCHEDULE_TYPES:
        return jsonify({"error": "schedule_type must be BREAK, STATBREAK, or JOIN."}), 400
    is_join = schedule_type == ScheduleType.JOIN

    if is_join:
        length = 0  # JOIN invariant: zero-length synchronisation point.
    else:
        try:
            length = int(data.get("length"))
        except (TypeError, ValueError):
            return jsonify({"error": "Length (minutes) is required."}), 400
        if length < 0:
            return jsonify({"error": "Length cannot be negative."}), 400

    raw_fields = data.get("fields")
    if not isinstance(raw_fields, list):
        raw_fields = []
    fields, seen = [], set()
    for f in raw_fields:
        f = str(f or "").strip()
        if f and f not in seen:
            seen.add(f)
            fields.append(f)
    if not fields:
        return jsonify({"error": "At least one field is required."}), 400
    known_fields = {f.name for f in Field.query.filter_by(event=tournament_url).all()}
    unknown = [f for f in fields if f not in known_fields]
    if unknown:
        return jsonify({"error": f"Unknown field(s): {', '.join(unknown)}"}), 400

    if data.get("teams"):
        return jsonify({"error": "Breaks and joins cannot have team requirements; they only occupy fields."}), 400

    start_dt = None
    if schedule_type == ScheduleType.STATBREAK:
        start_dt = _parse_start_time_utc(data.get("start_time"))
        if start_dt is None:
            return jsonify({"error": "Start time is required for Static Break groups."}), 400

    previous_map = data.get("previous_match")
    if not isinstance(previous_map, dict):
        previous_map = {}

    for f in fields:
        if _structural_name_collision(tournament_url, name, f):
            return (
                jsonify({"error": f'A break or join named "{name}" already exists on field "{f}".'}),
                400,
            )

    created: list[Match] = []
    for f in fields:
        match = Match(event=tournament_url, name=name)
        match.field = f
        match.schedule_type = schedule_type
        match.nominal_length = length
        match.skip_condition = None
        if start_dt is not None:
            match.nominal_start_time = start_dt
            match.scheduled_start_time = start_dt
        db.session.add(match)
        db.session.flush()
        if schedule_type in (ScheduleType.BREAK, ScheduleType.JOIN):
            prev_id = str(previous_map.get(f) or "").strip()
            if prev_id:
                prev_match = Match.query.filter_by(uuid=prev_id, event=tournament_url).first()
                if not prev_match:
                    db.session.rollback()
                    return jsonify({"error": f'Previous match not found for field "{f}".'}), 400
                if (prev_match.field or "").strip() != f:
                    db.session.rollback()
                    return (
                        jsonify({"error": f'Previous match for field "{f}" must be on that field.'}),
                        400,
                    )
            else:
                # Default: append at the tail of the field's chain.
                tail = _field_chain_tail(tournament_url, f, exclude_uuids={match.uuid})
                prev_match = tail
            if prev_match is not None:
                update_match_previous_link(match, prev_match.uuid, tournament_url, is_new=True)
        created.append(match)

    db.session.flush()
    db.session.commit()
    recompute_scheduled_and_nominal_times(tournament_url)

    return jsonify({"success": True, "name": name, "uuids": [m.uuid for m in created]})


@bp.route("/tournaments/<tournament_url>/break-groups/<name>", methods=["PUT"])
@login_required
def update_break_group_api(tournament_url, name):
    """Edit every same-name structural row at once (length/start_time/fields).

    JOIN groups only support field membership changes; length stays 0. Team
    requirements are rejected for every structural type. The whole group can
    be converted BREAK↔JOIN (JOIN→BREAK requires a length); nothing converts
    to STATBREAK, and a STATBREAK group is locked once its start has passed.
    """
    if not _check_to(tournament_url):
        return jsonify({"error": "Forbidden"}), 403

    rows = _break_group_rows(tournament_url, name)
    if not rows:
        return jsonify({"error": "Break group not found"}), 404

    data = request.get_json()
    if not data:
        return jsonify({"error": "Invalid JSON"}), 400

    group_type = rows[0].schedule_type
    # A completed static break is history: its window has passed, so there is
    # nothing meaningful left to edit. (BREAK/JOIN statuses are solver-derived
    # and re-derived by the recompute below, so they carry no lock.)
    if group_type == ScheduleType.STATBREAK and rows[0].effective_status == MatchStatus.COMPLETED:
        return jsonify({"error": "Static break cannot be edited once its start time has passed."}), 409

    # Whole-group type conversion: BREAK↔JOIN only (both are dynamic field
    # reservations). Nothing converts TO a STATBREAK — create one deliberately
    # with a start time instead.
    convert_to: ScheduleType | None = None
    if data.get("schedule_type") is not None:
        try:
            requested_type = ScheduleType(data.get("schedule_type"))
        except ValueError:
            requested_type = None
        if requested_type is not None and requested_type != group_type:
            if group_type in (ScheduleType.BREAK, ScheduleType.JOIN) and requested_type in (
                ScheduleType.BREAK,
                ScheduleType.JOIN,
            ):
                convert_to = requested_type
            elif requested_type == ScheduleType.STATBREAK:
                return jsonify(
                    {"error": "Breaks cannot be converted to static breaks; create a new static break instead."}
                ), 400
            else:
                return jsonify({"error": "A group's schedule type cannot be changed here."}), 400

    effective_type = convert_to or group_type
    is_join = effective_type == ScheduleType.JOIN

    length = data.get("length")
    if is_join:
        length = 0 if convert_to is not None else None  # JOIN invariant: length is 0.
    elif convert_to == ScheduleType.BREAK and length is None:
        # JOIN rows carry length 0; a break needs a real duration.
        return jsonify({"error": "Length (minutes) is required when converting a join to a break."}), 400
    elif length is not None:
        try:
            length = int(length)
        except (TypeError, ValueError):
            return jsonify({"error": "Length must be a number of minutes."}), 400
        if length < 0:
            return jsonify({"error": "Length cannot be negative."}), 400

    if data.get("teams"):
        return jsonify({"error": "Breaks and joins cannot have team requirements; they only occupy fields."}), 400

    start_dt = None
    if data.get("start_time") is not None:
        if group_type != ScheduleType.STATBREAK:
            start_dt = None  # start_time only applies to STATBREAK groups
        else:
            start_dt = _parse_start_time_utc(data.get("start_time"))
            if start_dt is None:
                return jsonify({"error": "Invalid start time."}), 400

    fields = data.get("fields")
    if fields is not None:
        if not isinstance(fields, list):
            return jsonify({"error": "fields must be a list of field names."}), 400
        normalized, seen = [], set()
        for f in fields:
            f = str(f or "").strip()
            if f and f not in seen:
                seen.add(f)
                normalized.append(f)
        fields = normalized
        if not fields:
            return (
                jsonify({"error": "A break group needs at least one field. Use DELETE to remove the group."}),
                400,
            )
        known_fields = {f.name for f in Field.query.filter_by(event=tournament_url).all()}
        unknown = [f for f in fields if f not in known_fields]
        if unknown:
            return jsonify({"error": f"Unknown field(s): {', '.join(unknown)}"}), 400

    # Shared edits on existing rows.
    for m in rows:
        if convert_to is not None:
            m.schedule_type = convert_to
        if length is not None:
            m.nominal_length = length
        if start_dt is not None:
            m.nominal_start_time = start_dt
            m.scheduled_start_time = start_dt

    # Field membership changes: create/delete rows.
    if fields is not None:
        current = {(m.field or "").strip(): m for m in rows}
        template = rows[0]
        to_add = [f for f in fields if f not in current]
        to_remove = [m for f, m in current.items() if f not in fields]

        for f in to_add:
            if _structural_name_collision(tournament_url, name, f):
                db.session.rollback()
                return (
                    jsonify({"error": f'A break or join named "{name}" already exists on field "{f}".'}),
                    400,
                )
            match = Match(event=tournament_url, name=name)
            match.field = f
            match.schedule_type = effective_type
            match.nominal_length = length if length is not None else template.nominal_length
            match.skip_condition = None
            if effective_type == ScheduleType.STATBREAK:
                match.nominal_start_time = template.nominal_start_time
                match.scheduled_start_time = template.scheduled_start_time
            db.session.add(match)
            db.session.flush()
            if effective_type in (ScheduleType.BREAK, ScheduleType.JOIN):
                tail = _field_chain_tail(tournament_url, f, exclude_uuids={match.uuid})
                if tail is not None:
                    update_match_previous_link(match, tail.uuid, tournament_url, is_new=True)

        for m in to_remove:
            detach_match_from_chain(m, tournament_url)
        if to_remove:
            db.session.flush()
            delete_matches_with_children([m.uuid for m in to_remove])

    db.session.flush()
    db.session.commit()
    recompute_scheduled_and_nominal_times(tournament_url)

    return jsonify({"success": True})


@bp.route("/tournaments/<tournament_url>/break-groups/<name>", methods=["DELETE"])
@login_required
def delete_break_group_api(tournament_url, name):
    """Delete every same-name structural row in the group."""
    if not _check_to(tournament_url):
        return jsonify({"error": "Forbidden"}), 403

    rows = _break_group_rows(tournament_url, name)
    if not rows:
        return jsonify({"error": "Break group not found"}), 404

    for m in rows:
        detach_match_from_chain(m, tournament_url)
    db.session.flush()
    delete_matches_with_children([m.uuid for m in rows])
    db.session.commit()
    recompute_scheduled_and_nominal_times(tournament_url)

    return jsonify({"success": True})


@bp.route("/tournaments/<tournament_url>/fields", methods=["POST"])
@login_required
def create_field_api(tournament_url):
    if not _check_to(tournament_url):
        return jsonify({"error": "Forbidden"}), 403

    data = request.get_json()
    if not data or "name" not in data:
        return jsonify({"error": "Name required"}), 400

    name = data["name"].strip()
    if not name:
        return jsonify({"error": "Name required"}), 400

    if Field.query.filter_by(event=tournament_url, name=name).first():
        return jsonify({"error": "Field already exists"}), 400

    field = Field(event=tournament_url, name=name)
    camera_urls = [url for url in data.get("camera_urls", []) if url.strip()]
    if camera_urls:
        field.camera = json.dumps(camera_urls)

    db.session.add(field)
    db.session.commit()
    return jsonify({"success": True, "id": field.id})


@bp.route("/tournaments/<tournament_url>/fields/<int:field_id>", methods=["DELETE"])
@login_required
def delete_field_api(tournament_url, field_id):
    if not _check_to(tournament_url):
        return jsonify({"error": "Forbidden"}), 403

    field = Field.query.filter_by(id=field_id, event=tournament_url).first_or_404()

    # Check usage
    if Match.query.filter_by(event=tournament_url, field=field.name).first():
        return jsonify({"error": "Cannot delete field with matches"}), 400

    db.session.delete(field)
    db.session.commit()
    return jsonify({"success": True})


@bp.route("/tournaments/<tournament_url>/tags", methods=["POST"])
@login_required
def create_tag_api(tournament_url):
    if not _check_to(tournament_url):
        return jsonify({"error": "Forbidden"}), 403

    data = request.get_json()
    if not data or "name" not in data:
        return jsonify({"error": "Name required"}), 400

    name = data["name"].strip()
    if not name:
        return jsonify({"error": "Name required"}), 400
    if "::" in name:
        return jsonify({"error": 'Tag name cannot contain "::"'}), 400

    if Tag.query.filter_by(event=tournament_url, name=name).first():
        return jsonify({"error": "Tag already exists"}), 400

    tag = Tag(event=tournament_url, name=name)
    db.session.add(tag)
    db.session.commit()
    return jsonify({"success": True, "id": tag.id})


@bp.route("/tournaments/<tournament_url>/tags/<int:tag_id>", methods=["DELETE"])
@login_required
def delete_tag_api(tournament_url, tag_id):
    if not _check_to(tournament_url):
        return jsonify({"error": "Forbidden"}), 403

    tag = Tag.query.filter_by(id=tag_id, event=tournament_url).first_or_404()
    used = _tag_usage(tournament_url, tag.name)
    if used:
        return (
            jsonify(
                {
                    "error": f'Cannot delete tag "{tag.name}": it is used in '
                    + ", ".join(used[:5])
                    + (" (and possibly more)" if len(used) > 5 else "")
                }
            ),
            400,
        )
    # Scrub any bracket/diagram soft refs (not covered by schedule usage check).
    from app.services.bracket_cleanup_service import scrub_deleted_tags

    scrub_deleted_tags(tournament_url, [tag.name])
    db.session.delete(tag)
    db.session.commit()
    return jsonify({"success": True})


@bp.route("/tournaments/<tournament_url>/tags/<int:tag_id>", methods=["PUT"])
@login_required
def update_tag_api(tournament_url, tag_id):
    if not _check_to(tournament_url):
        return jsonify({"error": "Forbidden"}), 403
    tag = Tag.query.filter_by(id=tag_id, event=tournament_url).first_or_404()
    data = request.get_json()
    if not data or "name" not in data:
        return jsonify({"error": "Name required"}), 400
    tag.name = data["name"]
    db.session.commit()
    return jsonify({"success": True})
