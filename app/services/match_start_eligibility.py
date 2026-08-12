"""
Match start eligibility: single source of truth for "can start" and blocking reasons.

Used by match detail API, start-match GET/POST, and MatchService.start_match.

Why modal has three sections:
1. Match is ready: What is the match status? Does this prevent it from being started?
2. Conflicts: Is there another match on the field? Are all dependency matches complete?
3. Ref permissions:
   a. Who is allowed: Based on tournament settings, who should be allowed to head ref?
   b. Current user: If team -> "only player accounts can be refs. you are signed in as a team (team name)."
      If not logged in -> "you must be logged in as a player to start matches."
      If explicitly listed refs exist -> "[list] are allowed"
      If reffing teams allowed -> "players registered for assigned ref teams [list of teams] are allowed."
      If user is player not registered -> "you are not registered for this tournament."
      If user is player registered but unattached -> "you are registered as unattached"
      If user is player registered for a team -> "you are currently registered for [team name]"
      If allow anyone -> "anyone registered for this event (or its league) can head ref."
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Tuple

from app.domain.enums import MatchStatus, RegistrationStatus, ScheduleType
from app.utils.helpers import can_head_ref_match, get_team_display_name_for_event


@dataclass
class WhySections:
    """Structured reasons for the 'Why can't I start?' modal."""

    match_ready: Dict[str, Any] = field(default_factory=dict)  # status, reasons[], blocks_start
    conflicts: List[str] = field(default_factory=list)
    ref_permissions: Dict[str, List[str]] = field(default_factory=dict)  # who_allowed[], current_user[]


def get_who_allowed_explanation(tournament_url: str, match=None) -> List[str]:
    """
    Based on tournament settings, who should be allowed to head ref?
    - If explicitly listed refs exist: "[list] are allowed"
    - If reffing teams allowed: "players registered for assigned ref teams [list of teams] are allowed."
    - If allow anyone: "anyone registered for this event (or its league) can head ref."
    """
    from app.error_values import Err, Ok
    from app.services._common import get_tournament_or_err
    from app.services.dual_write import get_head_ref_allowlist_ids, get_match_ref_team_ids

    match get_tournament_or_err(tournament_url):
        case Ok(tournament):
            pass
        case Err(_):
            return ["Tournament not found."]

    lines: List[str] = []

    allowed_list = get_head_ref_allowlist_ids(tournament)
    if allowed_list:
        lines.append(f"{', '.join(allowed_list)} are allowed.")

    if tournament.head_refs_allow_reffing_teams and match:
        ref_teams = [tid for tid in get_match_ref_team_ids(match) if tid]
        if ref_teams:
            names = [get_team_display_name_for_event(tournament_url, tid) or tid for tid in ref_teams]
            lines.append(f"Players registered for assigned ref teams ({', '.join(names)}) are allowed.")

    if tournament.head_refs_allow_anyone:
        lines.append("Anyone registered for this event (or its league) can head ref.")

    if not lines:
        lines.append("No head ref policy is configured for this tournament.")

    return lines


def get_current_user_explanation(tournament_url: str, user) -> List[str]:
    """
    Explanation of the current user in the context of ref permissions:
    - If user is a team: "only player accounts can be refs. you are signed in as a team (team name)."
    - If user is not logged in: "you must be logged in as a player to start matches."
    - If player not registered for tournament: "you are not registered for this tournament."
    - If player registered but unattached: "you are registered as unattached"
    - If player registered for a team: "you are currently registered for [team name]"
    """
    from app.utils.user_helpers import is_player, is_team

    lines: List[str] = []

    if not user:
        lines.append("You must be logged in as a player to start matches.")
        return lines

    if is_team(user):
        team_name = getattr(user, "name", None) or getattr(user, "id", "")
        lines.append(f"Only player accounts can be refs. You are signed in as a team ({team_name}).")
        return lines

    if not is_player(user):
        lines.append("You must be logged in as a player to start matches.")
        return lines

    user_id = getattr(user, "id", None)
    if not user_id:
        lines.append("Could not determine your user.")
        return lines

    from models import Tournament
    from app.services.registration_resolver import player_registration_for_tournament

    tournament = Tournament.query.get(tournament_url) if tournament_url else None
    reg = (
        player_registration_for_tournament(
            tournament,
            user_id,
            statuses=[RegistrationStatus.CONFIRMED],
        )
        if tournament is not None
        else None
    )

    if not reg:
        lines.append("You are not registered for this tournament.")
        return lines

    team_id = getattr(reg, "team", None)
    if not team_id:
        lines.append("You are registered as unattached.")
        return lines

    team_name = get_team_display_name_for_event(tournament_url, team_id)
    lines.append(f"You are currently registered for {team_name}.")
    return lines


