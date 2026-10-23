"""JSON shapes of the public API (``/api/v1``).

Unlike the SPA serializers, these are a contract with third-party apps:
fields may be added, but never renamed, retyped, or removed. Keys are
snake_case, timestamps are ISO-8601 UTC ending in ``Z``, and ids are the
ones the database uses (tournament and league URL slugs, player and team
ids, match uuids). Nothing here may expose contact details, payments,
waivers, or private injuries; callers decide which rows are visible.
"""

from __future__ import annotations

import collections

from app.domain.enums import RegistrationStatus, SetType, TeamRegistrationStatus
from app.services._common import Scope
from app.services.dual_write import get_camera_timepoint_arrays, get_match_ref_initials, get_match_ref_team_ids
from app.services.registration_resolver import player_registrations_for_scope, team_registrations_for_scope
from app.services.scoreboard_service import set_scores
from app.utils.datetime_helpers import to_iso_z
from app.utils.helpers import get_registrable_config
from models import Camera, League, Player, PlayerRegistration, Team, TeamRegistration, Tournament

STONES_PER_SET_DEFAULT = 100


def iso(dt) -> str | None:
    return to_iso_z(dt).unwrap_or(None)


def _enum_value(value) -> str | None:
    return str(value) if value is not None else None


def _photo_path(stored_path: str | None) -> str | None:
    """Profile photos are served by Arctos under ``/static/``."""
    return f"/static/{stored_path}" if stored_path else None


def scope_of(tournament) -> Scope:
    """Registration scope of *tournament*: its league's, or its own when standalone."""
    return Scope.league(tournament.league_id) if tournament.league_id else Scope.event(tournament.url)


def _reference(row) -> dict | None:
    return {"id": row.url, "name": row.name} if row else None


def _registration_settings_json(config) -> dict:
    return {
        "team_registration_open": bool(config and config.team_registration_open),
        "player_registration_open": bool(config and config.player_registration_open),
        "max_teams": config.n_max_teams if config else None,
        "max_roster_size": config.max_team_size_roster if config else None,
        "max_field_size": config.max_team_size_field if config else None,
        "team_fee": config.team_reg_fee if config else None,
        "player_fee": config.player_reg_fee if config else None,
    }


def tournament_json(tournament) -> dict:
    league = League.query.get(tournament.league_id) if tournament.league_id else None
    return {
        "id": tournament.url,
        "name": tournament.name,
        "banner_photo": _photo_path(tournament.banner_photo),
        "start_date": iso(tournament.start_date),
        "end_date": iso(tournament.end_date),
        "location": tournament.location,
        "about": tournament.about,
        "published": bool(tournament.published),
        "schedule_published": bool(tournament.schedule_published),
        "bracket_published": bool(tournament.bracket_published),
        "league": _reference(league),
        "registration": _registration_settings_json(get_registrable_config(tournament)),
    }


def league_json(league) -> dict:
    return {
        "id": league.url,
        "name": league.name,
        "banner_photo": _photo_path(league.banner_photo),
        "about": league.about,
        "published": bool(league.published),
        "registration": _registration_settings_json(league.registrable_config),
    }


def user_json(user, user_type: str) -> dict:
    return {"id": user.id, "name": user.name, "type": user_type}


def player_summary_json(player) -> dict:
    return {
        "id": player.id,
        "name": player.name,
        "location": player.location,
        "profile_photo": _photo_path(player.profile_photo),
    }


def player_profile_json(player) -> dict:
    return player_summary_json(player) | {
        "bio": player.bio,
        "pronouns": player.pronouns,
        "pompfen": player.pompfen or [],
    }


def team_summary_json(team) -> dict:
    return {
        "id": team.id,
        "name": team.name,
        "location": team.location,
        "profile_photo": _photo_path(team.profile_photo),
    }


def team_profile_json(team) -> dict:
    return team_summary_json(team) | {"website": team.website, "about": team.about}


def injury_json(injury) -> dict:
    return {
        "id": injury.id,
        "message": injury.message,
        "date": iso(injury.stamp),
        "active": bool(injury.active),
        "public": bool(injury.show),
    }


