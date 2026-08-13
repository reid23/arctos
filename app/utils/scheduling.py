"""
Scheduling logic for matches using the in-memory MatchGraph.

Implements PROCEDURE (per-match scheduling). Same flow on match create/edit
and on match start/end: build graph, topological sort, apply PROCEDURE, write back.
SAFE = finalize start time when last dependency is started.
FAST = finalize when all dependencies are completed.
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from app.domain.enums import (
    BREAK_SCHEDULE_TYPES,
    STRUCTURAL_SCHEDULE_TYPES,
    MatchStatus,
    ScheduleType,
)
from app.models.base import db
from app.utils.MatchGraph import (
    MatchGraph,
    MatchGraphNode,
    _match_participant_team_ids,
    build_match_graph,
    referee_team_ids_by_match_uuid,
)
from app.utils.name_validation import match_name_char_error
from app.utils.datetime_helpers import now_utc_naive

_tournament_locks: Dict[str, threading.Lock] = {}
_locks_lock = threading.Lock()


def _get_tournament_lock(tournament_url: str) -> threading.Lock:
    """Return the per-tournament reentrant lock, creating it on first access.

    Each tournament has its own lock to prevent concurrent scheduling runs
    from producing inconsistent results.

    Args:
        tournament_url: Tournament URL slug used as the lock key.

    Returns:
        The :class:`threading.Lock` for *tournament_url*.
    """
    with _locks_lock:
        if tournament_url not in _tournament_locks:
            _tournament_locks[tournament_url] = threading.Lock()
        return _tournament_locks[tournament_url]


def _now_utc() -> datetime:
    """Return the current UTC time as a timezone-naive :class:`~datetime.datetime`."""
    return now_utc_naive()


def _matches_share_any_team(
    match_a: object,
    match_b: object,
    ref_team_ids_by_uuid: Optional[Dict[str, set]] = None,
) -> bool:
    """Return ``True`` if *match_a* and *match_b* share at least one team/ref.

    Participant sets come from :func:`app.utils.MatchGraph._match_participant_team_ids`,
    which reads referee assignments from the ``match_referees`` table (optionally
    via a pre-fetched *ref_team_ids_by_uuid* map).

    Args:
        match_a: First match object.
        match_b: Second match object.
        ref_team_ids_by_uuid: Optional pre-fetched uuid → referee-team-ids map.

    Returns:
        ``True`` if the participant team-ID sets intersect.
    """
    return bool(
        _match_participant_team_ids(match_a, ref_team_ids_by_uuid)
        & _match_participant_team_ids(match_b, ref_team_ids_by_uuid)
    )


def _intervals_overlap(
    start_a: Optional[datetime],
    length_a: Optional[int],
    start_b: Optional[datetime],
    length_b: Optional[int],
) -> bool:
    """Return ``True`` if two time intervals overlap (exclusive endpoints).

    Args:
        start_a: Start of interval A, or ``None``.
        length_a: Duration of interval A in minutes, or ``None``.
        start_b: Start of interval B, or ``None``.
        length_b: Duration of interval B in minutes, or ``None``.

    Returns:
        ``True`` when both intervals are fully defined and share at least
        one moment; ``False`` when any argument is ``None``.
    """
    if start_a is None or start_b is None or length_a is None or length_b is None:
        return False
    end_a = start_a + timedelta(minutes=length_a)
    end_b = start_b + timedelta(minutes=length_b)
    return start_a < end_b and end_a > start_b


def _evaluate_skip_condition(
    tournament_url: str,
    node: MatchGraphNode,
    name_to_match: Dict,
) -> bool:
    """Evaluate the match's skip_condition DSL using in-memory match_resolver (no DB read)."""
    if not node.skip_condition or not node.skip_condition.strip():
        return False
    try:
        from app.utils.parser import Match as ParserMatch, get_parser, SymbolicMatch

        def match_resolver(name: str):
            m = name_to_match.get(name)
            if m is not None:
                return ParserMatch(m, tournament_url)
            return SymbolicMatch(name, tournament_url)

        parser = get_parser(tournament_url, match_resolver=match_resolver)
        result = parser.parse(node.skip_condition.strip())
        if isinstance(result, bool):
            return result
        return False
    except Exception:
        return False