def _reason_field_busy(tournament_url: str, match) -> str | None:
    """If another match is IN_PROGRESS on same field, return reason string; else None."""
    other = get_conflicting_match_on_field(tournament_url, match)
    if not other:
        return None
    return f"Another match is in progress on this field: {getattr(other, 'name', other.uuid)}."


def get_conflicting_match_on_field(tournament_url: str, match):
    """Return the match IN_PROGRESS on the same field, or None."""
    from models import Match

    field = getattr(match, "field", None)
    if not field or not str(field).strip():
        return None
    return (
        Match.query.filter_by(
            event=tournament_url,
            field=field,
            status=MatchStatus.IN_PROGRESS,
        )
        .filter(Match.uuid != match.uuid)
        .first()
    )


def _reasons_teams_refs(match, tournament_url: str) -> List[str]:
    """Reasons for teams or refs not resolved. For refs, list which slots are unresolved (tag, match ref, or explicit)."""
    from app.services.dual_write import get_match_referee_rows
    from models import Match, Tag

    reasons = []
    if not getattr(match, "team1", None) or not getattr(match, "team2", None):
        reasons.append("Teams not yet determined.")
    for row in get_match_referee_rows(match):
        if row.team_id:
            continue
        initial = (row.initial or "").strip()
        if not initial:
            continue
        slot_label = row.slot + 1
        if initial.lower().startswith("tag::"):
            tag_name = initial[5:].strip()
            tag = Tag.query.filter_by(event=tournament_url, name=tag_name).first()
            if not tag or not getattr(tag, "team", None):
                reasons.append(f"Ref slot {slot_label} is set by tag '{tag_name}', which is not yet assigned.")
        elif "::winner" in initial or "::loser" in initial:
            base = initial.split("::")[0].strip()
            dep = Match.query.filter_by(name=base, event=tournament_url).first()
            if not dep or getattr(dep, "match_winner", None) is None:
                reasons.append(f"Ref slot {slot_label} depends on match '{base}', which is not yet completed.")
        else:
            reasons.append(f"Ref slot {slot_label} is not yet set.")
    return reasons


def _reasons_status_and_deps(tournament_url: str, match) -> List[str]:
    """Reasons when status is NOT_STARTED or TIME_FINALIZED (deps, previous, tags)."""
    from app.utils.scheduling import get_match_dependencies
    from models import Match, Tag

    reasons = []
    status = getattr(match, "status", None)
    if status not in (MatchStatus.NOT_STARTED, MatchStatus.TIME_FINALIZED):
        return reasons

    prev_uuid = getattr(match, "previous_match", None)
    if prev_uuid:
        prev = Match.query.filter_by(uuid=prev_uuid, event=tournament_url).first()
        if prev and prev.status not in (MatchStatus.COMPLETED, MatchStatus.SKIPPED):
            reasons.append(f"Previous match '{getattr(prev, 'name', prev_uuid)}' is not completed.")

    if getattr(match, "schedule_type", None) != ScheduleType.STATIC:
        try:
            deps = get_match_dependencies(match, tournament_url)
        except Exception:
            deps = []
        if deps:
            not_finished = [d for d in deps if d.status not in (MatchStatus.COMPLETED, MatchStatus.SKIPPED)]
            if not_finished and not (getattr(match, "ready_to_start", False)):
                names = [getattr(d, "name", d.uuid) for d in not_finished]
                reasons.append(f"Dependency match(es) not completed: {', '.join(names)}.")

    for attr, label in [
        ("team1_initial", "Team 1"),
        ("team2_initial", "Team 2"),
    ]:
        initial = getattr(match, attr, None) or ""
        initial = (initial or "").strip()
        if not initial:
            continue
        if initial.lower().startswith("tag::"):
            tag_name = initial[5:].strip()
            tag = Tag.query.filter_by(event=tournament_url, name=tag_name).first()
            if not tag or not getattr(tag, "team", None):
                reasons.append(f"{label} is set by tag '{tag_name}', which is not yet assigned.")
        elif "::winner" in initial or "::loser" in initial:
            base = initial.split("::")[0].strip()
            dep = Match.query.filter_by(name=base, event=tournament_url).first()
            if not dep or getattr(dep, "match_winner", None) is None:
                reasons.append(f"{label} depends on match '{base}', which is not yet completed.")

    # Ref-slot reasons are added by _reasons_teams_refs so we don't duplicate here.
    return reasons