class TeamNames:
    """Display names of teams within one registration scope, loaded once per response.

    A team shows as its registration pseudonym, else its account name.
    """

    def __init__(self, scope: Scope):
        self.registrations = {r.team: r for r in team_registrations_for_scope(scope)}
        self._teams = (
            {t.id: t for t in Team.query.filter(Team.id.in_(list(self.registrations))).all()}
            if self.registrations
            else {}
        )

    def team(self, team_id: str):
        if team_id not in self._teams:
            self._teams[team_id] = Team.query.get(team_id)
        return self._teams[team_id]

    def reference(self, team_id: str | None, placeholder: str | None = None) -> dict:
        """``{"id", "name", "shortname"}``; unresolved slots have no id and show *placeholder*."""
        if not team_id:
            return {"id": None, "name": placeholder or None, "shortname": None}
        registration = self.registrations.get(team_id)
        if registration and registration.pseudonym:
            name = registration.pseudonym
        else:
            team = self.team(team_id)
            name = team.name if team else team_id
        return {
            "id": team_id,
            "name": name,
            "shortname": (registration.shortname or None) if registration else None,
        }


def _sets_json(points) -> list[dict]:
    return [
        {"number": number, "team1_score": team1_score, "team2_score": team2_score}
        for number, (team1_score, team2_score) in set_scores(points).items()
    ]


def point_json(point, set_type) -> dict:
    return {
        "stamp": iso(point.stamp),
        "end_stamp": iso(point.end_stamp),
        "set_number": point.set_number,
        "winner": point.winner,
        "rerolled": bool(point.rerolled),
        "stones_at_start": point.stones_at_start if set_type == SetType.STONES else None,
    }


def _stones_json(match) -> dict | None:
    if match.set_type != SetType.STONES:
        return None
    return {"per_set": match.stones_per_set or STONES_PER_SET_DEFAULT, "remaining": match.stones_remaining}


def match_json(match, teams: TeamNames, points) -> dict:
    return {
        "id": match.uuid,
        "name": match.name,
        "field": match.field,
        "status": _enum_value(match.effective_status),
        "schedule_type": _enum_value(match.schedule_type),
        "set_type": _enum_value(match.set_type),
        "ribbon": bool(match.ribbon),
        "team1": teams.reference(match.team1, match.team1_initial),
        "team2": teams.reference(match.team2, match.team2_initial),
        "winner": _enum_value(match.match_winner),
        "scheduled_start_time": iso(match.scheduled_start_time),
        "start_time": iso(match.nominal_start_time),
        "started_at": iso(match.confirmed_start_time),
        "completed_at": iso(match.completed_time),
        "length_minutes": match.nominal_length,
        "sets": _sets_json(points),
    }


def match_detail_json(match, teams: TeamNames, points) -> dict:
    team_ids = get_match_ref_team_ids(match)
    initials = get_match_ref_initials(match)
    referees = []
    for slot in range(max(len(team_ids), len(initials))):
        team_id = (team_ids[slot] if slot < len(team_ids) else "").strip()
        initial = (initials[slot] if slot < len(initials) else "").strip()
        if team_id or initial:
            referees.append(teams.reference(team_id or None, initial))
    cameras = (
        Camera.query.filter_by(match_uuid=match.uuid, event=match.event, status="SUCCESS")
        .order_by(Camera.name.asc())
        .all()
    )
    footage = []
    for camera in cameras:
        world_times, video_offsets = get_camera_timepoint_arrays(camera)
        footage.append(
            {
                "id": camera.uuid,
                "name": camera.name,
                "youtube_url": camera.link,
                "anchors": [
                    {"world_time": world_time, "video_offset": video_offset}
                    for world_time, video_offset in zip(world_times, video_offsets)
                ],
            }
        )
    return match_json(match, teams, points) | {
        "sets_to_win": match.nsets,
        "stones": _stones_json(match),
        "referees": referees,
        "footage": footage,
        "points": [{"id": point.uuid} | point_json(point, match.set_type) for point in points],
    }


def _scoreboard_side_json(side) -> dict:
    return {"name": side.name, "shortname": side.shortname}


