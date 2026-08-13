"""League-scoped and event-scoped registrations must behave the same.

These paths historically filtered only on ``event=tournament_url`` and silently
ignored league registrants (``league_id`` set, ``event`` NULL). Each test builds
a standalone event and a league event with equivalent confirmed registrations
and asserts identical outcomes.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import pytest

from app.domain.enums import MatchStatus, RegistrationStatus, ScheduleType, TeamRegistrationStatus
from app.filters import team_by_pseudonym_for_tournament, team_registration_for_tournament as filter_team_reg
from app.services.match_start_eligibility import get_current_user_explanation
from app.services.registration_resolver import (
    player_registration_for_tournament,
    player_registrations_for_tournament,
    team_registration_for_tournament,
)
from app.services.schedule_import_export_service import ScheduleImportExportService
from app.utils.helpers import can_head_ref_match, get_team_display_name_for_event
from app.utils.player_helpers import get_player_display_name
from models import (
    League,
    Match,
    Player,
    PlayerRegistration,
    Team,
    TeamRegistration,
    Tournament,
    db,
)
from tests.utils import make_registrable_config


def _now():
    return datetime.now(timezone.utc)


def _make_standalone_event(prefix: str) -> Tournament:
    cfg = make_registrable_config(team_registration_open=True, player_registration_open=True)
    t = Tournament(
        url=f"{prefix}-sa",
        name=f"{prefix} standalone",
        start_date=_now(),
        end_date=_now() + timedelta(days=1),
        location="L",
        max_field_size=14,
        published=True,
        schedule_published=True,
        registrable_config_id=cfg.id,
        head_refs_allow_anyone=False,
        head_refs_allow_reffing_teams=False,
    )
    db.session.add(t)
    db.session.flush()
    return t


def _make_league_event(prefix: str) -> Tournament:
    cfg = make_registrable_config(team_registration_open=True, player_registration_open=True)
    league = League(url=f"{prefix}-lg", name=f"{prefix} league", registrable_config_id=cfg.id)
    db.session.add(league)
    db.session.flush()
    t = Tournament(
        url=f"{prefix}-evt",
        name=f"{prefix} league event",
        start_date=_now(),
        end_date=_now() + timedelta(days=1),
        location="L",
        max_field_size=14,
        published=True,
        schedule_published=True,
        league_id=league.url,
        head_refs_allow_anyone=False,
        head_refs_allow_reffing_teams=False,
    )
    db.session.add(t)
    db.session.flush()
    return t


def _ensure_team(team_id: str, name: str | None = None) -> Team:
    team = Team.query.get(team_id)
    if team is None:
        team = Team(id=team_id, name=name or team_id, pw_hash="x")
        db.session.add(team)
        db.session.flush()
    return team


def _ensure_player(player_id: str, name: str | None = None) -> Player:
    player = Player.query.get(player_id)
    if player is None:
        player = Player(id=player_id, name=name or player_id, pw_hash="x")
        db.session.add(player)
        db.session.flush()
    return player


def _register_team(tournament: Tournament, team_id: str, *, pseudonym: str) -> None:
    _ensure_team(team_id)
    kwargs = {
        "team": team_id,
        "pseudonym": pseudonym,
        "status": TeamRegistrationStatus.CONFIRMED,
    }
    if tournament.league_id:
        kwargs["league_id"] = tournament.league_id
    else:
        kwargs["event"] = tournament.url
    db.session.add(TeamRegistration(**kwargs))
    db.session.flush()


def _register_player(
    tournament: Tournament,
    player_id: str,
    *,
    team_id: str | None = None,
    jersey_name: str | None = None,
    jersey_number: str | None = None,
) -> None:
    _ensure_player(player_id)
    if team_id:
        _ensure_team(team_id)
    kwargs = {
        "player": player_id,
        "team": team_id,
        "status": RegistrationStatus.CONFIRMED,
        "jersey_name": jersey_name,
        "jersey_number": jersey_number,
    }
    if tournament.league_id:
        kwargs["league_id"] = tournament.league_id
    else:
        kwargs["event"] = tournament.url
    db.session.add(PlayerRegistration(**kwargs))
    db.session.flush()


def _pair(prefix: str) -> tuple[Tournament, Tournament]:
    """Return (standalone_event, league_event) with distinct URL prefixes."""
    return _make_standalone_event(f"{prefix}-s"), _make_league_event(f"{prefix}-l")


@pytest.mark.unit
def test_can_head_ref_allow_anyone_league_and_standalone(app, test_db):
    with app.app_context():
        sa, lg = _pair("href-any")
        for t in (sa, lg):
            t.head_refs_allow_anyone = True
            _register_player(t, f"p-{t.url}", team_id=f"tm-{t.url}")
        db.session.commit()

        assert can_head_ref_match(sa.url, f"p-{sa.url}") is True
        assert can_head_ref_match(lg.url, f"p-{lg.url}") is True
        assert can_head_ref_match(sa.url, "stranger") is False
        assert can_head_ref_match(lg.url, "stranger") is False


@pytest.mark.unit
def test_can_head_ref_reffing_teams_league_and_standalone(app, test_db):
    with app.app_context():
        sa, lg = _pair("href-ref")
        for t in (sa, lg):
            t.head_refs_allow_reffing_teams = True
            team_id = f"refteam-{t.url}"
            _register_player(t, f"refp-{t.url}", team_id=team_id)
            # Match whose assigned ref team is the player's team.
            _ensure_team("a")
            _ensure_team("b")
            m = Match(
                name=f"M-{t.url}",
                event=t.url,
                schedule_type=ScheduleType.STATIC,
                set_type="SETS",
                status=MatchStatus.NOT_STARTED,
                team1="a",
                team2="b",
                nominal_length=60,
            )
            db.session.add(m)
            db.session.flush()
            from app.services.dual_write import set_match_referees

            set_match_referees(m, [team_id], [team_id])
            db.session.commit()

            assert can_head_ref_match(t.url, f"refp-{t.url}", match=m) is True
            assert can_head_ref_match(t.url, "other-player", match=m) is False


@pytest.mark.unit
def test_team_display_name_league_and_standalone(app, test_db):
    with app.app_context():
        sa, lg = _pair("tdisp")
        for t, pseudo in ((sa, "Standalone Pseudos"), (lg, "League Pseudos")):
            _register_team(t, f"team-{t.url}", pseudonym=pseudo)
        db.session.commit()

        assert get_team_display_name_for_event(sa.url, f"team-{sa.url}") == "Standalone Pseudos"
        assert get_team_display_name_for_event(lg.url, f"team-{lg.url}") == "League Pseudos"


@pytest.mark.unit
def test_team_registration_helpers_league_and_standalone(app, test_db):
    with app.app_context():
        sa, lg = _pair("treg")
        for t, pseudo in ((sa, "SA Club"), (lg, "LG Club")):
            _register_team(t, f"tid-{t.url}", pseudonym=pseudo)
        db.session.commit()

        for t, pseudo in ((sa, "SA Club"), (lg, "LG Club")):
            tid = f"tid-{t.url}"
            reg = team_registration_for_tournament(t, tid)
            assert reg is not None
            assert reg.pseudonym == pseudo
            assert filter_team_reg(tid, t.url) is not None
            assert filter_team_reg(tid, t.url).pseudonym == pseudo
            by_pseudo = team_by_pseudonym_for_tournament(pseudo, t.url)
            assert by_pseudo is not None
            assert by_pseudo.team == tid


@pytest.mark.unit
def test_player_registration_and_roster_league_and_standalone(app, test_db):
    with app.app_context():
        sa, lg = _pair("preg")
        for t in (sa, lg):
            team_id = f"roster-{t.url}"
            _register_team(t, team_id, pseudonym=team_id)
            _register_player(
                t,
                f"pl-{t.url}-1",
                team_id=team_id,
                jersey_name="Ace",
                jersey_number="7",
            )
            _register_player(
                t,
                f"pl-{t.url}-2",
                team_id=team_id,
                jersey_name="Bee",
                jersey_number="11",
            )
        db.session.commit()

        for t in (sa, lg):
            team_id = f"roster-{t.url}"
            pr = player_registration_for_tournament(t, f"pl-{t.url}-1", statuses=[RegistrationStatus.CONFIRMED])
            assert pr is not None
            assert pr.team == team_id
            roster = player_registrations_for_tournament(t, team_id=team_id, statuses=[RegistrationStatus.CONFIRMED])
            assert {r.player for r in roster} == {f"pl-{t.url}-1", f"pl-{t.url}-2"}


@pytest.mark.unit
def test_player_display_name_jersey_league_and_standalone(app, test_db):
    with app.app_context():
        sa, lg = _pair("jers")
        for t in (sa, lg):
            _register_player(
                t,
                f"jp-{t.url}",
                team_id=f"jt-{t.url}",
                jersey_name="Flash",
                jersey_number="9",
            )
        db.session.commit()

        for t in (sa, lg):
            name, display = get_player_display_name(f"jp-{t.url}", t.url)
            assert name  # real player name
            assert display is not None
            assert "Flash" in display or "9" in display


@pytest.mark.unit
def test_current_user_explanation_registered_league_and_standalone(app, test_db):
    with app.app_context():
        sa, lg = _pair("expl")
        for t, pseudo in ((sa, "SA Squad"), (lg, "LG Squad")):
            team_id = f"exteam-{t.url}"
            _register_team(t, team_id, pseudonym=pseudo)
            _register_player(t, f"exp-{t.url}", team_id=team_id)
        db.session.commit()

        for t, pseudo in ((sa, "SA Squad"), (lg, "LG Squad")):
            player = Player.query.get(f"exp-{t.url}")
            lines = get_current_user_explanation(t.url, player)
            joined = " ".join(lines).lower()
            assert "not registered" not in joined
            assert pseudo.lower() in joined or "registered" in joined


@pytest.mark.unit
def test_schedule_import_registered_teams_league_and_standalone(app, test_db):
    with app.app_context():
        sa, lg = _pair("imp")
        for t in (sa, lg):
            _register_team(t, f"ok-{t.url}", pseudonym=f"ok-{t.url}")
        db.session.commit()

        for t in (sa, lg):
            tags_out, matches_out, warnings, auto_tag_names = ScheduleImportExportService._rewrite_unknown_team_refs(
                t,
                tags_data=[{"name": "T", "team": f"ok-{t.url}"}],
                matches_data=[
                    {
                        "team1_initial": f"ok-{t.url}",
                        "team2_initial": "missing-team",
                        "refs_initial": "",
                    }
                ],
            )
            # missing-team should be rewritten to a tag reference with a warning;
            # registered ok-* teams (event- or league-scoped) stay untouched.
            assert matches_out[0]["team1_initial"] == f"ok-{t.url}"
            assert matches_out[0]["team2_initial"] == "tag::missing-team"
            assert auto_tag_names == {"missing-team"}
            assert any("missing-team" in w for w in warnings)
            assert not any(f"ok-{t.url}" in w for w in warnings)
            assert tags_out[0]["team"] == f"ok-{t.url}"