def _all_schedule_deps_in(node: MatchGraphNode, statuses: Tuple[MatchStatus, ...]) -> bool:
    deps = node.get_schedule_dependencies()
    if not deps:
        return True
    return all(dep.status in statuses for dep in deps)


def _slot_resolved(
    team_id: Optional[str],
    initial: Optional[str],
    tournament_url: str,
    name_to_match: Dict,
    tag_by_name: Optional[Dict[str, object]] = None,
) -> bool:
    """True if this team/ref slot is resolved to a specific team (or is empty)."""
    if team_id and str(team_id).strip():
        return True
    if not initial or not str(initial).strip():
        return True
    initial = str(initial).strip()
    if initial.lower().startswith("tag::"):
        from app.models.tournament import Tag

        tag_name = initial[5:].strip()
        if tag_by_name is not None:
            tag = tag_by_name.get(tag_name)
        else:
            tag = Tag.query.filter_by(event=tournament_url, name=tag_name).first()
        return bool(tag and getattr(tag, "team", None))
    if "::winner" in initial or "::loser" in initial:
        base = initial.split("::")[0].strip()
        dep = name_to_match.get(base)
        return bool(dep and getattr(dep, "match_winner", None) is not None)
    return False


def _all_participating_teams_resolved(
    match: object,
    tournament_url: str,
    name_to_match: Dict,
    tag_by_name: Optional[Dict[str, object]] = None,
) -> bool:
    """
    True if all participating teams (team1, team2, refs) are fully resolved:
    each slot is either a concrete team ID, or a tag:: ref with the tag assigned to a team,
    or a MatchName::winner/loser ref whose match is completed.
    """
    schedule_type = getattr(match, "schedule_type", None)
    if schedule_type in STRUCTURAL_SCHEDULE_TYPES:
        return True

    if not _slot_resolved(
        getattr(match, "team1", None),
        getattr(match, "team1_initial", None),
        tournament_url,
        name_to_match,
        tag_by_name,
    ):
        return False
    if not _slot_resolved(
        getattr(match, "team2", None),
        getattr(match, "team2_initial", None),
        tournament_url,
        name_to_match,
        tag_by_name,
    ):
        return False

    from app.services.dual_write import get_match_referee_rows

    for row in get_match_referee_rows(match):
        if not row.team_id and not row.initial:
            continue
        if not _slot_resolved(row.team_id, row.initial, tournament_url, name_to_match, tag_by_name):
            return False

    return True