def _scoreboard_summary_json(scoreboard_match) -> dict | None:
    if scoreboard_match is None:
        return None
    match = scoreboard_match.match
    return {
        "id": match.uuid,
        "name": match.name,
        "team1": _scoreboard_side_json(scoreboard_match.team1),
        "team2": _scoreboard_side_json(scoreboard_match.team2),
        "start_time": iso(match.nominal_start_time),
        "winner": _enum_value(match.match_winner),
    }


def scoreboard_json(state) -> dict:
    """The field's match in progress, or ``match: null`` plus the previous and next matches."""
    if state.active is None:
        return {
            "match": None,
            "previous": _scoreboard_summary_json(state.previous),
            "next": _scoreboard_summary_json(state.next),
        }
    match = state.active.match
    return {
        "match": {
            "id": match.uuid,
            "team1": _scoreboard_side_json(state.active.team1),
            "team2": _scoreboard_side_json(state.active.team2),
            "set_type": _enum_value(match.set_type),
            "sets": [
                {"number": number, "team1_score": team1_score, "team2_score": team2_score}
                for number, (team1_score, team2_score) in state.set_scores.items()
            ],
            "stones": _stones_json(match),
            "points": [point_json(point, match.set_type) for point in state.points],
        }
    }


def scope_teams_json(scope: Scope) -> list[dict]:
    """Confirmed teams of a registration scope, ordered by name."""
    registrations = team_registrations_for_scope(scope)
    team_ids = [r.team for r in registrations]
    teams = {t.id: t for t in Team.query.filter(Team.id.in_(team_ids)).all()} if team_ids else {}
    player_counts = collections.Counter(
        r.team for r in player_registrations_for_scope(scope, statuses=[RegistrationStatus.CONFIRMED]) if r.team
    )
    rows = []
    for registration in registrations:
        team = teams.get(registration.team)
        rows.append(
            {
                "id": registration.team,
                "name": registration.pseudonym or (team.name if team else registration.team),
                "shortname": registration.shortname or None,
                "team_name": team.name if team else None,
                "profile_photo": _photo_path(team.profile_photo) if team else None,
                "player_count": player_counts[registration.team],
                "registered_at": iso(registration.registered_at),
            }
        )
    return sorted(rows, key=lambda row: row["name"].lower())


def scope_players_json(scope: Scope, team_id: str | None = None) -> list[dict]:
    """Confirmed players of a registration scope (optionally one team's roster), ordered by name."""
    registrations = player_registrations_for_scope(scope, team_id=team_id, statuses=[RegistrationStatus.CONFIRMED])
    player_ids = [r.player for r in registrations]
    players = {p.id: p for p in Player.query.filter(Player.id.in_(player_ids)).all()} if player_ids else {}
    teams = TeamNames(scope)
    rows = []
    for registration in registrations:
        player = players.get(registration.player)
        rows.append(
            {
                "id": registration.player,
                "name": player.name if player else registration.player,
                "jersey_name": registration.jersey_name,
                "jersey_number": registration.jersey_number,
                "profile_photo": _photo_path(player.profile_photo) if player else None,
                "team": teams.reference(registration.team) if registration.team else None,
            }
        )
    return sorted(rows, key=lambda row: row["name"].lower())


def standings_json(team_stats: list[dict]) -> list[dict]:
    """Rows of :func:`~app.services.team_stats_service.compute_team_stats`, ordered by team name.

    Teams that only exist as schedule placeholders have no id.
    """
    stat_ids = [row["id"] for row in team_stats]
    real_team_ids = {t.id for t in Team.query.filter(Team.id.in_(stat_ids)).all()} if stat_ids else set()
    rows = [
        {
            "team": {
                "id": row["id"] if row["id"] in real_team_ids else None,
                "name": row["pseudonym"],
                "shortname": row["shortname"],
            },
            "matches_won": row["matches_won"],
            "matches_lost": row["matches_lost"],
            "points_won": row["points_won"],
            "points_lost": row["points_lost"],
        }
        for row in team_stats
    ]
    return sorted(rows, key=lambda row: str(row["team"]["name"]).lower())


