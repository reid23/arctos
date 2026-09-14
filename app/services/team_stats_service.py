"""Compute team standings (wins, losses, points) from a set of matches.

The same logic is used for tournament results and league results, so
the function takes the matches as input rather than querying them
itself.  Skipped matches and incomplete matches are excluded; only
``COMPLETED`` matches contribute to standings.
"""

from __future__ import annotations

from app.domain.enums import ScheduleType
from app.services.registration_resolver import team_registrations_for_tournament


def _pseudonym_and_photo_maps(
    team_id: str, reg_by_team: dict, team_by_id: dict
) -> tuple[str | None, str | None, str | None]:
    """Resolve a team's display name, profile photo, and shortname from preloaded maps.

    Args:
        team_id: The team's unique identifier.
        reg_by_team: Mapping of team ID →
            :class:`~app.models.registration.TeamRegistration`.
        team_by_id: Mapping of team ID →
            :class:`~app.models.user.Team`.

    Returns:
        A ``(pseudonym, profile_photo, shortname)`` tuple.  *pseudonym* falls
        back to the team name, then the team ID.  *profile_photo* is ``None``
        when the team record is not found.  *shortname* is the registration's
        shortname when present, otherwise ``None``.
    """
    if not team_id:
        return None, None, None
    reg = reg_by_team.get(team_id)
    pseudonym = reg.pseudonym if reg and reg.pseudonym else None
    shortname = reg.shortname if (reg and reg.shortname) else None
    team = team_by_id.get(team_id)
    profile_photo = team.profile_photo if team else None
    if not pseudonym and team:
        pseudonym = team.name
    if not pseudonym:
        pseudonym = team_id
    return pseudonym, profile_photo, shortname


def compute_team_stats(matches: list, tournament, include_ribbon: bool = False) -> list[dict]:
    """Compute aggregate win/loss and point statistics for all teams in a set of matches.

    Skips ``BREAK`` and ``JOIN`` schedule-type matches, and optionally skips
    ribbon (exhibition) games.

    Args:
        matches: List of :class:`~app.models.match.Match` instances to
            aggregate over.
        tournament: Any :class:`~app.models.tournament.Tournament` in the
            same event or league; used for pseudonym/photo lookup.
        include_ribbon: When ``True``, ribbon games are included in
            stats; otherwise they are excluded.

    Returns:
        List of dicts, each with keys: ``id``, ``pseudonym``,
        ``shortname``, ``profile_photo``, ``matches_won``,
        ``matches_lost``, ``points_won``, ``points_lost``.
    """
    from models import Point, Team

    count_matches = [
        m
        for m in matches
        if getattr(m, "schedule_type", None) not in (ScheduleType.BREAK, ScheduleType.STATBREAK, ScheduleType.JOIN)
        and (include_ribbon or not getattr(m, "ribbon", False))
    ]

    real_team_ids: set[str] = set()
    for m in count_matches:
        for tid in (m.team1 or m.team1_initial, m.team2 or m.team2_initial):
            if not tid or tid == "TBA" or "::" in str(tid):
                continue
            if str(tid).startswith("tag::"):
                continue
            real_team_ids.add(str(tid))

    regs = team_registrations_for_tournament(tournament)
    reg_by_team = {r.team: r for r in regs}
    team_by_id = {}
    if real_team_ids:
        team_by_id = {t.id: t for t in Team.query.filter(Team.id.in_(real_team_ids)).all()}

    points_by_match = {}
    if count_matches:
        match_ids = [m.uuid for m in count_matches]
        for p in Point.query.filter(Point.match.in_(match_ids)).all():
            points_by_match.setdefault(p.match, []).append(p)
    team_stats = {}
    for m in count_matches:
        t1 = m.team1 or m.team1_initial
        t2 = m.team2 or m.team2_initial
        for tid, _ in [(t1, True), (t2, False)]:
            if not tid or tid == "TBA" or "::" in str(tid):
                continue
            if tid not in team_stats:
                if str(tid).startswith("tag::") or "::" in str(tid):
                    pseudonym, profile_photo, shortname = tid, None, None
                else:
                    pseudonym, profile_photo, shortname = _pseudonym_and_photo_maps(tid, reg_by_team, team_by_id)
                team_stats[tid] = {
                    "id": tid,
                    "pseudonym": pseudonym or tid,
                    "shortname": shortname,
                    "profile_photo": profile_photo,
                    "matches_won": 0,
                    "matches_lost": 0,
                    "points_won": 0,
                    "points_lost": 0,
                }
        winner = m.match_winner.value if m.match_winner else None
        if winner and t1 and t2 and t1 != "TBA" and t2 != "TBA":
            if winner == "TEAM1":
                team_stats[t1]["matches_won"] += 1
                team_stats[t2]["matches_lost"] += 1
            elif winner == "TEAM2":
                team_stats[t2]["matches_won"] += 1
                team_stats[t1]["matches_lost"] += 1
        points_list = points_by_match.get(m.uuid, [])
        t1p = sum(1 for p in points_list if getattr(p, "winner", None) == "TEAM1" and not getattr(p, "rerolled", False))
        t2p = sum(1 for p in points_list if getattr(p, "winner", None) == "TEAM2" and not getattr(p, "rerolled", False))
        if t1 and t1 != "TBA":
            team_stats[t1]["points_won"] += t1p
            team_stats[t1]["points_lost"] += t2p
        if t2 and t2 != "TBA":
            team_stats[t2]["points_won"] += t2p
            team_stats[t2]["points_lost"] += t1p
    return list(team_stats.values())