def _procedure_with_match(
    graph: MatchGraph,
    node: MatchGraphNode,
    tournament_url: str,
    name_to_match: Dict,
    tag_by_name: Optional[Dict[str, object]] = None,
) -> None:
    """
    PROCEDURE: WITH MATCH m

    Mutates node (nominal_start_time, status). No callbacks.
    """
    if node.schedule_type == ScheduleType.STATBREAK:
        # Statically-scheduled break: the solver never moves its time, and it
        # is never startable (no READY_TO_START). Its status is purely
        # time-derived — COMPLETED once its window (start + length) has
        # elapsed, NOT_STARTED otherwise — so moving a completed STATBREAK
        # into the future un-completes it. Downstream dependents then use its
        # end (start + length) exactly like a BREAK's. Handled before the
        # started/completed early-return below because that lock never applies
        # to STATBREAKs.
        anchor = node.nominal_start_time or node.scheduled_start_time
        if anchor is not None and node.nominal_length is not None:
            if _now_utc() >= anchor + timedelta(minutes=node.nominal_length):
                node.status = MatchStatus.COMPLETED
            elif node.status == MatchStatus.COMPLETED:
                node.status = MatchStatus.NOT_STARTED
        return

    if node.status in (
        MatchStatus.COMPLETED,
        MatchStatus.IN_PROGRESS,
        MatchStatus.SKIPPED,
    ):
        return

    nominal_start_if_skipped: Optional[datetime] = None

    if node.schedule_type == ScheduleType.STATIC:
        if node.status == MatchStatus.NOT_STARTED:
            node.status = MatchStatus.TIME_FINALIZED

    elif node.schedule_type in (ScheduleType.BREAK, ScheduleType.JOIN):
        latest_end = node.get_direct_deps_latest_end_time()
        if latest_end is not None:
            node.nominal_start_time = latest_end

    elif node.schedule_type == ScheduleType.SAFE:
        if node.status == MatchStatus.NOT_STARTED:
            node.nominal_start_time = node.get_direct_deps_latest_end_time(for_safe_nominal=True)
            nominal_start_if_skipped = node.get_direct_deps_latest_end_time(for_safe_nominal=False)

    elif node.schedule_type == ScheduleType.FAST:
        if node.status == MatchStatus.NOT_STARTED:
            node.nominal_start_time = node.get_direct_deps_latest_end_time(for_safe_nominal=False)

    if _all_schedule_deps_in(node, (MatchStatus.COMPLETED, MatchStatus.SKIPPED)):
        skip_cond = _evaluate_skip_condition(tournament_url, node, name_to_match)
        if skip_cond:
            node.status = MatchStatus.SKIPPED
            node.nominal_start_time = (
                nominal_start_if_skipped if nominal_start_if_skipped is not None else node.nominal_start_time
            )
        else:
            if node.schedule_type in (
                ScheduleType.STATIC,
                ScheduleType.SAFE,
                ScheduleType.FAST,
            ):
                # Only mark READY_TO_START when all teams/refs are fully resolved
                if _all_participating_teams_resolved(
                    name_to_match[node.name],
                    tournament_url,
                    name_to_match,
                    tag_by_name,
                ):
                    node.status = MatchStatus.READY_TO_START
                # else: leave at TIME_FINALIZED (or NOT_STARTED for STATIC) until resolved
            else:
                node.status = MatchStatus.COMPLETED

    elif node.schedule_type == ScheduleType.SAFE and _all_schedule_deps_in(
        node, (MatchStatus.IN_PROGRESS, MatchStatus.COMPLETED, MatchStatus.SKIPPED)
    ):
        node.status = MatchStatus.TIME_FINALIZED


def _procedure_for_cycle_node(
    node: MatchGraphNode,
    graph: MatchGraph,
    uuid_to_match: Dict[str, object],
) -> None:
    """Best-effort placement for a node caught in a dependency cycle.

    Topological order can't tell us when a cycle node should run, so we do the
    least-surprising thing instead of raising:

    * STATIC: keep the user-supplied ``nominal_start_time`` (and finalize the
      time, mirroring the normal STATIC procedure).
    * Dynamic (SAFE/FAST/BREAK/JOIN): fall back to the doubly-linked-list
      previous match's effective end time. If there's no previous match (or
      the previous match has no time yet either), leave the existing
      ``nominal_start_time`` alone — the operator can fix the cycle from the
      Schedule Warnings modal.

    Status is left at ``NOT_STARTED`` because in a cycle we can't honestly
    say the schedule is finalised.
    """
    from app.utils.MatchGraph import _node_end_time

    if node.status in (
        MatchStatus.COMPLETED,
        MatchStatus.IN_PROGRESS,
        MatchStatus.SKIPPED,
    ):
        return
    if node.schedule_type == ScheduleType.STATIC:
        if node.status == MatchStatus.NOT_STARTED:
            node.status = MatchStatus.TIME_FINALIZED
        return
    if node.schedule_type == ScheduleType.STATBREAK:
        # Statically scheduled: keep the user-supplied time even inside a cycle.
        return

    match_obj = uuid_to_match.get(node.uuid)
    prev_uuid = getattr(match_obj, "previous_match", None) if match_obj is not None else None
    if not prev_uuid:
        return
    prev_key = graph.uuid_to_key.get(prev_uuid)
    if prev_key is None:
        return
    prev_node = graph.nodes_by_key.get(prev_key)
    if prev_node is None:
        return
    end = _node_end_time(prev_node)
    if end is not None:
        node.nominal_start_time = end