def side_competition_json(side_competition, registrant_count: int) -> dict:
    return {
        "id": side_competition.id,
        "name": side_competition.name,
        "type": _enum_value(side_competition.type),
        "description": side_competition.description,
        "registration_open": bool(side_competition.registration_open),
        "active": bool(side_competition.active),
        "finalized": bool(side_competition.finalized),
        "allowed_weapons": side_competition.allowed_weapon_names(),
        "track_losses": bool(side_competition.track_losses),
        "track_chain_losses": bool(side_competition.track_chain_losses),
        "track_break_losses": bool(side_competition.track_break_losses),
        "results_available": side_competition.results_page_enabled(),
        "registrant_count": registrant_count,
        "created_at": iso(side_competition.created_at),
    }


def side_competition_detail_json(side_competition, registrants) -> dict:
    """*registrants* are ``(SideCompRegistration, Player)`` pairs; incomplete registrations are left out."""
    complete = [(registration, player) for registration, player in registrants if registration.is_complete()]
    return {
        **side_competition_json(side_competition, len(complete)),
        "registrants": [
            {
                "entry_number": registration.entry_number,
                "player": {"id": registration.player, "name": player.name if player else registration.player},
                "weapon": registration.weapon_name(),
                "registered_at": iso(registration.registered_at),
            }
            for registration, player in complete
        ],
    }


def side_competition_results_json(standings: dict, teams: TeamNames) -> dict:
    """Public tables of :meth:`~app.services.sidecomp_service.SideCompService.standings`."""
    return {
        "finalized": standings["finalized"],
        "tables": [
            {
                "id": table["id"],
                "title": table["title"],
                "track_losses": table["track_losses"],
                "rows": [
                    {
                        "rank": row["rank"],
                        "entry_number": row["entry_number"],
                        "weapon": row["weapon"],
                        "player": {"id": row["player_id"], "name": row["display_name"]},
                        "team": teams.reference(row["team_id"]) if row["team_id"] else None,
                        "wins": row["wins"],
                        "losses": row["losses"],
                        "win_percentage": row["win_percentage"],
                    }
                    for row in table["rows"]
                ],
            }
            for table in standings["tables"]
        ],
    }


def _published_scopes(registrations) -> tuple[dict, dict]:
    """Published tournaments and leagues referenced by *registrations*, keyed by URL."""
    event_urls = {r.event for r in registrations if r.event}
    league_urls = {r.league_id for r in registrations if r.league_id}
    tournaments = (
        {t.url: t for t in Tournament.query.filter(Tournament.url.in_(event_urls), Tournament.published).all()}
        if event_urls
        else {}
    )
    leagues = (
        {league.url: league for league in League.query.filter(League.url.in_(league_urls), League.published).all()}
        if league_urls
        else {}
    )
    return tournaments, leagues


def player_registrations_json(player_id: str) -> list[dict]:
    """The player's confirmed registrations in published tournaments and leagues."""
    registrations = PlayerRegistration.query.filter_by(player=player_id, status=RegistrationStatus.CONFIRMED).all()
    tournaments, leagues = _published_scopes(registrations)
    rows = []
    for registration in registrations:
        tournament = tournaments.get(registration.event)
        league = leagues.get(registration.league_id)
        if tournament is None and league is None:
            continue
        team = None
        if registration.team:
            scope = Scope.event(tournament.url) if tournament else Scope.league(league.url)
            team = TeamNames(scope).reference(registration.team)
        rows.append(
            {
                "tournament": _reference(tournament),
                "league": _reference(league),
                "team": team,
                "jersey_name": registration.jersey_name,
                "jersey_number": registration.jersey_number,
            }
        )
    return rows


def team_registrations_json(team_id: str) -> list[dict]:
    """The team's confirmed registrations in published tournaments and leagues, with their rosters."""
    registrations = TeamRegistration.query.filter_by(team=team_id, status=TeamRegistrationStatus.CONFIRMED).all()
    tournaments, leagues = _published_scopes(registrations)
    rows = []
    for registration in registrations:
        tournament = tournaments.get(registration.event)
        league = leagues.get(registration.league_id)
        if tournament is None and league is None:
            continue
        scope = Scope.event(tournament.url) if tournament else Scope.league(league.url)
        rows.append(
            {
                "tournament": _reference(tournament),
                "league": _reference(league),
                "name": registration.pseudonym,
                "shortname": registration.shortname or None,
                "players": scope_players_json(scope, team_id=team_id),
            }
        )
    return rows
