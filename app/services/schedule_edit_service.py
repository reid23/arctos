"""Organizer edits to a tournament's schedule, one match or break group at a time.

Creates, edits, and deletes matches and structural groups (same-name
BREAK/STATBREAK/JOIN rows across fields), sets many match lengths at once,
pushes back unstarted matches, and re-applies finished matches' results to
the matches that depend on them. Every write re-solves the schedule.

Callers authorize the organizer first; both the SPA's session routes in
:mod:`app.routes.tournaments.matches_admin` / :mod:`~app.routes.tournaments.scheduling`
and the public API's ``manage`` routes share these methods. Request bodies
are the same JSON objects for both, passed through as ``data``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone

from sqlalchemy.orm.attributes import flag_modified

from app.domain.enums import STRUCTURAL_SCHEDULE_TYPES, MatchStatus, ScheduleType, SetType
from app.error_values import Err, Ok, Result, Some
from app.exceptions import ArctosError, NotFoundError, ValidationError
from app.services.dual_write import clear_match_referees, set_match_referees_from_csv
from app.utils.dependencies import apply_match_dependencies
from app.utils.helpers import resolve_match_winner_loser_ref, resolve_tag_to_team, resolve_team_name_to_id
from app.utils.match_chain import delete_matches_with_children, detach_match_from_chain, update_match_previous_link
from app.utils.match_ref_resolution import refs_string_to_tokens, resolve_refs_slots
from app.utils.name_validation import match_name_char_error
from app.utils.datetime_helpers import now_utc_naive
from app.utils.scheduling import (
    compute_dynamic_match_nominal_start_time,
    push_back_unstarted_matches,
    recompute_scheduled_and_nominal_times,
    validate_match_input,
)
from models import Field, Match, db

# Statuses where a match has already started and is no longer editable.
LOCKED_STATUSES = (MatchStatus.IN_PROGRESS, MatchStatus.COMPLETED, MatchStatus.SKIPPED)

# Allowed schedule type transitions when editing (only these target types are allowed from each source).
ALLOWED_SCHEDULE_TYPE_TRANSITIONS = {
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

STRUCTURAL_TEAMS_ERROR = "Breaks and joins cannot have team requirements; they only occupy fields."


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


def _statbreak_start_passed(row: Match) -> bool:
    """True when a STATBREAK's scheduled start is in the past (edit lock)."""
    start = row.nominal_start_time or row.scheduled_start_time
    if start is None:
        return False
    return now_utc_naive() >= start


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


def _structural_name_type_conflict(tournament_url: str, name: str, schedule_type: ScheduleType) -> Match | None:
    """Existing same-name structural row with a different schedule type, or ``None``.

    Display name is the group key, so BREAK ``Lunch`` and JOIN ``Lunch`` must
    not coexist in one tournament.
    """
    return (
        Match.query.filter_by(event=tournament_url, name=name)
        .filter(Match.schedule_type.in_(STRUCTURAL_SCHEDULE_TYPES))
        .filter(Match.schedule_type != schedule_type)
        .first()
    )


def _name_type_conflict_error(name: str, type_conflict: Match) -> ArctosError:
    return ValidationError(
        f'Name "{name}" is already used by a '
        f"{type_conflict.schedule_type.value} group; "
        "structural types cannot share a name."
    )


def _previous_match_error(tournament_url: str, previous_match_id: str, field_name: str) -> ArctosError | None:
    """Why *previous_match_id* can't precede a match on *field_name*, or ``None`` when it can."""
    if not field_name:
        return ValidationError("Field is required when using a previous match.")
    previous_match = Match.query.filter_by(uuid=previous_match_id, event=tournament_url).first()
    if not previous_match:
        return ValidationError("Previous match not found.")
    if (previous_match.field or "").strip() != field_name:
        return ValidationError("Previous match must be on the same field.")
    return None


def _distinct_field_names(raw_fields: list) -> list[str]:
    fields, seen = [], set()
    for f in raw_fields:
        f = str(f or "").strip()
        if f and f not in seen:
            seen.add(f)
            fields.append(f)
    return fields