def _scheduled_procedure(node: MatchGraphNode) -> None:
    """Planned-timeline placement: set scheduled_start_time from dependencies.

    Propagates the "if everything had gone to plan" timeline: each dependency
    contributes scheduled_start_time + nominal_length, ignoring real/confirmed
    times and match status entirely. Never reads or writes status, and never
    touches nominal_start_time.

    STATIC and STATBREAK matches keep their user-set scheduled_start_time anchor.
    """
    if node.schedule_type in (ScheduleType.STATIC, ScheduleType.STATBREAK):
        return
    latest = node.get_direct_deps_latest_scheduled_end_time()
    if latest is not None:
        node.scheduled_start_time = latest


def _scheduled_procedure_for_cycle_node(
    node: MatchGraphNode,
    graph: MatchGraph,
    uuid_to_match: Dict[str, object],
) -> None:
    """Best-effort planned placement for a node caught in a dependency cycle.

    Mirrors :func:`_procedure_for_cycle_node` but on the planned timeline: fall
    back to the doubly-linked-list previous match's scheduled end time. STATIC
    and STATBREAK matches keep their anchor. No status is read or written.
    """
    from app.utils.MatchGraph import _node_scheduled_end_time

    if node.schedule_type in (ScheduleType.STATIC, ScheduleType.STATBREAK):
        return
    match_obj = uuid_to_match.get(node.uuid)
    prev_uuid = getattr(match_obj, "previous_match", None) if match_obj is not None else None
    if not prev_uuid:
        return
    prev_key = graph.uuid_to_key.get(prev_uuid)
    if prev_key is None:
        return
    prev_node = graph.nodes_by_key.get(prev_key)
    if prev_node is None:
        return
    end = _node_scheduled_end_time(prev_node)
    if end is not None:
        node.scheduled_start_time = end


def _write_graph_to_db(graph: MatchGraph, uuid_to_match: Dict[str, object]) -> None:
    """Persist graph state to in-memory Match objects (no DB read). Caller commits once."""
    for node in graph.get_all_nodes():
        uuids_to_update = list(node.component_uuids) if node.component_uuids else [node.uuid]
        for uid in uuids_to_update:
            m = uuid_to_match.get(uid)
            if m is not None:
                m.nominal_start_time = node.nominal_start_time
                m.status = node.status


def _write_scheduled_to_db(graph: MatchGraph, uuid_to_match: Dict[str, object]) -> None:
    """Persist only scheduled_start_time to in-memory Match objects. Caller commits once."""
    for node in graph.get_all_nodes():
        uuids_to_update = list(node.component_uuids) if node.component_uuids else [node.uuid]
        for uid in uuids_to_update:
            m = uuid_to_match.get(uid)
            if m is not None:
                m.scheduled_start_time = node.scheduled_start_time