def _build_why_sections(tournament_url: str, match, user) -> WhySections:
    """Build the three-section structure for the why modal."""
    from app.utils.user_helpers import is_player

    status = getattr(match, "status", None)
    status_str = str(status) if status is not None else "unknown"

    sections = WhySections()

    # 1. Match is ready
    match_ready_reasons: List[str] = []
    blocks_start = False
    if status in (MatchStatus.COMPLETED, MatchStatus.SKIPPED):
        match_ready_reasons.append(f"Match status is {status_str}. The match is already over.")
    elif status == MatchStatus.READY_TO_START:
        match_ready_reasons.append(
            "Match status is READY_TO_START. The match can be started once ref permissions and conflicts are satisfied."
        )
    elif status in (MatchStatus.NOT_STARTED, MatchStatus.TIME_FINALIZED):
        match_ready_reasons.append(f"Match status is {status_str}. The match is not yet ready to start.")
        match_ready_reasons.extend(_reasons_status_and_deps(tournament_url, match))
        if len(match_ready_reasons) == 1:
            match_ready_reasons.append("Schedule or dependencies not yet finalized.")
        blocks_start = True
    else:
        match_ready_reasons.append(f"Match status is {status_str}, not READY_TO_START.")
        blocks_start = True

    team_ref_reasons = _reasons_teams_refs(match, tournament_url)
    if team_ref_reasons:
        match_ready_reasons.extend(team_ref_reasons)
        blocks_start = True

    sections.match_ready = {
        "status": status_str,
        "reasons": match_ready_reasons,
        "blocks_start": blocks_start or bool(team_ref_reasons),
    }

    # 2. Conflicts: only "another match on the field" (field busy)
    field_reason = _reason_field_busy(tournament_url, match)
    if field_reason:
        sections.conflicts.append(field_reason)

    # 3. Ref permissions
    sections.ref_permissions["who_allowed"] = get_who_allowed_explanation(tournament_url, match)
    sections.ref_permissions["current_user"] = get_current_user_explanation(tournament_url, user)
    # Section is "ok" (not blocking) when user is allowed to head ref
    user_id = getattr(user, "id", None) if user else None
    sections.ref_permissions["is_ok"] = bool(
        user_id and is_player(user) and can_head_ref_match(tournament_url, user_id, match=match)
    )

    return sections


def get_can_start_and_reasons(tournament_url: str, match, user) -> Tuple[bool, List[str], WhySections]:
    """
    Single source of truth: can the given user start this match, and why not if not?

    Returns:
        (can_start, block_reasons, why_sections).
        block_reasons is a flat list for backward compatibility.
        why_sections is always populated when match is not COMPLETED/SKIPPED (for the modal).
    """
    from app.utils.user_helpers import is_player

    status = getattr(match, "status", None)
    why_sections = _build_why_sections(tournament_url, match, user)

    if status in (MatchStatus.COMPLETED, MatchStatus.SKIPPED):
        return False, [], why_sections

    reasons: List[str] = []

    if not user or not is_player(user):
        reasons.extend(why_sections.ref_permissions["current_user"])
        return False, reasons, why_sections

    user_id = getattr(user, "id", None)
    if not user_id:
        reasons.append("Could not determine your user.")
        return False, reasons, why_sections

    if not can_head_ref_match(tournament_url, user_id, match=match):
        reasons.append("You are not allowed to head ref this match.")
        reasons.extend(why_sections.ref_permissions["current_user"])
        return False, reasons, why_sections

    field_reason = _reason_field_busy(tournament_url, match)
    if field_reason:
        reasons.append(field_reason)
        return False, reasons, why_sections

    if status != MatchStatus.READY_TO_START:
        reasons.extend(why_sections.match_ready["reasons"])
        return False, reasons, why_sections

    team_ref_reasons = _reasons_teams_refs(match, tournament_url)
    if team_ref_reasons:
        reasons.extend(team_ref_reasons)
        return False, reasons, why_sections

    return True, [], why_sections


def why_sections_to_dict(sections: WhySections) -> Dict[str, Any]:
    """Serialize WhySections for JSON API response."""
    return {
        "match_ready": sections.match_ready,
        "conflicts": sections.conflicts,
        "ref_permissions": sections.ref_permissions,
    }