def _unknown_fields_error(tournament_url: str, fields: list[str]) -> ArctosError | None:
    known_fields = {f.name for f in Field.query.filter_by(event=tournament_url).all()}
    unknown = [f for f in fields if f not in known_fields]
    if unknown:
        return ValidationError(f"Unknown field(s): {', '.join(unknown)}")
    return None


@dataclass(frozen=True)
class ScheduleEditService:
    """Organizer schedule edits. All methods are static; the class acts as a typed namespace."""

    @staticmethod
    def create_match(tournament_url: str, data: dict) -> Result[Match, ArctosError]:
        """Create one match (or a single-field break/join) from the schedule editor's JSON."""
        name = data.get("name")
        if not name:
            return Err(ValidationError("Match name is required"))
        match match_name_char_error(name.strip()):
            case Some(error):
                return Err(ValidationError(error))

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
        # Same display name must not be reused across different structural types.
        if schedule_type in STRUCTURAL_SCHEDULE_TYPES:
            type_conflict = _structural_name_type_conflict(tournament_url, name.strip(), schedule_type)
            if type_conflict:
                return Err(_name_type_conflict_error(name.strip(), type_conflict))
            existing = _structural_name_collision(tournament_url, name.strip(), effective_field)
        else:
            existing = Match.query.filter_by(event=tournament_url, name=name.strip()).first()
        if existing:
            if schedule_type in STRUCTURAL_SCHEDULE_TYPES:
                return Err(
                    ValidationError(f"A {schedule_type.value} match with this name already exists on this field.")
                )
            return Err(ValidationError("Match name already exists"))

        match = Match(event=tournament_url, name=name)
        match.field = data.get("field")
        match.schedule_type = schedule_type

        # Structural rows match the break-groups API: field required, length rules,
        # no teams/refs/skip_condition.
        if match.schedule_type in STRUCTURAL_SCHEDULE_TYPES:
            if not effective_field:
                return Err(ValidationError("Field is required for breaks and joins."))
            if (data.get("team1") or "").strip() or (data.get("team2") or "").strip():
                return Err(ValidationError(STRUCTURAL_TEAMS_ERROR))
            refs = data.get("refs")
            if refs:
                if isinstance(refs, list) and any(str(r or "").strip() for r in refs):
                    return Err(ValidationError(STRUCTURAL_TEAMS_ERROR))
                if isinstance(refs, str) and refs.strip():
                    return Err(ValidationError(STRUCTURAL_TEAMS_ERROR))
            if (data.get("skip_condition") or "").strip():
                return Err(ValidationError("Breaks and joins cannot have a skip condition."))
            if match.schedule_type == ScheduleType.JOIN:
                match.nominal_length = 0
            else:
                try:
                    length = int(data.get("length"))
                except (TypeError, ValueError):
                    return Err(ValidationError("Length (minutes) is required."))
                if length < 0:
                    return Err(ValidationError("Length cannot be negative."))
                match.nominal_length = length
            match.skip_condition = None
        else:
            match.nominal_length = int(data.get("length")) if data.get("length") is not None else None

        # BREAK, JOIN, FAST, SAFE require non-empty previous_match on same field
        if match.schedule_type in (
            ScheduleType.BREAK,
            ScheduleType.JOIN,
            ScheduleType.FAST,
            ScheduleType.SAFE,
        ):
            previous_match_id = (data.get("previous_match_id") or "").strip()
            if not previous_match_id:
                return Err(ValidationError("Previous match is required for Break, Join, Fast, and Safe matches."))
            if (
                error := _previous_match_error(tournament_url, previous_match_id, (match.field or "").strip())
            ) is not None:
                return Err(error)

        if match.schedule_type in (ScheduleType.STATIC, ScheduleType.STATBREAK):
            start_time_str = data.get("start_time")
            if not start_time_str and match.schedule_type == ScheduleType.STATBREAK:
                return Err(ValidationError("Start time is required for Static Break matches."))
            if start_time_str:
                try:
                    dt = datetime.fromisoformat(start_time_str.replace("Z", "+00:00"))
                    if dt.tzinfo:
                        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
                    match.nominal_start_time = dt
                    match.scheduled_start_time = dt
                except ValueError:
                    if match.schedule_type == ScheduleType.STATBREAK:
                        return Err(ValidationError("Invalid start time for Static Break match."))

        if match.schedule_type not in STRUCTURAL_SCHEDULE_TYPES:
            team1_name = str(data.get("team1") or "").strip()
            team2_name = str(data.get("team2") or "").strip()
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

        if match.schedule_type not in STRUCTURAL_SCHEDULE_TYPES:
            match.skip_condition = data.get("skip_condition")

        db.session.add(match)
        db.session.flush()  # Ensure uuid exists before link updates and validation.

        if refs_csv_pair is not None:
            set_match_referees_from_csv(match, refs_csv_pair[0], refs_csv_pair[1])

        # Handle linked list insert (STATBREAK may optionally sit in a chain so
        # downstream matches wait for its end). Same-field validation for any type
        # that sets a predecessor here.
        previous_match_id = str(data.get("previous_match_id") or "").strip()
        if previous_match_id:
            if (
                error := _previous_match_error(tournament_url, previous_match_id, (match.field or "").strip())
            ) is not None:
                db.session.rollback()
                return Err(error)
            update_match_previous_link(match, previous_match_id, tournament_url, is_new=True)

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
            return Err(ValidationError(err))

        db.session.commit()
        recompute_scheduled_and_nominal_times(tournament_url)
        return Ok(match)

    @staticmethod
    def update_match(tournament_url: str, match_id: str, data: dict) -> Result[None, ArctosError]:
        """Edit one match. Its name is immutable; a ``name`` in *data* is ignored."""
        match = Match.query.filter_by(uuid=match_id, event=tournament_url).first()
        if match is None:
            return Err(NotFoundError("Match not found"))
        # STATBREAK status is time-derived (nobody "starts" them): locked once the
        # scheduled start has passed, editable before that. (effective_status also
        # flips to COMPLETED at that same moment.)
        if match.schedule_type == ScheduleType.STATBREAK:
            if _statbreak_start_passed(match):
                return Err(
                    ArctosError("Static break cannot be edited once its start time has passed.", status_code=409)
                )
        elif match.status in LOCKED_STATUSES:
            return Err(
                ArctosError(
                    f"Match cannot be edited once it has started (current status: {match.status.value}).",
                    status_code=409,
                )
            )

        field = data.get("field")
        schedule_type_str = data.get("schedule_type")
        length = data.get("length")
        start_time_str = data.get("start_time")
        previous_match_id = data.get("previous_match_id")
        refs = data.get("refs")
        team1_input = data.get("team1")
        team2_input = data.get("team2")
        set_type_str = data.get("set_type")
        nsets = data.get("nsets")
        stones_per_set = data.get("stones_per_set")
        ribbon = data.get("ribbon")
        skip_condition = data.get("skip_condition")

        # Schedule Type (apply first so name uniqueness uses the new type)
        if schedule_type_str:
            try:
                new_schedule_type = ScheduleType(schedule_type_str)
                current_schedule_type = match.schedule_type
                allowed = ALLOWED_SCHEDULE_TYPE_TRANSITIONS.get(current_schedule_type, (current_schedule_type,))
                if new_schedule_type not in allowed:
                    return Err(
                        ValidationError(
                            f"Match type cannot be changed from {current_schedule_type.value} to {new_schedule_type.value}. "
                            "Allowed changes: Static→Safe/Fast, Safe→Fast, Break↔Join, Static Break→Break."
                        )
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
                    return Err(
                        ValidationError(
                            "This break/join spans multiple fields; convert the whole group via the group editor."
                        )
                    )
                match.schedule_type = new_schedule_type
            except ValueError:
                pass  # Ignore invalid enum

        if field is not None:  # field can be empty string/null
            match.field = field

        # Match-name uniqueness check (still useful when only `field` changes for
        # structural matches, whose names are unique per field across BREAK/STATBREAK/JOIN).
        if field is not None:
            effective_name = (match.name or "").strip()
            effective_field = (match.field or "").strip()
            if effective_name and match.schedule_type in STRUCTURAL_SCHEDULE_TYPES:
                existing_name = (
                    Match.query.filter_by(event=tournament_url, name=effective_name, field=effective_field)
                    .filter(Match.schedule_type.in_(STRUCTURAL_SCHEDULE_TYPES), Match.uuid != match.uuid)
                    .first()
                )
                if existing_name:
                    return Err(
                        ValidationError(
                            f"A {match.schedule_type.value} match with this name already exists on this field."
                        )
                    )

        # Structural matches (BREAK/STATBREAK/JOIN) never have playing teams or
        # refs — they only occupy fields. Match the break-groups API: reject
        # team/ref/skip payloads rather than silently dropping them.
        if match.schedule_type in STRUCTURAL_SCHEDULE_TYPES:
            if (team1_input is not None and str(team1_input).strip()) or (
                team2_input is not None and str(team2_input).strip()
            ):
                return Err(ValidationError(STRUCTURAL_TEAMS_ERROR))
            if refs is not None:
                if isinstance(refs, list):
                    has_refs = any(str(r or "").strip() for r in refs)
                else:
                    has_refs = bool(str(refs).strip())
                if has_refs:
                    return Err(ValidationError(STRUCTURAL_TEAMS_ERROR))
            if skip_condition is not None and str(skip_condition).strip():
                return Err(ValidationError("Breaks and joins cannot have a skip condition."))
            match.team1 = None
            match.team1_initial = None
            match.team2 = None
            match.team2_initial = None
            clear_match_referees(match)
            match.skip_condition = None
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
                tokens = refs if isinstance(refs, list) else refs_string_to_tokens(refs)
                refs_csv, refs_initial_csv = resolve_refs_slots(tokens, tournament_url)
                set_match_referees_from_csv(match, refs_csv, refs_initial_csv)

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

        if match.schedule_type == ScheduleType.JOIN:
            match.nominal_length = 0
        elif length is not None:
            try:
                length_minutes = int(length)
            except (TypeError, ValueError):
                return Err(ValidationError("Length must be an integer number of minutes."))
            if length_minutes < 0:
                return Err(ValidationError("Length cannot be negative."))
            match.nominal_length = length_minutes

        # Skip Condition (only for SAFE/FAST)
        if match.schedule_type not in STRUCTURAL_SCHEDULE_TYPES and skip_condition is not None:
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
            required_previous_id = (previous_match_id or "").strip() if previous_match_id is not None else ""
            if not required_previous_id:
                return Err(ValidationError("Previous match is required for Break, Join, Fast, and Safe matches."))
            effective_field = match.field or ""
            if not effective_field:
                return Err(ValidationError("Field is required when using a previous match."))
            if (
                error := _previous_match_error(tournament_url, required_previous_id, effective_field.strip())
            ) is not None:
                return Err(error)

        if match.schedule_type == ScheduleType.STATIC:
            if start_time_str:
                try:
                    # Handle ISO format (potentially with Z or offset)
                    dt = datetime.fromisoformat(start_time_str.replace("Z", "+00:00"))
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
            # timelines and the solver never moves it. Like STATIC it does not require
            # a predecessor; an optional previous_match may still place it in a field
            # chain so matches after it wait for its end.
            if start_time_str:
                try:
                    dt = datetime.fromisoformat(start_time_str.replace("Z", "+00:00"))
                    if dt.tzinfo:
                        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
                    match.nominal_start_time = dt
                    match.scheduled_start_time = dt
                except ValueError:
                    pass
            if previous_match_id is not None:
                optional_previous_id = str(previous_match_id).strip()
                if optional_previous_id:
                    error = _previous_match_error(tournament_url, optional_previous_id, (match.field or "").strip())
                    if error is not None:
                        return Err(error)
                    update_match_previous_link(match, optional_previous_id, tournament_url)
                else:
                    detach_match_from_chain(match, tournament_url)
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
            return Err(ValidationError(err))

        db.session.flush()  # Emit UPDATE for previous_match etc. before commit
        db.session.commit()
        recompute_scheduled_and_nominal_times(tournament_url)
        return Ok(None)

    @staticmethod
    def delete_match(tournament_url: str, match_id: str) -> Result[None, ArctosError]:
        match = Match.query.filter_by(uuid=match_id, event=tournament_url).first()
        if match is None:
            return Err(NotFoundError("Match not found"))

        # Splice this match out of its per-field chain so its neighbours remain linked,
        # then flush so the reconnection persists before we hard-delete the row.
        detach_match_from_chain(match, tournament_url)
        db.session.flush()

        delete_matches_with_children([match_id])
        db.session.commit()
        recompute_scheduled_and_nominal_times(tournament_url)
        return Ok(None)

    @staticmethod
    def set_match_lengths(tournament_url: str, data: dict) -> Result[dict, ArctosError]:
        """Set ``nominal_length`` on many matches at once.

        Body: ``{"match_ids": [...], "length": <minutes>}``. Applies the shared
        length to every editable match. Selecting any row of a multi-field
        BREAK/STATBREAK group expands to the whole same-name group so durations
        stay in sync. Skips JOINs (structurally zero-length), started matches, and
        STATBREAKs whose start has passed (same lock as the group endpoint).
        Single commit, one recompute. Returns ``{"updated", "results"}`` with a
        status per match.
        """
        match_ids = data.get("match_ids")
        if not isinstance(match_ids, list) or not match_ids:
            return Err(ValidationError("match_ids must be a non-empty list."))

        try:
            length = int(data.get("length"))
        except (TypeError, ValueError):
            return Err(ValidationError("Length must be a number of minutes."))
        if length <= 0:
            return Err(ValidationError("Length must be greater than zero."))

        # Dedup while preserving order so results line up with the request.
        seen: set[str] = set()
        unique_ids: list[str] = []
        for match_id in match_ids:
            match_id = str(match_id or "").strip()
            if match_id and match_id not in seen:
                seen.add(match_id)
                unique_ids.append(match_id)
        if not unique_ids:
            return Err(ValidationError("match_ids must be a non-empty list."))

        # Expand BREAK/STATBREAK selections to every same-name structural row so a
        # bulk length change cannot desync a multi-field group.
        expanded_ids: list[str] = []
        expanded_seen: set[str] = set()
        for match_id in unique_ids:
            match = Match.query.filter_by(uuid=match_id, event=tournament_url).first()
            if match is not None and match.schedule_type in (ScheduleType.BREAK, ScheduleType.STATBREAK) and match.name:
                for sibling in _break_group_rows(tournament_url, match.name):
                    if sibling.uuid not in expanded_seen:
                        expanded_seen.add(sibling.uuid)
                        expanded_ids.append(sibling.uuid)
            elif match_id not in expanded_seen:
                expanded_seen.add(match_id)
                expanded_ids.append(match_id)

        results = []
        updated = 0
        for match_id in expanded_ids:
            match = Match.query.filter_by(uuid=match_id, event=tournament_url).first()
            if not match:
                results.append({"match_id": match_id, "status": "not_found"})
                continue
            if match.schedule_type == ScheduleType.JOIN:
                results.append({"match_id": match_id, "status": "skipped_join"})
                continue
            if match.schedule_type == ScheduleType.STATBREAK and _statbreak_start_passed(match):
                results.append({"match_id": match_id, "status": "skipped_locked"})
                continue
            if match.status in LOCKED_STATUSES:
                results.append({"match_id": match_id, "status": "skipped_locked"})
                continue
            match.nominal_length = length
            updated += 1
            results.append({"match_id": match_id, "status": "updated"})

        db.session.commit()
        if updated:
            recompute_scheduled_and_nominal_times(tournament_url)
        return Ok({"updated": updated, "results": results})

    # Structural groups ("break-groups"): same-name BREAK/STATBREAK/JOIN rows
    # across multiple fields, created and edited as one unit (shared name / length /
    # start time; JOINs carry only a name — length is always 0). Structural rows
    # only occupy fields; they never have teams or refs. Group identity is the
    # display name (``/`` is rejected by match_name_char_error so route segments work).

    @staticmethod
    def create_break_group(tournament_url: str, data: dict) -> Result[list[Match], ArctosError]:
        """Create one BREAK/STATBREAK/JOIN row per field, sharing name/length.

        JOIN rows always have ``nominal_length`` 0. Structural rows never carry
        teams — they only occupy fields.
        """
        name = (data.get("name") or "").strip()
        if not name:
            return Err(ValidationError("Name is required"))
        match match_name_char_error(name):
            case Some(error):
                return Err(ValidationError(error))

        try:
            schedule_type = ScheduleType(data.get("schedule_type") or "BREAK")
        except ValueError:
            schedule_type = None
        if schedule_type not in STRUCTURAL_SCHEDULE_TYPES:
            return Err(ValidationError("schedule_type must be BREAK, STATBREAK, or JOIN."))

        if schedule_type == ScheduleType.JOIN:
            length = 0  # JOIN invariant: zero-length synchronisation point.
        else:
            try:
                length = int(data.get("length"))
            except (TypeError, ValueError):
                return Err(ValidationError("Length (minutes) is required."))
            if length < 0:
                return Err(ValidationError("Length cannot be negative."))

        raw_fields = data.get("fields")
        fields = _distinct_field_names(raw_fields if isinstance(raw_fields, list) else [])
        if not fields:
            return Err(ValidationError("At least one field is required."))
        if (error := _unknown_fields_error(tournament_url, fields)) is not None:
            return Err(error)

        if data.get("teams"):
            return Err(ValidationError(STRUCTURAL_TEAMS_ERROR))

        start_time = None
        if schedule_type == ScheduleType.STATBREAK:
            start_time = _parse_start_time_utc(data.get("start_time"))
            if start_time is None:
                return Err(ValidationError("Start time is required for Static Break groups."))

        previous_map = data.get("previous_match")
        if not isinstance(previous_map, dict):
            previous_map = {}

        type_conflict = _structural_name_type_conflict(tournament_url, name, schedule_type)
        if type_conflict:
            return Err(_name_type_conflict_error(name, type_conflict))

        for f in fields:
            if _structural_name_collision(tournament_url, name, f):
                return Err(ValidationError(f'A break or join named "{name}" already exists on field "{f}".'))

        created: list[Match] = []
        for f in fields:
            match = Match(event=tournament_url, name=name)
            match.field = f
            match.schedule_type = schedule_type
            match.nominal_length = length
            match.skip_condition = None
            if start_time is not None:
                match.nominal_start_time = start_time
                match.scheduled_start_time = start_time
            db.session.add(match)
            db.session.flush()
            if schedule_type in (ScheduleType.BREAK, ScheduleType.JOIN):
                previous_match_id = str(previous_map.get(f) or "").strip()
                if previous_match_id:
                    previous_match = Match.query.filter_by(uuid=previous_match_id, event=tournament_url).first()
                    if not previous_match:
                        db.session.rollback()
                        return Err(ValidationError(f'Previous match not found for field "{f}".'))
                    if (previous_match.field or "").strip() != f:
                        db.session.rollback()
                        return Err(ValidationError(f'Previous match for field "{f}" must be on that field.'))
                else:
                    # Default: append at the tail of the field's chain.
                    previous_match = _field_chain_tail(tournament_url, f, exclude_uuids={match.uuid})
                if previous_match is not None:
                    update_match_previous_link(match, previous_match.uuid, tournament_url, is_new=True)
            created.append(match)

        db.session.flush()
        db.session.commit()
        recompute_scheduled_and_nominal_times(tournament_url)
        return Ok(created)

    @staticmethod
    def update_break_group(tournament_url: str, name: str, data: dict) -> Result[None, ArctosError]:
        """Edit every same-name structural row at once (length/start_time/fields).

        JOIN groups only support field membership changes; length stays 0. Team
        requirements are rejected for every structural type. The whole group can
        be converted BREAK↔JOIN (JOIN→BREAK requires a length); nothing converts
        to STATBREAK, and a STATBREAK group is locked once its start has passed.
        """
        rows = _break_group_rows(tournament_url, name)
        if not rows:
            return Err(NotFoundError("Break group not found"))

        if len({m.schedule_type for m in rows}) != 1:
            return Err(ArctosError("Break group has mixed schedule types; cannot edit.", status_code=409))
        group_type = rows[0].schedule_type
        # Lock once the static break's start has passed (same moment effective_status
        # becomes COMPLETED — the break is history for editing).
        if group_type == ScheduleType.STATBREAK and _statbreak_start_passed(rows[0]):
            return Err(ArctosError("Static break cannot be edited once its start time has passed.", status_code=409))

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
                    return Err(
                        ValidationError(
                            "Breaks cannot be converted to static breaks; create a new static break instead."
                        )
                    )
                else:
                    return Err(ValidationError("A group's schedule type cannot be changed here."))

        effective_type = convert_to or group_type

        length = data.get("length")
        if effective_type == ScheduleType.JOIN:
            length = 0 if convert_to is not None else None  # JOIN invariant: length is 0.
        elif convert_to == ScheduleType.BREAK and length is None:
            # JOIN rows carry length 0; a break needs a real duration.
            return Err(ValidationError("Length (minutes) is required when converting a join to a break."))
        elif length is not None:
            try:
                length = int(length)
            except (TypeError, ValueError):
                return Err(ValidationError("Length must be a number of minutes."))
            if length < 0:
                return Err(ValidationError("Length cannot be negative."))

        if data.get("teams"):
            return Err(ValidationError(STRUCTURAL_TEAMS_ERROR))

        # start_time only applies to STATBREAK groups
        start_time = None
        if data.get("start_time") is not None and group_type == ScheduleType.STATBREAK:
            start_time = _parse_start_time_utc(data.get("start_time"))
            if start_time is None:
                return Err(ValidationError("Invalid start time."))

        fields = data.get("fields")
        if fields is not None:
            if not isinstance(fields, list):
                return Err(ValidationError("fields must be a list of field names."))
            fields = _distinct_field_names(fields)
            if not fields:
                return Err(ValidationError("A break group needs at least one field. Use DELETE to remove the group."))
            if (error := _unknown_fields_error(tournament_url, fields)) is not None:
                return Err(error)

        # Shared edits on existing rows.
        for m in rows:
            if convert_to is not None:
                m.schedule_type = convert_to
            if length is not None:
                m.nominal_length = length
            if start_time is not None:
                m.nominal_start_time = start_time
                m.scheduled_start_time = start_time

        # Field membership changes: create/delete rows.
        if fields is not None:
            current = {(m.field or "").strip(): m for m in rows}
            template = rows[0]
            to_add = [f for f in fields if f not in current]
            to_remove = [m for f, m in current.items() if f not in fields]

            for f in to_add:
                if _structural_name_collision(tournament_url, name, f):
                    db.session.rollback()
                    return Err(ValidationError(f'A break or join named "{name}" already exists on field "{f}".'))
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
        return Ok(None)

    @staticmethod
    def delete_break_group(tournament_url: str, name: str) -> Result[None, ArctosError]:
        """Delete every same-name structural row in the group."""
        rows = _break_group_rows(tournament_url, name)
        if not rows:
            return Err(NotFoundError("Break group not found"))

        for m in rows:
            detach_match_from_chain(m, tournament_url)
        db.session.flush()
        delete_matches_with_children([m.uuid for m in rows])
        db.session.commit()
        recompute_scheduled_and_nominal_times(tournament_url)
        return Ok(None)

    @staticmethod
    def push_back_matches(tournament_url: str, data: dict) -> Result[int, ArctosError]:
        """Push unstarted STATIC/STATBREAK start times on one local day by ``minutes``.

        Body: ``{"minutes", "day": "YYYY-MM-DD", "tz_offset_minutes"}``. Returns
        how many matches moved; ``minutes`` of 0 changes nothing.
        """
        try:
            minutes = int(data.get("minutes", 0))
        except (TypeError, ValueError):
            return Err(ValidationError("Invalid number of minutes"))
        if not minutes:
            return Ok(0)

        day_text = (data.get("day") or "").strip()
        if not day_text:
            return Err(ValidationError("day is required (YYYY-MM-DD)."))
        try:
            day = date.fromisoformat(day_text)
        except ValueError:
            return Err(ValidationError("day must be YYYY-MM-DD."))
        try:
            tz_offset_minutes = int(data.get("tz_offset_minutes", 0))
        except (TypeError, ValueError):
            return Err(ValidationError("Invalid tz_offset_minutes"))

        return Ok(push_back_unstarted_matches(tournament_url, minutes, day, tz_offset_minutes=tz_offset_minutes))

    @staticmethod
    def update_all_references(tournament_url: str) -> None:
        """Re-apply every finished match's result to the matches that reference it."""
        finished = (
            Match.query.filter_by(event=tournament_url)
            .filter(Match.status.in_([MatchStatus.COMPLETED, MatchStatus.SKIPPED]))
            .all()
        )
        for match in finished:
            apply_match_dependencies(tournament_url, match)