def run_scheduling(tournament_url: str, *, scheduled_pass: bool = False) -> None:
    """
    Single scheduling pass: load all matches, build graph, apply PROCEDURE, write back.

    With ``scheduled_pass=False`` (default) this is the normal solve: it writes
    nominal_start_time and status using real/confirmed times. With
    ``scheduled_pass=True`` it runs the planned-timeline solve, writing only
    scheduled_start_time (scheduled_start_time + nominal_length per dependency,
    ignoring real times and status).
    """
    from app.models.match import Match
    from app.models.tournament import Tag

    lock = _get_tournament_lock(tournament_url)
    lock.acquire()
    try:
        all_matches = Match.query.filter_by(event=tournament_url).all()
        tags = Tag.query.filter_by(event=tournament_url).all()
        tag_by_name = {t.name: t for t in tags}
        uuid_to_match = {m.uuid: m for m in all_matches}
        name_to_match = {}
        for m in all_matches:
            if m.name not in name_to_match:
                name_to_match[m.name] = m
        # The planned (scheduled) pass omits cross-field same-team serialization edges:
        # the planned timeline is purely structural, so scheduled_start_time can't depend
        # on itself through a shifting resource-conflict edge. The nominal pass includes
        # them, anchored on the now-stable scheduled times.
        graph = build_match_graph(
            tournament_url,
            all_matches,
            include_resource_conflict_edges=not scheduled_pass,
        )
        order, cycle_keys = graph.topological_sort()
        for name, field in order:
            node = graph.get_node(name, field)
            if node:
                if scheduled_pass:
                    _scheduled_procedure(node)
                else:
                    _procedure_with_match(graph, node, tournament_url, name_to_match, tag_by_name)
        # Cycle-affected nodes can't be placed by topological order. Stable iteration
        # order is used so re-runs produce identical results even when the cycle
        # composition stays the same.
        for name, field in sorted(cycle_keys):
            node = graph.get_node(name, field)
            if node:
                if scheduled_pass:
                    _scheduled_procedure_for_cycle_node(node, graph, uuid_to_match)
                else:
                    _procedure_for_cycle_node(node, graph, uuid_to_match)
        if scheduled_pass:
            _write_scheduled_to_db(graph, uuid_to_match)
        else:
            _write_graph_to_db(graph, uuid_to_match)
        db.session.commit()
    finally:
        lock.release()


def recompute_all_match_times(tournament_url: str) -> None:
    """Full recompute of nominal times and statuses. Same as run_scheduling."""
    run_scheduling(tournament_url)


def recompute_scheduled_and_nominal_times(tournament_url: str) -> None:
    """Recompute both the planned (scheduled_start_time) and dynamic (nominal_start_time) timelines.

    Step 1 solves the planned timeline (scheduled_start_time), then step 2 runs
    the normal solve (nominal_start_time + status). The planned pass runs first
    so the normal pass's resource-conflict anchors see fresh scheduled times.
    """
    run_scheduling(tournament_url, scheduled_pass=True)
    run_scheduling(tournament_url, scheduled_pass=False)


def get_match_dependencies(match, tournament_url: str) -> List:
    """
    Return list of Match rows that are schedule dependencies of the given match.
    Used by UI to check if dependencies are finished before allowing start.
    """
    from app.models.match import Match

    graph = build_match_graph(tournament_url)
    field = "" if getattr(match, "schedule_type", None) == ScheduleType.JOIN else getattr(match, "field", None)
    node = graph.get_node(match.name, field)
    if not node:
        return []
    schedule_deps = node.get_schedule_dependencies()
    if not schedule_deps:
        return []
    names = [n.name for n in schedule_deps]
    return Match.query.filter(
        Match.event == tournament_url,
        Match.name.in_(names),
    ).all()


def compute_dynamic_match_nominal_start_time(match, tournament_url: str) -> Optional[datetime]:
    """
    Compute nominal_start_time for a SAFE/FAST/BREAK/JOIN match from the graph
    (for use when adding/editing a match before commit). Does not write to DB.
    """
    graph = build_match_graph(tournament_url)
    field = "" if getattr(match, "schedule_type", None) == ScheduleType.JOIN else getattr(match, "field", None)
    node = graph.get_node(match.name, field)
    if not node:
        return None
    if node.schedule_type in (ScheduleType.BREAK, ScheduleType.JOIN):
        return node.get_direct_deps_latest_end_time()
    if node.schedule_type == ScheduleType.SAFE:
        return node.get_direct_deps_latest_end_time(for_safe_nominal=True)
    if node.schedule_type == ScheduleType.FAST:
        return node.get_direct_deps_latest_end_time(for_safe_nominal=False)
    return None


def validate_match_input(match, tournament_url: str) -> Tuple[bool, Optional[str]]:
    """Hard pre-write checks for a match: name is well-formed and unique.

    Soft scheduling conflicts (double-booked teams, cycles, dangling refs,
    unknown teams) are explicitly *not* checked here — they're surfaced by
    :func:`validate_match_warnings` after the write so the operator can fix
    them in any order.

    Returns ``(True, None)`` on success or ``(False, message)`` on failure.
    """
    if not match.name or not match.name.strip():
        return False, "Match name is required."
    mn_err = match_name_char_error(match.name)
    if mn_err:
        return False, mn_err
    from app.models.match import Match

    schedule_type = getattr(match, "schedule_type", None)
    existing = Match.query.filter_by(
        event=tournament_url,
        name=match.name.strip(),
    )
    # For BREAK/STATBREAK/JOIN, only check uniqueness on the same field within the
    # structural group (mirrors the `unique_with_field` partial index, which spans
    # all structural types).
    if schedule_type in STRUCTURAL_SCHEDULE_TYPES:
        field = (getattr(match, "field", None) or "").strip()
        existing = existing.filter(
            Match.field == field,
            Match.schedule_type.in_(STRUCTURAL_SCHEDULE_TYPES),
        )
    if match.uuid:
        existing = existing.filter(Match.uuid != match.uuid)
    if existing.first():
        return False, "A match with this name already exists."
    return True, None


def _extract_match_ref_names(initial: Optional[str]) -> List[str]:
    """Return base match names referenced in an `_initial` token (`Match1::winner` etc.)."""
    if not initial:
        return []
    names: List[str] = []
    for raw in str(initial).split(","):
        token = raw.strip()
        if "::" not in token:
            continue
        base, _, qualifier = token.partition("::")
        base = base.strip()
        if not base:
            continue
        if qualifier in ("winner", "loser"):
            names.append(base)
    return names


def validate_match_warnings(tournament_url: str) -> List[dict]:
    """Compute the soft-validation warnings for a tournament's full schedule.

    Returns a list of ``{"kind": ..., "message": ..., "matches": [...]}`` rows
    covering:

    * ``unknown_team``: ``team1`` / ``team2`` / a ref points at a team that
      doesn't exist in the tournament's registrations.
    * ``missing_team``: ``team1`` or ``team2`` is empty on a non-BREAK/JOIN
      match (no resolved id and no ``_initial`` placeholder either).
    * ``duplicate_team``: the same team (id or unresolved ``_initial`` token)
      appears in more than one slot of a single match.
    * ``cycle``: the dependency graph contains a cycle (the topological sort
      raises ``ValueError``).
    * ``unknown_match_ref``: a ``Match::winner`` / ``::loser`` / ``previous_match``
      / skip-condition reference names a match that doesn't exist.
    * ``double_booked``: two matches share a team (or ref) and overlap in time
      (FAST/SAFE matches at the same nominal start; STATIC overlapping intervals).
    """
    from app.models import Team, Tournament
    from app.models.match import Match
    from app.models.tournament import Tag
    from app.services.dual_write import get_match_refs_initial_csv
    from app.services.registration_resolver import team_registrations_for_tournament

    matches = Match.query.filter_by(event=tournament_url).all()
    name_to_match = {m.name: m for m in matches}
    tag_names = {t.name for t in Tag.query.filter_by(event=tournament_url).all()}

    # Pull registrations through the scope-aware resolver so league events count
    # their league-scoped registrations as registered (and vice versa for
    # standalone events). Without this, every team in a league event reads as
    # "not registered" because raw `event=tournament_url` never matches league rows.
    tournament = Tournament.query.filter_by(url=tournament_url).first()
    registered_team_ids: set[str] = set()
    if tournament is not None:
        for tr in team_registrations_for_tournament(tournament):
            if tr.team:
                registered_team_ids.add(tr.team)
    all_team_ids = {t.id for t in Team.query.all()}

    warnings: List[dict] = []

    def add(kind: str, message: str, matches_involved: List[str]) -> None:
        warnings.append({"kind": kind, "message": message, "matches": sorted(set(matches_involved))})

    def looks_like_team_ref(token: str) -> bool:
        token = (token or "").strip()
        if not token:
            return False
        if "::" in token:
            return False
        if token.lower().startswith("tag::"):
            return False
        return True

    # Unknown teams (concrete team1/team2/refs that don't exist in the registration set).
    for m in matches:
        for slot, val in (("team1", m.team1), ("team2", m.team2)):
            if val and val not in registered_team_ids and val in all_team_ids:
                add(
                    "unknown_team",
                    f"Match '{m.name}' {slot} '{val}' is not registered for this tournament.",
                    [m.name],
                )
            elif val and val not in all_team_ids:
                add(
                    "unknown_team",
                    f"Match '{m.name}' {slot} references team '{val}' which does not exist.",
                    [m.name],
                )
        for slot, raw in (("team1_initial", m.team1_initial), ("team2_initial", m.team2_initial)):
            if raw and looks_like_team_ref(raw) and raw not in all_team_ids:
                add(
                    "unknown_team",
                    f"Match '{m.name}' {slot} '{raw}' is not a known team or registered shortname.",
                    [m.name],
                )
        # tags should resolve
        for slot, raw in (("team1_initial", m.team1_initial), ("team2_initial", m.team2_initial)):
            if raw and raw.lower().startswith("tag::"):
                tag_name = raw[5:].strip()
                if tag_name and tag_name not in tag_names:
                    add(
                        "unknown_team",
                        f"Match '{m.name}' {slot} references unknown tag '{tag_name}'.",
                        [m.name],
                    )

    # Missing team1 / team2 (BREAK and JOIN matches don't have teams, so skip them).
    from app.services.dual_write import get_match_referee_rows

    def _slot_normalized_key(team_id, initial):
        """Pick a comparable key for a slot. Prefer the resolved team id; fall back to
        the user-typed `_initial` token (so two unresolved ``Match::winner`` slots
        still flag as duplicates). Returns ``None`` when both are empty."""
        if team_id and str(team_id).strip():
            return str(team_id).strip()
        if initial and str(initial).strip():
            return str(initial).strip()
        return None

    for m in matches:
        if m.schedule_type in STRUCTURAL_SCHEDULE_TYPES:
            continue
        for slot, team_id, initial in (
            ("team1", m.team1, m.team1_initial),
            ("team2", m.team2, m.team2_initial),
        ):
            if _slot_normalized_key(team_id, initial) is None:
                add(
                    "missing_team",
                    f"Match '{m.name}' has no {slot} assigned.",
                    [m.name],
                )

    # Duplicate-team within a single match (same id or same `_initial` token in 2+ slots).
    for m in matches:
        if m.schedule_type in (ScheduleType.JOIN,):
            continue
        slot_entries: list[tuple[str, str]] = []
        for slot, team_id, initial in (
            ("team1", m.team1, m.team1_initial),
            ("team2", m.team2, m.team2_initial),
        ):
            key = _slot_normalized_key(team_id, initial)
            if key is not None:
                slot_entries.append((slot, key))
        for row in get_match_referee_rows(m):
            key = _slot_normalized_key(getattr(row, "team_id", None), getattr(row, "initial", None))
            if key is not None:
                slot_entries.append((f"refs[{row.slot}]", key))
        seen: dict[str, str] = {}
        reported: set[tuple[str, str, str]] = set()
        for slot, key in slot_entries:
            prior = seen.get(key)
            if prior is not None and prior != slot:
                triple = (prior, slot, key)
                if triple not in reported:
                    reported.add(triple)
                    add(
                        "duplicate_team",
                        f"Match '{m.name}' has '{key}' in both {prior} and {slot}.",
                        [m.name],
                    )
            else:
                seen.setdefault(key, slot)

    # Unknown match references: Match::winner / Match::loser / previous_match / skip-cond direct deps.
    for m in matches:
        refs_csv = get_match_refs_initial_csv(m) or ""
        ref_sources = [m.team1_initial, m.team2_initial, refs_csv]
        referenced_names: set[str] = set()
        for src in ref_sources:
            referenced_names.update(_extract_match_ref_names(src))
        try:
            skip_deps = m.get_skip_condition_dependencies()
            referenced_names.update(skip_deps.get("direct", set()))
            referenced_names.update(skip_deps.get("skip_condition", set()))
        except Exception:
            pass
        for ref_name in referenced_names:
            if ref_name not in name_to_match:
                add(
                    "unknown_match_ref",
                    f"Match '{m.name}' references match '{ref_name}', which does not exist.",
                    [m.name, ref_name],
                )
        if m.previous_match and m.previous_match not in {x.uuid for x in matches}:
            add(
                "unknown_match_ref",
                f"Match '{m.name}' has a previous_match link pointing at a missing match.",
                [m.name],
            )

    # Cycle detection via the existing graph builder. topological_sort no longer
    # raises — it just hands back the keys it couldn't place.
    graph = build_match_graph(tournament_url, matches)
    _order, cycle_keys = graph.topological_sort()
    if cycle_keys:
        cycle_names = sorted({name for name, _field in cycle_keys})
        add(
            "cycle",
            "Cyclic match dependency among: " + ", ".join(cycle_names),
            cycle_names,
        )

    # Double-booked teams on the *planned* (scheduled) timeline. The nominal solve
    # serializes cross-field same-team matches so they don't actually collide, which
    # means the conflict only shows up on the planned timeline — that's the timeline
    # we check here. Two matches sharing a team whose scheduled intervals overlap are
    # double-booked, regardless of field or schedule type. Same-name break rows are
    # one logical break spanning fields, so a shared team attending "both" is fine.
    ref_ids_by_uuid = referee_team_ids_by_match_uuid(matches)
    for i, m in enumerate(matches):
        for other in matches[i + 1 :]:
            if (
                m.name == other.name
                and m.schedule_type in BREAK_SCHEDULE_TYPES
                and other.schedule_type in BREAK_SCHEDULE_TYPES
            ):
                continue
            if not _matches_share_any_team(m, other, ref_ids_by_uuid):
                continue
            if _intervals_overlap(
                m.scheduled_start_time,
                m.nominal_length,
                other.scheduled_start_time,
                other.nominal_length,
            ):
                add(
                    "double_booked",
                    f"Matches '{m.name}' and '{other.name}' share a team and overlap on the planned schedule.",
                    [m.name, other.name],
                )

    return warnings


def detect_match_conflicts(tournament_url: str) -> List[dict]:
    """
    Detect scheduling conflicts (e.g. overlapping matches on same field).
    Returns a list of conflict descriptors for the UI.
    """
    from datetime import timedelta
    from app.models.match import Match

    matches = Match.query.filter_by(event=tournament_url).all()
    conflicts = []
    for m in matches:
        if not m.field or not m.nominal_start_time or not m.nominal_length:
            continue
        end = m.nominal_start_time + timedelta(minutes=m.nominal_length or 0)
        for other in matches:
            if other.uuid == m.uuid or not other.field or other.field != m.field:
                continue
            if not other.nominal_start_time or not other.nominal_length:
                continue
            other_end = other.nominal_start_time + timedelta(minutes=other.nominal_length)
            if m.nominal_start_time < other_end and end > other.nominal_start_time:
                conflicts.append({"match1": m.name, "match2": other.name, "field": m.field})
    return conflicts
