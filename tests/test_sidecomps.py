"""Tests for side competition models, services, and routes."""

import pytest
from sqlalchemy.exc import IntegrityError

from app.domain.enums import RegistrationStatus
from app.error_values import Err, Ok
from models import (
    Player,
    PlayerRegistration,
    SideComp,
    SideCompRegistration,
    SideCompResult,
    TO,
    db,
)
from tests.utils import login_as


def _make_player(player_id="p_alice", name="Alice"):
    p = Player(id=player_id, name=name, pw_hash="dummy_hash")
    p.set_password("testpass")
    db.session.add(p)
    db.session.commit()
    return p


def _confirm_event_registration(tournament_url, player_id, team_id=None):
    reg = PlayerRegistration(
        event=tournament_url,
        player=player_id,
        team=team_id,
        jersey_number="0",
        jersey_name="N/A",
        status=RegistrationStatus.CONFIRMED,
        paid=True,
    )
    db.session.add(reg)
    db.session.commit()
    return reg


def _make_league_sidecomp_context(registration_open=False):
    from datetime import datetime, timedelta, timezone

    from app.domain.enums import TeamRegistrationStatus
    from models import League, Team, TeamRegistration, Tournament
    from tests.utils import make_registrable_config

    cfg = make_registrable_config(team_registration_open=True, player_registration_open=True)
    league = League(url="lg", name="LG", registrable_config_id=cfg.id)
    db.session.add(league)
    db.session.flush()

    tournament = Tournament(
        url="lg-evt",
        name="League Event",
        start_date=datetime.now(timezone.utc),
        end_date=datetime.now(timezone.utc) + timedelta(days=1),
        league_id=league.url,
    )
    db.session.add(tournament)
    db.session.flush()

    to_user = _make_player("lg_to", "LG TO")
    db.session.add(TO(user_id=to_user.id, user_type="player", event=None, league_id=league.url))

    team = Team(id="lg_team", name="LG Team", pw_hash="dummy_hash")
    db.session.add(team)
    db.session.flush()
    db.session.add(
        TeamRegistration(
            event=None,
            league_id=league.url,
            team=team.id,
            pseudonym="LG Pseudonym",
            status=TeamRegistrationStatus.CONFIRMED,
        )
    )

    player = _make_player("lg_p1", "LG P1")
    db.session.add(
        PlayerRegistration(
            event=None,
            league_id=league.url,
            player=player.id,
            team=team.id,
            jersey_number="7",
            jersey_name="P1",
            status=RegistrationStatus.CONFIRMED,
            paid=True,
        )
    )

    sidecomp = SideComp(event=tournament.url, name="LG SC", type="DUELING", registration_open=registration_open)
    db.session.add(sidecomp)
    db.session.commit()

    return {
        "tournament": tournament,
        "to_user": to_user,
        "player": player,
        "sidecomp": sidecomp,
    }


def test_sidecomp_has_created_at(test_db, tournament):
    sc = SideComp(event=tournament.url, name="Dueling 1v1", type="DUELING")
    db.session.add(sc)
    db.session.commit()
    assert sc.created_at is not None


def test_sidecomp_registration_unique_per_player(test_db, tournament):
    p = _make_player()
    sc = SideComp(event=tournament.url, name="Chain", type="CHAIN_BREAKING")
    db.session.add(sc)
    db.session.flush()
    db.session.add(SideCompRegistration(comp=sc.id, player=p.id, entry_number=1, weapon=0))
    db.session.commit()
    db.session.add(SideCompRegistration(comp=sc.id, player=p.id, entry_number=2, weapon=0))
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


def _make_to(tournament_url, user_id, user_type="player"):
    db.session.add(TO(event=tournament_url, user_id=user_id, user_type=user_type))
    db.session.commit()


def test_sidecomp_service_create_to_succeeds(test_db, tournament):
    p = _make_player("to_user", "TO User")
    _make_to(tournament.url, p.id)

    from app.services.sidecomp_service import SideCompService

    res = SideCompService.create(
        tournament.url,
        actor_user_id=p.id,
        actor_user_type="player",
        name="Dueling 1v1",
        type="DUELING",
    )
    assert isinstance(res, Ok)
    sc = res.unwrap()
    assert sc.name == "Dueling 1v1"
    assert sc.type == "DUELING"
    assert sc.event == tournament.url


def test_sidecomp_service_create_non_to_forbidden(test_db, tournament):
    p = _make_player("not_to", "Non TO")

    from app.services.sidecomp_service import SideCompService

    res = SideCompService.create(
        tournament.url,
        actor_user_id=p.id,
        actor_user_type="player",
        name="X",
        type="DUELING",
    )
    assert isinstance(res, Err)
    assert res.unwrap_err().status_code == 403


def test_sidecomp_service_create_invalid_type(test_db, tournament):
    p = _make_player("to_user", "TO User")
    _make_to(tournament.url, p.id)

    from app.services.sidecomp_service import SideCompService

    res = SideCompService.create(
        tournament.url,
        actor_user_id=p.id,
        actor_user_type="player",
        name="X",
        type="NOT_A_REAL_TYPE",
    )
    assert isinstance(res, Err)
    assert res.unwrap_err().status_code == 400


def test_sidecomp_service_create_empty_name(test_db, tournament):
    p = _make_player("to_user", "TO User")
    _make_to(tournament.url, p.id)

    from app.services.sidecomp_service import SideCompService

    res = SideCompService.create(
        tournament.url,
        actor_user_id=p.id,
        actor_user_type="player",
        name="   ",
        type="DUELING",
    )
    assert isinstance(res, Err)
    assert res.unwrap_err().status_code == 400


def test_sidecomp_list_for_event_returns_all(test_db, tournament):
    sc1 = SideComp(event=tournament.url, name="A", type="DUELING")
    sc2 = SideComp(event=tournament.url, name="B", type="OTHER")
    db.session.add_all([sc1, sc2])
    db.session.commit()

    from app.services.sidecomp_service import SideCompService

    rows = SideCompService.list_for_event(tournament.url)
    names = sorted(r.name for r in rows)
    assert names == ["A", "B"]


def test_sidecomp_get_with_registrants(test_db, tournament):
    p = _make_player()
    sc = SideComp(event=tournament.url, name="C", type="CHAIN_BREAKING")
    db.session.add(sc)
    db.session.flush()
    db.session.add(SideCompRegistration(comp=sc.id, player=p.id, entry_number=1, weapon=0))
    db.session.commit()

    from app.services.sidecomp_service import SideCompService

    res = SideCompService.get_with_registrants(sc.id)
    assert isinstance(res, Ok)
    comp, registrants = res.unwrap()
    assert comp.id == sc.id
    assert len(registrants) == 1
    reg, player = registrants[0]
    assert reg.player == p.id
    assert player.id == p.id


def test_sidecomp_get_with_registrants_not_found(test_db):
    from app.services.sidecomp_service import SideCompService

    res = SideCompService.get_with_registrants(99999)
    assert isinstance(res, Err)
    assert res.unwrap_err().status_code == 404


def test_sidecomp_update_changes_name_and_type(test_db, tournament):
    p = _make_player("to_user", "TO User")
    _make_to(tournament.url, p.id)
    sc = SideComp(event=tournament.url, name="Old", type="DUELING")
    db.session.add(sc)
    db.session.commit()

    from app.services.sidecomp_service import SideCompService

    res = SideCompService.update(
        sc.id,
        actor_user_id=p.id,
        actor_user_type="player",
        name="New",
        type="OTHER",
    )
    assert isinstance(res, Ok)
    db.session.refresh(sc)
    assert sc.name == "New"
    assert sc.type == "OTHER"


def test_sidecomp_update_partial(test_db, tournament):
    p = _make_player("to_user", "TO User")
    _make_to(tournament.url, p.id)
    sc = SideComp(event=tournament.url, name="Old", type="DUELING")
    db.session.add(sc)
    db.session.commit()

    from app.services.sidecomp_service import SideCompService

    res = SideCompService.update(
        sc.id,
        actor_user_id=p.id,
        actor_user_type="player",
        name="NewName",
    )
    assert isinstance(res, Ok)
    db.session.refresh(sc)
    assert sc.name == "NewName"
    assert sc.type == "DUELING"


def test_sidecomp_update_non_to_forbidden(test_db, tournament):
    p = _make_player("non_to", "Non TO")
    sc = SideComp(event=tournament.url, name="X", type="DUELING")
    db.session.add(sc)
    db.session.commit()

    from app.services.sidecomp_service import SideCompService

    res = SideCompService.update(
        sc.id,
        actor_user_id=p.id,
        actor_user_type="player",
        name="Y",
    )
    assert isinstance(res, Err)
    assert res.unwrap_err().status_code == 403


def test_sidecomp_delete_cascades(test_db, tournament):
    to_user = _make_player("to_user", "TO User")
    other = _make_player("other_player", "Other")
    _make_to(tournament.url, to_user.id)
    sc = SideComp(event=tournament.url, name="Z", type="DUELING")
    db.session.add(sc)
    db.session.flush()
    db.session.add(SideCompRegistration(comp=sc.id, player=other.id, entry_number=1, weapon=0))
    db.session.flush()
    reg = SideCompRegistration.query.filter_by(comp=sc.id, player=other.id).first()
    db.session.add(SideCompResult(comp=sc.id, player=reg.id, points=1, ref=other.id))
    db.session.commit()
    comp_id = sc.id

    from app.services.sidecomp_service import SideCompService

    res = SideCompService.delete(comp_id, actor_user_id=to_user.id, actor_user_type="player")
    assert isinstance(res, Ok)
    assert SideComp.query.get(comp_id) is None
    assert SideCompRegistration.query.filter_by(comp=comp_id).count() == 0
    assert SideCompResult.query.filter_by(comp=comp_id).count() == 0


def test_register_player_succeeds_when_event_registered(test_db, tournament):
    p = _make_player()
    _confirm_event_registration(tournament.url, p.id)
    sc = SideComp(event=tournament.url, name="A", type="DUELING", registration_open=True)
    db.session.add(sc)
    db.session.commit()

    from app.services.sidecomp_service import SideCompService

    res = SideCompService.register_player(sc.id, player_id=p.id, weapon="CHAIN")
    assert isinstance(res, Ok)
    reg = res.unwrap()
    assert reg.comp == sc.id
    assert reg.player == p.id
    assert reg.registered_by_to is False


def test_register_player_succeeds_with_league_scoped_registration(test_db):
    ctx = _make_league_sidecomp_context(registration_open=True)

    from app.services.sidecomp_service import SideCompService

    res = SideCompService.register_player(ctx["sidecomp"].id, player_id=ctx["player"].id, weapon="CHAIN")

    assert isinstance(res, Ok)
    reg = res.unwrap()
    assert reg.comp == ctx["sidecomp"].id
    assert reg.player == ctx["player"].id
    assert reg.registered_by_to is False


def test_register_player_no_event_registration(test_db, tournament):
    p = _make_player()
    sc = SideComp(event=tournament.url, name="A", type="DUELING", registration_open=True)
    db.session.add(sc)
    db.session.commit()

    from app.services.sidecomp_service import SideCompService

    res = SideCompService.register_player(sc.id, player_id=p.id, weapon="CHAIN")
    assert isinstance(res, Err)
    assert res.unwrap_err().status_code == 400


def test_register_player_cancelled_event_registration(test_db, tournament):
    p = _make_player()
    reg = _confirm_event_registration(tournament.url, p.id)
    reg.status = RegistrationStatus.CANCELLED
    db.session.commit()
    sc = SideComp(event=tournament.url, name="A", type="DUELING", registration_open=True)
    db.session.add(sc)
    db.session.commit()

    from app.services.sidecomp_service import SideCompService

    res = SideCompService.register_player(sc.id, player_id=p.id, weapon="CHAIN")
    assert isinstance(res, Err)
    assert res.unwrap_err().status_code == 400


def test_register_player_duplicate_rejected(test_db, tournament):
    p = _make_player()
    _confirm_event_registration(tournament.url, p.id)
    sc = SideComp(event=tournament.url, name="A", type="DUELING", registration_open=True)
    db.session.add(sc)
    db.session.commit()

    from app.services.sidecomp_service import SideCompService

    SideCompService.register_player(sc.id, player_id=p.id, weapon="CHAIN")
    res2 = SideCompService.register_player(sc.id, player_id=p.id, weapon="CHAIN")
    assert isinstance(res2, Err)
    assert res2.unwrap_err().status_code == 400


def test_register_player_unaffiliated_succeeds(test_db, tournament):
    """Players with no team_id on their event registration can still register."""
    p = _make_player()
    _confirm_event_registration(tournament.url, p.id, team_id=None)
    sc = SideComp(event=tournament.url, name="A", type="DUELING", registration_open=True)
    db.session.add(sc)
    db.session.commit()

    from app.services.sidecomp_service import SideCompService

    res = SideCompService.register_player(sc.id, player_id=p.id, weapon="CHAIN")
    assert isinstance(res, Ok)


def test_deregister_player_removes_row(test_db, tournament):
    p = _make_player()
    _confirm_event_registration(tournament.url, p.id)
    sc = SideComp(event=tournament.url, name="A", type="DUELING")
    db.session.add(sc)
    db.session.flush()
    db.session.add(SideCompRegistration(comp=sc.id, player=p.id, entry_number=1, weapon=0))
    db.session.commit()

    from app.services.sidecomp_service import SideCompService

    res = SideCompService.deregister_player(sc.id, player_id=p.id)
    assert isinstance(res, Ok)
    assert SideCompRegistration.query.filter_by(comp=sc.id, player=p.id).first() is None


def test_deregister_player_idempotent_when_missing(test_db, tournament):
    p = _make_player()
    sc = SideComp(event=tournament.url, name="A", type="DUELING")
    db.session.add(sc)
    db.session.commit()

    from app.services.sidecomp_service import SideCompService

    res = SideCompService.deregister_player(sc.id, player_id=p.id)
    assert isinstance(res, Ok)


def test_register_player_as_to_succeeds(test_db, tournament):
    to_user = _make_player("to_user", "TO User")
    other = _make_player("p_other", "Other")
    _make_to(tournament.url, to_user.id)
    _confirm_event_registration(tournament.url, other.id)
    sc = SideComp(event=tournament.url, name="A", type="DUELING")
    db.session.add(sc)
    db.session.commit()

    from app.services.sidecomp_service import SideCompService

    res = SideCompService.register_player_as_to(
        sc.id,
        actor_user_id=to_user.id,
        actor_user_type="player",
        player_id=other.id,
        weapon="CHAIN",
    )
    assert isinstance(res, Ok)
    reg = res.unwrap()
    assert reg.registered_by_to is True


def test_register_player_as_to_succeeds_with_league_scoped_registration(test_db):
    ctx = _make_league_sidecomp_context()

    from app.services.sidecomp_service import SideCompService

    res = SideCompService.register_player_as_to(
        ctx["sidecomp"].id,
        actor_user_id=ctx["to_user"].id,
        actor_user_type="player",
        player_id=ctx["player"].id,
        weapon="CHAIN",
    )

    assert isinstance(res, Ok)
    reg = res.unwrap()
    assert reg.comp == ctx["sidecomp"].id
    assert reg.player == ctx["player"].id
    assert reg.registered_by_to is True


def test_register_player_as_to_non_to_forbidden(test_db, tournament):
    actor = _make_player("not_to", "Not TO")
    target = _make_player("p_other", "Other")
    _confirm_event_registration(tournament.url, target.id)
    sc = SideComp(event=tournament.url, name="A", type="DUELING")
    db.session.add(sc)
    db.session.commit()

    from app.services.sidecomp_service import SideCompService

    res = SideCompService.register_player_as_to(
        sc.id,
        actor_user_id=actor.id,
        actor_user_type="player",
        player_id=target.id,
        weapon="CHAIN",
    )
    assert isinstance(res, Err)
    assert res.unwrap_err().status_code == 403


def test_register_player_as_to_target_not_event_registered(test_db, tournament):
    to_user = _make_player("to_user", "TO User")
    target = _make_player("p_other", "Other")
    _make_to(tournament.url, to_user.id)
    sc = SideComp(event=tournament.url, name="A", type="DUELING")
    db.session.add(sc)
    db.session.commit()

    from app.services.sidecomp_service import SideCompService

    res = SideCompService.register_player_as_to(
        sc.id,
        actor_user_id=to_user.id,
        actor_user_type="player",
        player_id=target.id,
        weapon="CHAIN",
    )
    assert isinstance(res, Err)
    assert res.unwrap_err().status_code == 400


def test_deregister_player_as_to_idempotent(test_db, tournament):
    to_user = _make_player("to_user", "TO User")
    target = _make_player("p_other", "Other")
    _make_to(tournament.url, to_user.id)
    sc = SideComp(event=tournament.url, name="A", type="DUELING")
    db.session.add(sc)
    db.session.commit()

    from app.services.sidecomp_service import SideCompService

    res = SideCompService.deregister_player_as_to(
        sc.id,
        actor_user_id=to_user.id,
        actor_user_type="player",
        player_id=target.id,
    )
    assert isinstance(res, Ok)


def test_register_player_rejected_when_closed(test_db, tournament):
    """Self-registration is blocked when the comp is not open, even with event reg."""
    p = _make_player()
    _confirm_event_registration(tournament.url, p.id)
    sc = SideComp(event=tournament.url, name="A", type="DUELING")
    db.session.add(sc)
    db.session.commit()

    from app.services.sidecomp_service import SideCompService

    res = SideCompService.register_player(sc.id, player_id=p.id, weapon="CHAIN")
    assert isinstance(res, Err)
    assert res.unwrap_err().status_code == 400


def test_register_player_succeeds_when_opened(test_db, tournament):
    """Opening a closed comp via update unblocks player self-registration."""
    to_user = _make_player("to_user", "TO User")
    p = _make_player("p_alice2", "Alice2")
    _make_to(tournament.url, to_user.id)
    _confirm_event_registration(tournament.url, p.id)
    sc = SideComp(event=tournament.url, name="A", type="DUELING")
    db.session.add(sc)
    db.session.commit()

    from app.services.sidecomp_service import SideCompService

    closed = SideCompService.register_player(sc.id, player_id=p.id, weapon="CHAIN")
    assert isinstance(closed, Err)

    upd = SideCompService.update(
        sc.id,
        actor_user_id=to_user.id,
        actor_user_type="player",
        registration_open=True,
    )
    assert isinstance(upd, Ok)

    res = SideCompService.register_player(sc.id, player_id=p.id, weapon="CHAIN")
    assert isinstance(res, Ok)


def test_register_player_as_to_works_when_closed(test_db, tournament):
    """TO-driven registration must work regardless of registration_open state."""
    to_user = _make_player("to_user", "TO User")
    target = _make_player("p_other", "Other")
    _make_to(tournament.url, to_user.id)
    _confirm_event_registration(tournament.url, target.id)
    sc = SideComp(event=tournament.url, name="A", type="DUELING")
    db.session.add(sc)
    db.session.commit()
    assert sc.registration_open is False

    from app.services.sidecomp_service import SideCompService

    res = SideCompService.register_player_as_to(
        sc.id,
        actor_user_id=to_user.id,
        actor_user_type="player",
        player_id=target.id,
        weapon="CHAIN",
    )
    assert isinstance(res, Ok)


def test_register_player_assigns_entry_number_one(test_db, tournament):
    p = _make_player()
    _confirm_event_registration(tournament.url, p.id)
    sc = SideComp(event=tournament.url, name="A", type="DUELING", registration_open=True)
    db.session.add(sc)
    db.session.commit()

    from app.services.sidecomp_service import SideCompService

    res = SideCompService.register_player(sc.id, player_id=p.id, weapon="CHAIN")
    assert isinstance(res, Ok)
    assert res.unwrap().entry_number == 1


def test_register_player_assigns_sequential_entry_numbers(test_db, tournament):
    p1 = _make_player("p1", "P1")
    p2 = _make_player("p2", "P2")
    _confirm_event_registration(tournament.url, p1.id)
    _confirm_event_registration(tournament.url, p2.id)
    sc = SideComp(event=tournament.url, name="A", type="DUELING", registration_open=True)
    db.session.add(sc)
    db.session.commit()

    from app.services.sidecomp_service import SideCompService

    r1 = SideCompService.register_player(sc.id, player_id=p1.id, weapon="CHAIN").unwrap()
    r2 = SideCompService.register_player(sc.id, player_id=p2.id, weapon="CHAIN").unwrap()
    assert r1.entry_number == 1
    assert r2.entry_number == 2


def test_entry_numbers_do_not_reuse_after_deregister(test_db, tournament):
    p1 = _make_player("p1", "P1")
    p2 = _make_player("p2", "P2")
    p3 = _make_player("p3", "P3")
    _confirm_event_registration(tournament.url, p1.id)
    _confirm_event_registration(tournament.url, p2.id)
    _confirm_event_registration(tournament.url, p3.id)
    sc = SideComp(event=tournament.url, name="A", type="DUELING", registration_open=True)
    db.session.add(sc)
    db.session.commit()

    from app.services.sidecomp_service import SideCompService

    r1 = SideCompService.register_player(sc.id, player_id=p1.id, weapon="CHAIN").unwrap()
    r2 = SideCompService.register_player(sc.id, player_id=p2.id, weapon="CHAIN").unwrap()
    assert r1.entry_number == 1
    assert r2.entry_number == 2

    SideCompService.deregister_player(sc.id, player_id=p1.id)

    r3 = SideCompService.register_player(sc.id, player_id=p3.id, weapon="CHAIN").unwrap()
    assert r3.entry_number == 3


def test_entry_numbers_shared_across_comps_in_tournament(test_db, tournament):
    p1 = _make_player("p1", "P1")
    p2 = _make_player("p2", "P2")
    _confirm_event_registration(tournament.url, p1.id)
    _confirm_event_registration(tournament.url, p2.id)
    sc1 = SideComp(event=tournament.url, name="A", type="DUELING", registration_open=True)
    sc2 = SideComp(event=tournament.url, name="B", type="OTHER", registration_open=True)
    db.session.add_all([sc1, sc2])
    db.session.commit()

    from app.services.sidecomp_service import SideCompService

    assert SideCompService.register_player(sc1.id, player_id=p1.id, weapon="CHAIN").unwrap().entry_number == 1
    assert SideCompService.register_player(sc2.id, player_id=p2.id, weapon="CHAIN").unwrap().entry_number == 2
    assert SideCompService.register_player(sc2.id, player_id=p1.id, weapon="CHAIN").unwrap().entry_number == 1
    assert SideCompService.register_player(sc1.id, player_id=p2.id, weapon="CHAIN").unwrap().entry_number == 2


def test_entry_numbers_shared_across_tournaments_in_league(test_db):
    from datetime import datetime, timezone

    from models import Tournament

    ctx = _make_league_sidecomp_context(registration_open=True)
    second_tournament = Tournament(
        url="lg-evt-2",
        name="League Event 2",
        start_date=datetime.now(timezone.utc),
        league_id=ctx["tournament"].league_id,
    )
    db.session.add(second_tournament)
    db.session.flush()
    other = _make_player("lg_p2", "LG P2")
    db.session.add(
        PlayerRegistration(
            event=None,
            league_id=second_tournament.league_id,
            player=other.id,
            jersey_number="8",
            jersey_name="P2",
            status=RegistrationStatus.CONFIRMED,
            paid=True,
        )
    )
    second_sc = SideComp(event=second_tournament.url, name="LG SC 2", type="DUELING", registration_open=True)
    db.session.add(second_sc)
    db.session.commit()

    from app.services.sidecomp_service import SideCompService

    player = ctx["player"]
    first_sc = ctx["sidecomp"]
    assert SideCompService.register_player(first_sc.id, player_id=player.id, weapon="LONG").unwrap().entry_number == 1
    assert SideCompService.register_player(second_sc.id, player_id=other.id, weapon="LONG").unwrap().entry_number == 2
    assert SideCompService.register_player(second_sc.id, player_id=player.id, weapon="LONG").unwrap().entry_number == 1


def test_entry_numbers_independent_across_standalone_tournaments(test_db, tournament):
    from datetime import datetime, timezone

    from models import Tournament
    from tests.utils import make_registrable_config

    other_tournament = Tournament(
        url="other-evt",
        name="Other",
        registrable_config_id=make_registrable_config().id,
        start_date=datetime.now(timezone.utc),
    )
    db.session.add(other_tournament)
    db.session.commit()

    p1 = _make_player("p1", "P1")
    p2 = _make_player("p2", "P2")
    _confirm_event_registration(tournament.url, p1.id)
    _confirm_event_registration(other_tournament.url, p2.id)
    sc1 = SideComp(event=tournament.url, name="A", type="DUELING", registration_open=True)
    sc2 = SideComp(event=other_tournament.url, name="B", type="DUELING", registration_open=True)
    db.session.add_all([sc1, sc2])
    db.session.commit()

    from app.services.sidecomp_service import SideCompService

    assert SideCompService.register_player(sc1.id, player_id=p1.id, weapon="LONG").unwrap().entry_number == 1
    assert SideCompService.register_player(sc2.id, player_id=p2.id, weapon="LONG").unwrap().entry_number == 1


def test_register_player_as_to_assigns_entry_number(test_db, tournament):
    to_user = _make_player("to_user", "TO User")
    target = _make_player("p_other", "Other")
    _make_to(tournament.url, to_user.id)
    _confirm_event_registration(tournament.url, target.id)
    sc = SideComp(event=tournament.url, name="A", type="DUELING")
    db.session.add(sc)
    db.session.commit()

    from app.services.sidecomp_service import SideCompService

    res = SideCompService.register_player_as_to(
        sc.id,
        actor_user_id=to_user.id,
        actor_user_type="player",
        player_id=target.id,
        weapon="CHAIN",
    )
    assert isinstance(res, Ok)
    assert res.unwrap().entry_number == 1


def test_entry_number_unique_constraint_enforced(test_db, tournament):
    p1 = _make_player("p1", "P1")
    p2 = _make_player("p2", "P2")
    sc = SideComp(event=tournament.url, name="A", type="DUELING")
    db.session.add(sc)
    db.session.flush()
    db.session.add(SideCompRegistration(comp=sc.id, player=p1.id, entry_number=1, weapon=0))
    db.session.commit()
    db.session.add(SideCompRegistration(comp=sc.id, player=p2.id, entry_number=1, weapon=0))
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


def test_create_with_description(test_db, tournament):
    p = _make_player("to_user", "TO User")
    _make_to(tournament.url, p.id)

    from app.services.sidecomp_service import SideCompService

    res = SideCompService.create(
        tournament.url,
        actor_user_id=p.id,
        actor_user_type="player",
        name="Dueling 1v1",
        type="DUELING",
        description="Best of 3 sets, single elimination.",
    )
    assert isinstance(res, Ok)
    sc = res.unwrap()
    assert sc.description == "Best of 3 sets, single elimination."


def test_update_description_can_clear(test_db, tournament):
    p = _make_player("to_user", "TO User")
    _make_to(tournament.url, p.id)
    sc = SideComp(
        event=tournament.url,
        name="A",
        type="DUELING",
        description="initial",
    )
    db.session.add(sc)
    db.session.commit()

    from app.services.sidecomp_service import SideCompService

    res = SideCompService.update(
        sc.id,
        actor_user_id=p.id,
        actor_user_type="player",
        description="   ",
    )
    assert isinstance(res, Ok)
    db.session.refresh(sc)
    assert sc.description is None


def test_update_registration_open_toggle(test_db, tournament):
    p = _make_player("to_user", "TO User")
    _make_to(tournament.url, p.id)
    sc = SideComp(event=tournament.url, name="A", type="DUELING")
    db.session.add(sc)
    db.session.commit()
    assert sc.registration_open is False

    from app.services.sidecomp_service import SideCompService

    res = SideCompService.update(
        sc.id,
        actor_user_id=p.id,
        actor_user_type="player",
        registration_open=True,
    )
    assert isinstance(res, Ok)
    db.session.refresh(sc)
    assert sc.registration_open is True

    res = SideCompService.update(
        sc.id,
        actor_user_id=p.id,
        actor_user_type="player",
        registration_open=False,
    )
    assert isinstance(res, Ok)
    db.session.refresh(sc)
    assert sc.registration_open is False


def test_update_active_toggle(test_db, tournament):
    p = _make_player("to_user", "TO User")
    _make_to(tournament.url, p.id)
    sc = SideComp(event=tournament.url, name="A", type="DUELING")
    db.session.add(sc)
    db.session.commit()
    assert sc.active is False

    from app.services.sidecomp_service import SideCompService

    res = SideCompService.update(
        sc.id,
        actor_user_id=p.id,
        actor_user_type="player",
        active=True,
    )
    assert isinstance(res, Ok)
    db.session.refresh(sc)
    assert sc.active is True

    res = SideCompService.update(
        sc.id,
        actor_user_id=p.id,
        actor_user_type="player",
        active=False,
    )
    assert isinstance(res, Ok)
    db.session.refresh(sc)
    assert sc.active is False


def test_cancel_player_registrations_in_event_removes_only_matching(test_db, tournament):
    p1 = _make_player("p1", "P1")
    p2 = _make_player("p2", "P2")
    sc1 = SideComp(event=tournament.url, name="A", type="DUELING")
    sc2 = SideComp(event=tournament.url, name="B", type="OTHER")
    db.session.add_all([sc1, sc2])
    db.session.flush()
    db.session.add(SideCompRegistration(comp=sc1.id, player=p1.id, entry_number=1, weapon=0))
    db.session.add(SideCompRegistration(comp=sc2.id, player=p1.id, entry_number=1, weapon=0))
    db.session.add(SideCompRegistration(comp=sc1.id, player=p2.id, entry_number=2, weapon=0))
    db.session.commit()

    from app.services.sidecomp_service import SideCompService

    SideCompService.cancel_player_registrations_in_event(tournament.url, p1.id)

    assert SideCompRegistration.query.filter_by(player=p1.id).count() == 0
    assert SideCompRegistration.query.filter_by(player=p2.id).count() == 1


def test_player_self_deregister_from_event_cascades(app, client, tournament):
    """Player self-deregister via /deregister-player should cascade to side comps."""
    from tests.utils import login_as

    with app.app_context():
        p = _make_player()
        _confirm_event_registration(tournament.url, p.id)
        sc = SideComp(event=tournament.url, name="A", type="DUELING")
        db.session.add(sc)
        db.session.flush()
        db.session.add(SideCompRegistration(comp=sc.id, player=p.id, entry_number=1, weapon=0))
        db.session.commit()
        login_as(client, p)

    resp = client.post(f"/_api/{tournament.url}/deregister-player")
    assert resp.status_code == 200

    with app.app_context():
        assert SideCompRegistration.query.filter_by(player=p.id).count() == 0


def test_route_list_for_event_public(client, tournament):
    sc = SideComp(event=tournament.url, name="A", type="DUELING")
    db.session.add(sc)
    db.session.commit()

    resp = client.get(f"/_api/{tournament.url}/sidecomps")
    assert resp.status_code == 200
    payload = resp.get_json()
    assert any(row["name"] == "A" and row["type"] == "DUELING" for row in payload)


def test_route_detail_public_with_registrants(client, tournament):
    p = _make_player()
    sc = SideComp(event=tournament.url, name="A", type="DUELING")
    db.session.add(sc)
    db.session.flush()
    db.session.add(SideCompRegistration(comp=sc.id, player=p.id, entry_number=1, weapon=0))
    db.session.commit()

    resp = client.get(f"/_api/sidecomps/{sc.id}")
    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["name"] == "A"
    assert payload["type"] == "DUELING"
    assert len(payload["registrants"]) == 1
    assert payload["registrants"][0]["player_id"] == p.id


def test_route_detail_includes_entry_numbers(app, client, tournament):
    with app.app_context():
        p1 = _make_player("p1", "P1")
        p2 = _make_player("p2", "P2")
        _confirm_event_registration(tournament.url, p1.id)
        _confirm_event_registration(tournament.url, p2.id)
        sc = SideComp(event=tournament.url, name="A", type="DUELING", registration_open=True)
        db.session.add(sc)
        db.session.commit()
        comp_id = sc.id

        from app.services.sidecomp_service import SideCompService

        SideCompService.register_player(comp_id, player_id=p1.id, weapon="CHAIN")
        SideCompService.register_player(comp_id, player_id=p2.id, weapon="CHAIN")

    resp = client.get(f"/_api/sidecomps/{comp_id}")
    assert resp.status_code == 200
    payload = resp.get_json()
    entry_numbers = [r["entry_number"] for r in payload["registrants"]]
    assert entry_numbers == [1, 2]


def test_route_detail_not_found(client):
    resp = client.get("/_api/sidecomps/99999")
    assert resp.status_code == 404


def test_route_detail_viewer_flags_anonymous(client, tournament):
    sc = SideComp(event=tournament.url, name="A", type="DUELING")
    db.session.add(sc)
    db.session.commit()

    resp = client.get(f"/_api/sidecomps/{sc.id}")
    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["viewer_is_to"] is False
    assert payload["viewer_can_register"] is False
    assert payload["viewer_is_registered_in_comp"] is False


def test_route_detail_viewer_flags_event_player(app, client, tournament):
    with app.app_context():
        p = _make_player()
        _confirm_event_registration(tournament.url, p.id)
        sc = SideComp(event=tournament.url, name="A", type="DUELING", registration_open=True)
        db.session.add(sc)
        db.session.commit()
        comp_id = sc.id
        login_as(client, p)

    resp = client.get(f"/_api/sidecomps/{comp_id}")
    payload = resp.get_json()
    assert payload["viewer_is_to"] is False
    assert payload["viewer_can_register"] is True
    assert payload["viewer_is_registered_in_comp"] is False


def test_route_detail_viewer_flags_already_registered(app, client, tournament):
    with app.app_context():
        p = _make_player()
        _confirm_event_registration(tournament.url, p.id)
        sc = SideComp(event=tournament.url, name="A", type="DUELING")
        db.session.add(sc)
        db.session.flush()
        db.session.add(SideCompRegistration(comp=sc.id, player=p.id, entry_number=1, weapon=0))
        db.session.commit()
        comp_id = sc.id
        login_as(client, p)

    resp = client.get(f"/_api/sidecomps/{comp_id}")
    payload = resp.get_json()
    assert payload["viewer_can_register"] is False
    assert payload["viewer_is_registered_in_comp"] is True


def test_route_detail_viewer_can_register_with_league_scoped_registration(app, client, test_db):
    with app.app_context():
        ctx = _make_league_sidecomp_context(registration_open=True)
        comp_id = ctx["sidecomp"].id
        login_as(client, ctx["player"])

    resp = client.get(f"/_api/sidecomps/{comp_id}")

    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["viewer_can_register"] is True
    assert payload["viewer_is_registered_in_comp"] is False


def test_route_detail_viewer_flags_to(app, client, tournament):
    with app.app_context():
        to_user = _make_player("to_user", "TO User")
        _make_to(tournament.url, to_user.id)
        sc = SideComp(event=tournament.url, name="A", type="DUELING")
        db.session.add(sc)
        db.session.commit()
        comp_id = sc.id
        login_as(client, to_user)

    resp = client.get(f"/_api/sidecomps/{comp_id}")
    payload = resp.get_json()
    assert payload["viewer_is_to"] is True


def test_route_create_to_succeeds(app, client, tournament):
    with app.app_context():
        to_user = _make_player("to_user", "TO User")
        _make_to(tournament.url, to_user.id)
        login_as(client, to_user)

    resp = client.post(
        f"/_api/{tournament.url}/sidecomps",
        json={"name": "Dueling", "type": "DUELING"},
    )
    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["name"] == "Dueling"
    assert payload["type"] == "DUELING"


def test_route_create_non_to_forbidden(app, client, tournament):
    with app.app_context():
        p = _make_player("non_to", "Non TO")
        login_as(client, p)

    resp = client.post(
        f"/_api/{tournament.url}/sidecomps",
        json={"name": "X", "type": "DUELING"},
    )
    assert resp.status_code == 403


def test_route_update_to_succeeds(app, client, tournament):
    with app.app_context():
        to_user = _make_player("to_user", "TO User")
        _make_to(tournament.url, to_user.id)
        sc = SideComp(event=tournament.url, name="Old", type="DUELING")
        db.session.add(sc)
        db.session.commit()
        comp_id = sc.id
        login_as(client, to_user)

    resp = client.patch(
        f"/_api/sidecomps/{comp_id}",
        json={"name": "New"},
    )
    assert resp.status_code == 200
    assert resp.get_json()["name"] == "New"


def test_route_create_with_description(app, client, tournament):
    with app.app_context():
        to_user = _make_player("to_user", "TO User")
        _make_to(tournament.url, to_user.id)
        login_as(client, to_user)

    resp = client.post(
        f"/_api/{tournament.url}/sidecomps",
        json={"name": "Dueling", "type": "DUELING", "description": "Single elim"},
    )
    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["description"] == "Single elim"
    assert payload["registration_open"] is False
    assert payload["active"] is False


def test_route_update_open_close(app, client, tournament):
    with app.app_context():
        to_user = _make_player("to_user", "TO User")
        _make_to(tournament.url, to_user.id)
        sc = SideComp(event=tournament.url, name="A", type="DUELING")
        db.session.add(sc)
        db.session.commit()
        comp_id = sc.id
        login_as(client, to_user)

    resp = client.patch(
        f"/_api/sidecomps/{comp_id}",
        json={"registration_open": True},
    )
    assert resp.status_code == 200
    assert resp.get_json()["registration_open"] is True

    resp = client.get(f"/_api/sidecomps/{comp_id}")
    assert resp.status_code == 200
    assert resp.get_json()["registration_open"] is True


def test_route_update_active(app, client, tournament):
    with app.app_context():
        to_user = _make_player("to_user", "TO User")
        _make_to(tournament.url, to_user.id)
        sc = SideComp(event=tournament.url, name="A", type="DUELING")
        db.session.add(sc)
        db.session.commit()
        comp_id = sc.id
        login_as(client, to_user)

    resp = client.get(f"/_api/sidecomps/{comp_id}")
    assert resp.status_code == 200
    assert resp.get_json()["active"] is False

    resp = client.patch(
        f"/_api/sidecomps/{comp_id}",
        json={"active": True},
    )
    assert resp.status_code == 200
    assert resp.get_json()["active"] is True

    resp = client.get(f"/_api/sidecomps/{comp_id}")
    assert resp.status_code == 200
    assert resp.get_json()["active"] is True


def test_route_detail_viewer_cannot_register_when_closed(app, client, tournament):
    with app.app_context():
        p = _make_player()
        _confirm_event_registration(tournament.url, p.id)
        sc = SideComp(event=tournament.url, name="A", type="DUELING")
        db.session.add(sc)
        db.session.commit()
        comp_id = sc.id
        login_as(client, p)

    resp = client.get(f"/_api/sidecomps/{comp_id}")
    payload = resp.get_json()
    assert payload["registration_open"] is False
    assert payload["viewer_can_register"] is False


def test_route_list_includes_registration_open(client, tournament):
    sc = SideComp(event=tournament.url, name="A", type="DUELING")
    db.session.add(sc)
    db.session.commit()

    resp = client.get(f"/_api/{tournament.url}/sidecomps")
    assert resp.status_code == 200
    rows = resp.get_json()
    row = next(r for r in rows if r["name"] == "A")
    assert "registration_open" in row
    assert row["registration_open"] is False


def test_route_delete_to_succeeds(app, client, tournament):
    with app.app_context():
        to_user = _make_player("to_user", "TO User")
        _make_to(tournament.url, to_user.id)
        sc = SideComp(event=tournament.url, name="Old", type="DUELING")
        db.session.add(sc)
        db.session.commit()
        comp_id = sc.id
        login_as(client, to_user)

    resp = client.delete(f"/_api/sidecomps/{comp_id}")
    assert resp.status_code == 200
    with app.app_context():
        assert SideComp.query.get(comp_id) is None


def test_route_player_register_succeeds(app, client, tournament):
    with app.app_context():
        p = _make_player()
        _confirm_event_registration(tournament.url, p.id)
        sc = SideComp(event=tournament.url, name="A", type="DUELING", registration_open=True)
        db.session.add(sc)
        db.session.commit()
        comp_id = sc.id
        login_as(client, p)

    resp = client.post(f"/_api/sidecomps/{comp_id}/register", json={"weapon": "CHAIN"})
    assert resp.status_code == 200

    with app.app_context():
        assert SideCompRegistration.query.filter_by(comp=comp_id, player=p.id).count() == 1


def test_route_player_register_requires_player_account(app, client, tournament):
    """Team accounts cannot self-register for side competitions."""
    from models import Team

    with app.app_context():
        t = Team(id="t_team", name="T", pw_hash="dummy_hash")
        t.set_password("p")
        db.session.add(t)
        db.session.commit()
        sc = SideComp(event=tournament.url, name="A", type="DUELING")
        db.session.add(sc)
        db.session.commit()
        comp_id = sc.id
        login_as(client, t)

    resp = client.post(f"/_api/sidecomps/{comp_id}/register", json={"weapon": "CHAIN"})
    assert resp.status_code == 403


def test_route_player_deregister_succeeds(app, client, tournament):
    with app.app_context():
        p = _make_player()
        _confirm_event_registration(tournament.url, p.id)
        sc = SideComp(event=tournament.url, name="A", type="DUELING")
        db.session.add(sc)
        db.session.flush()
        db.session.add(SideCompRegistration(comp=sc.id, player=p.id, entry_number=1, weapon=0))
        db.session.commit()
        comp_id = sc.id
        login_as(client, p)

    resp = client.post(f"/_api/sidecomps/{comp_id}/deregister")
    assert resp.status_code == 200

    with app.app_context():
        assert SideCompRegistration.query.filter_by(comp=comp_id, player=p.id).count() == 0


def test_route_register_player_as_to_succeeds(app, client, tournament):
    with app.app_context():
        to_user = _make_player("to_user", "TO User")
        _make_to(tournament.url, to_user.id)
        target = _make_player("p_other", "Other")
        _confirm_event_registration(tournament.url, target.id)
        sc = SideComp(event=tournament.url, name="A", type="DUELING")
        db.session.add(sc)
        db.session.commit()
        comp_id = sc.id
        target_id = target.id
        login_as(client, to_user)

    resp = client.post(
        f"/_api/sidecomps/{comp_id}/register-player-as-to",
        json={"player_id": target_id, "weapon": "CHAIN"},
    )
    assert resp.status_code == 200

    with app.app_context():
        reg = SideCompRegistration.query.filter_by(comp=comp_id, player=target_id).first()
        assert reg is not None
        assert reg.registered_by_to is True


def test_delete_tournament_cascades_sidecomp_registrations(app, client, tournament):
    """Deleting a tournament must remove SideCompRegistration rows before SideComp.

    Regression test: SideCompRegistration has a FK to sidecomps.id, so deleting
    side comps without first removing their registrations raises IntegrityError
    when SQLite foreign_keys=ON.
    """
    with app.app_context():
        to_user = _make_player("to_user", "TO User")
        target = _make_player("p_other", "Other")
        _make_to(tournament.url, to_user.id)
        _confirm_event_registration(tournament.url, target.id)
        sc = SideComp(event=tournament.url, name="A", type="DUELING")
        db.session.add(sc)
        db.session.flush()
        db.session.add(SideCompRegistration(comp=sc.id, player=target.id, entry_number=1, weapon=0))
        db.session.commit()
        comp_id = sc.id
        tournament_url = tournament.url
        login_as(client, to_user)

    resp = client.post(
        f"/_api/{tournament_url}/delete",
        data={"confirm_url": tournament_url},
    )
    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["success"] is True

    with app.app_context():
        assert SideComp.query.get(comp_id) is None
        assert SideCompRegistration.query.filter_by(comp=comp_id).count() == 0


def test_route_eligible_players_marks_sidecomp_registered_players(app, client, tournament):
    with app.app_context():
        to_user = _make_player("to_user", "TO User")
        _make_to(tournament.url, to_user.id)
        p1 = _make_player("p1", "P1")
        p2 = _make_player("p2", "P2")
        _confirm_event_registration(tournament.url, p1.id)
        _confirm_event_registration(tournament.url, p2.id)
        sc = SideComp(event=tournament.url, name="A", type="DUELING")
        db.session.add(sc)
        db.session.flush()
        db.session.add(SideCompRegistration(comp=sc.id, player=p1.id, entry_number=1, weapon=0))
        db.session.commit()
        comp_id = sc.id
        login_as(client, to_user)

    resp = client.get(f"/_api/sidecomps/{comp_id}/eligible-players")
    assert resp.status_code == 200
    payload = resp.get_json()
    rows_by_id = {row["player_id"]: row for row in payload["players"]}
    assert set(rows_by_id) == {"p1", "p2"}
    assert rows_by_id["p1"]["sidecomp_registered"] is True
    assert rows_by_id["p1"]["entry_number"] == 1
    assert rows_by_id["p2"]["sidecomp_registered"] is False
    assert rows_by_id["p2"]["entry_number"] is None


def test_route_eligible_players_returns_league_scoped_registrations(app, client, test_db):
    """A side comp on a league-linked tournament must surface league-scoped registrations.

    For league tournaments, PlayerRegistration rows have league_id set and event NULL,
    and team pseudonyms live on league-scoped TeamRegistration rows. The endpoint must
    resolve registrations through the tournament's scope rather than filtering by event
    directly.
    """
    from datetime import datetime, timedelta, timezone

    from app.domain.enums import TeamRegistrationStatus
    from models import League, Team, TeamRegistration, Tournament
    from tests.utils import make_registrable_config

    with app.app_context():
        cfg = make_registrable_config(team_registration_open=True, player_registration_open=True)
        league = League(url="lg", name="LG", registrable_config_id=cfg.id)
        db.session.add(league)
        db.session.flush()
        tourn = Tournament(
            url="lg-evt",
            name="League Event",
            start_date=datetime.now(timezone.utc),
            end_date=datetime.now(timezone.utc) + timedelta(days=1),
            league_id="lg",
        )
        db.session.add(tourn)
        db.session.flush()

        to_user = _make_player("lg_to", "LG TO")
        db.session.add(TO(user_id=to_user.id, user_type="player", event=None, league_id="lg"))

        team = Team(id="lg_team", name="LG Team", pw_hash="dummy_hash")
        db.session.add(team)
        db.session.flush()
        db.session.add(
            TeamRegistration(
                event=None,
                league_id="lg",
                team=team.id,
                pseudonym="LG Pseudonym",
                status=TeamRegistrationStatus.CONFIRMED,
            )
        )

        p1 = _make_player("lg_p1", "LG P1")
        db.session.add(
            PlayerRegistration(
                event=None,
                league_id="lg",
                player=p1.id,
                team=team.id,
                jersey_number="7",
                jersey_name="P1",
                status=RegistrationStatus.CONFIRMED,
            )
        )

        sc = SideComp(event=tourn.url, name="LG SC", type="DUELING")
        db.session.add(sc)
        db.session.commit()
        comp_id = sc.id
        login_as(client, to_user)

    resp = client.get(f"/_api/sidecomps/{comp_id}/eligible-players")
    assert resp.status_code == 200
    rows = resp.get_json()["players"]
    assert [row["player_id"] for row in rows] == ["lg_p1"]
    assert rows[0]["team_pseudonym"] == "LG Pseudonym"


def test_register_player_requires_valid_weapon(test_db, tournament):
    p = _make_player()
    _confirm_event_registration(tournament.url, p.id)
    sc = SideComp(event=tournament.url, name="A", type="DUELING", registration_open=True)
    db.session.add(sc)
    db.session.commit()

    from app.services.sidecomp_service import SideCompService

    res = SideCompService.register_player(sc.id, player_id=p.id, weapon="NOT_A_WEAPON")
    assert isinstance(res, Err)
    assert res.unwrap_err().status_code == 400


def test_register_player_rejects_disabled_weapon(test_db, tournament):
    p = _make_player()
    _confirm_event_registration(tournament.url, p.id)
    sc = SideComp(event=tournament.url, name="A", type="DUELING", registration_open=True)
    from app.domain.enums import Pompfen

    sc.set_allowed_weapons([Pompfen.CHAIN])
    db.session.add(sc)
    db.session.commit()

    from app.services.sidecomp_service import SideCompService

    res = SideCompService.register_player(sc.id, player_id=p.id, weapon="LONG")
    assert isinstance(res, Err)
    assert res.unwrap_err().status_code == 400

    ok = SideCompService.register_player(sc.id, player_id=p.id, weapon="CHAIN")
    assert isinstance(ok, Ok)
    assert ok.unwrap().weapon == Pompfen.CHAIN.value


def test_update_allowed_weapons_deregisters_disabled(test_db, tournament):
    to_user = _make_player("to_user", "TO User")
    _make_to(tournament.url, to_user.id)
    p1 = _make_player("p1", "P1")
    p2 = _make_player("p2", "P2")
    _confirm_event_registration(tournament.url, p1.id)
    _confirm_event_registration(tournament.url, p2.id)
    sc = SideComp(event=tournament.url, name="A", type="DUELING", registration_open=True)
    db.session.add(sc)
    db.session.flush()
    from app.domain.enums import Pompfen

    db.session.add(SideCompRegistration(comp=sc.id, player=p1.id, entry_number=1, weapon=Pompfen.CHAIN.value))
    db.session.add(SideCompRegistration(comp=sc.id, player=p2.id, entry_number=2, weapon=Pompfen.LONG.value))
    db.session.commit()

    from app.services.sidecomp_service import SideCompService

    res = SideCompService.update(
        sc.id,
        actor_user_id=to_user.id,
        actor_user_type="player",
        allowed_weapons=["CHAIN"],
    )
    assert isinstance(res, Ok)
    remaining = SideCompRegistration.query.filter_by(comp=sc.id).all()
    assert len(remaining) == 1
    assert remaining[0].player == p1.id
    assert remaining[0].weapon == Pompfen.CHAIN.value


def test_update_player_weapon(test_db, tournament):
    p = _make_player()
    _confirm_event_registration(tournament.url, p.id)
    sc = SideComp(event=tournament.url, name="A", type="DUELING", registration_open=True)
    db.session.add(sc)
    db.session.commit()

    from app.domain.enums import Pompfen
    from app.services.sidecomp_service import SideCompService

    SideCompService.register_player(sc.id, player_id=p.id, weapon="CHAIN").unwrap()
    res = SideCompService.update_player_weapon(sc.id, player_id=p.id, weapon="STAFF")
    assert isinstance(res, Ok)
    assert res.unwrap().weapon == Pompfen.STAFF.value


def test_log_result_and_standings(test_db, tournament):
    from app.services.sidecomp_service import SideCompService

    p = _make_player()
    _confirm_event_registration(tournament.url, p.id)
    sc = SideComp(
        event=tournament.url,
        name="Duel",
        type="DUELING",
        registration_open=True,
        active=True,
        track_losses=True,
    )
    db.session.add(sc)
    db.session.commit()

    reg = SideCompService.register_player(sc.id, player_id=p.id, weapon="LONG").unwrap()
    r1 = SideCompService.log_result(sc.id, registration_id=reg.id, points=1, ref_user_id="ref").unwrap()
    SideCompService.log_result(sc.id, registration_id=reg.id, points=1, ref_user_id="ref").unwrap()
    SideCompService.log_result(sc.id, registration_id=reg.id, points=-1, ref_user_id="ref").unwrap()

    flagged = SideCompService.set_result_flagged(r1.uuid, flagged=True, actor_user_id="ref").unwrap()
    assert flagged.flagged is True

    standings = SideCompService.standings(sc.id).unwrap()
    assert standings["results_page_enabled"] is True
    assert len(standings["tables"]) == 1
    assert standings["tables"][0]["track_losses"] is True
    row = standings["tables"][0]["rows"][0]
    assert row["wins"] == 2
    assert row["losses"] == 1
    assert row["win_percentage"] == pytest.approx(200 / 3)
    assert row["rank"] == 1
    assert row["weapon"] == "LONG"


def test_log_result_rejects_when_inactive(test_db, tournament):
    from app.error_values import Err
    from app.services.sidecomp_service import SideCompService

    p = _make_player()
    _confirm_event_registration(tournament.url, p.id)
    sc = SideComp(event=tournament.url, name="Duel", type="DUELING", registration_open=True, active=False)
    db.session.add(sc)
    db.session.commit()

    reg = SideCompService.register_player(sc.id, player_id=p.id, weapon="LONG").unwrap()
    res = SideCompService.log_result(sc.id, registration_id=reg.id, points=1, ref_user_id="ref")
    assert isinstance(res, Err)
    assert "not active" in str(res.unwrap_err()).lower()


def test_delete_result_only_by_ref_while_active(test_db, tournament):
    from app.error_values import Err
    from app.services.sidecomp_service import SideCompService

    p = _make_player()
    _confirm_event_registration(tournament.url, p.id)
    sc = SideComp(event=tournament.url, name="Duel", type="DUELING", registration_open=True, active=True)
    db.session.add(sc)
    db.session.commit()

    reg = SideCompService.register_player(sc.id, player_id=p.id, weapon="LONG").unwrap()
    kept = SideCompService.log_result(sc.id, registration_id=reg.id, points=1, ref_user_id="ref").unwrap()
    undone = SideCompService.log_result(sc.id, registration_id=reg.id, points=1, ref_user_id="ref").unwrap()
    undone_uuid = undone.uuid

    assert isinstance(SideCompService.delete_result(undone_uuid, actor_user_id="other_ref"), Err)

    SideCompService.delete_result(undone_uuid, actor_user_id="ref").unwrap()
    assert SideCompResult.query.get(undone_uuid) is None
    assert SideCompService.standings(sc.id).unwrap()["tables"][0]["rows"][0]["wins"] == 1

    sc.active = False
    db.session.commit()
    res = SideCompService.delete_result(kept.uuid, actor_user_id="ref")
    assert isinstance(res, Err)
    assert SideCompResult.query.get(kept.uuid) is not None


def test_chain_breaking_standings_split_and_top_n(test_db, tournament):
    from app.services.sidecomp_service import SideCompService

    chain = _make_player("chain_p", "Chain")
    staff = _make_player("staff_p", "Staff")
    long_p = _make_player("long_p", "Long")
    for pl in (chain, staff, long_p):
        _confirm_event_registration(tournament.url, pl.id)

    sc = SideComp(
        event=tournament.url,
        name="CB",
        type="CHAIN_BREAKING",
        registration_open=True,
        active=True,
        only_show_top_n_results=1,
    )
    db.session.add(sc)
    db.session.commit()

    r_chain = SideCompService.register_player(sc.id, player_id=chain.id, weapon="CHAIN").unwrap()
    r_staff = SideCompService.register_player(sc.id, player_id=staff.id, weapon="STAFF").unwrap()
    r_long = SideCompService.register_player(sc.id, player_id=long_p.id, weapon="LONG").unwrap()

    SideCompService.log_result(sc.id, registration_id=r_chain.id, points=1, ref_user_id="ref").unwrap()
    SideCompService.log_result(sc.id, registration_id=r_staff.id, points=1, ref_user_id="ref").unwrap()
    SideCompService.log_result(sc.id, registration_id=r_staff.id, points=1, ref_user_id="ref").unwrap()
    SideCompService.log_result(sc.id, registration_id=r_long.id, points=1, ref_user_id="ref").unwrap()

    standings = SideCompService.standings(sc.id).unwrap()
    assert [t["id"] for t in standings["tables"]] == ["chains", "breaks"]
    chains = standings["tables"][0]["rows"]
    breaks = standings["tables"][1]["rows"]
    assert len(chains) == 1
    assert chains[0]["weapon"] == "CHAIN"
    # top N=1 keeps only the highest-scoring break player (staff with 2)
    assert len(breaks) == 1
    assert breaks[0]["player_id"] == staff.id
    assert breaks[0]["rank"] == 1
    assert breaks[0]["wins"] == 2


def test_standings_top_n_organizers_see_every_row(app, client, tournament):
    from flask_login import login_user

    from app.routes.sidecomps import standings as standings_view
    from app.services.sidecomp_service import SideCompService

    with app.app_context():
        organizer = _make_player("to_user", "TO User")
        _make_to(tournament.url, organizer.id)
        outsider = _make_player("fan", "Fan")
        staff = _make_player("staff_p", "Staff")
        long_p = _make_player("long_p", "Long")
        for player in (staff, long_p):
            _confirm_event_registration(tournament.url, player.id)
        sc = SideComp(
            event=tournament.url,
            name="Duel",
            type="DUELING",
            registration_open=True,
            active=True,
            only_show_top_n_results=1,
        )
        db.session.add(sc)
        db.session.commit()
        staff_reg = SideCompService.register_player(sc.id, player_id=staff.id, weapon="LONG").unwrap()
        long_reg = SideCompService.register_player(sc.id, player_id=long_p.id, weapon="LONG").unwrap()
        SideCompService.log_result(sc.id, registration_id=staff_reg.id, points=1, ref_user_id="ref").unwrap()
        SideCompService.log_result(sc.id, registration_id=staff_reg.id, points=1, ref_user_id="ref").unwrap()
        SideCompService.log_result(sc.id, registration_id=long_reg.id, points=1, ref_user_id="ref").unwrap()
        comp_id = sc.id

        def rows_for(response):
            body, status = response
            assert status == 200
            return body.get_json()["tables"][0]["rows"]

        with app.test_request_context(f"/_api/sidecomps/{comp_id}/standings"):
            public_rows = rows_for(standings_view(comp_id))
        assert [row["wins"] for row in public_rows] == [2]

        with app.test_request_context(f"/_api/sidecomps/{comp_id}/standings"):
            login_user(outsider)
            outsider_rows = rows_for(standings_view(comp_id))
        assert [row["wins"] for row in outsider_rows] == [2]

        login_as(client, organizer)

    organizer_rows = client.get(f"/_api/sidecomps/{comp_id}/standings").get_json()["tables"][0]["rows"]
    assert [row["wins"] for row in organizer_rows] == [2, 1]
    assert [row["rank"] for row in organizer_rows] == [1, 2]


def test_enter_results_roster_query_filters_by_entry_number(app, client, tournament):
    from app.services.sidecomp_service import SideCompService

    with app.app_context():
        viewer = _make_player("viewer", "Viewer")
        first = _make_player("first", "First")
        second = _make_player("second", "Second")
        for player in (first, second):
            _confirm_event_registration(tournament.url, player.id)
        sc = SideComp(event=tournament.url, name="Duel", type="DUELING", registration_open=True, active=True)
        db.session.add(sc)
        db.session.commit()
        SideCompService.register_player(sc.id, player_id=first.id, weapon="LONG").unwrap()
        SideCompService.register_player(sc.id, player_id=second.id, weapon="LONG").unwrap()
        comp_id = sc.id
        login_as(client, viewer)

    full = client.get(f"/_api/sidecomps/{comp_id}/enter-results-roster")
    assert full.status_code == 200
    assert len(full.get_json()["registrants"]) == 2

    matched = client.get(f"/_api/sidecomps/{comp_id}/enter-results-roster?q=1")
    assert matched.status_code == 200
    numbers = [row["entry_number"] for row in matched.get_json()["registrants"]]
    assert numbers == [1]


def test_only_show_top_n_zero_hides_results_page(test_db, tournament):
    sc = SideComp(event=tournament.url, name="Hidden", type="DUELING", only_show_top_n_results=0)
    db.session.add(sc)
    db.session.commit()
    from app.services.sidecomp_service import SideCompService

    standings = SideCompService.standings(sc.id).unwrap()
    assert standings["results_page_enabled"] is False


def test_invalid_results_excluded_from_standings(test_db, tournament):
    from app.services.sidecomp_service import SideCompService

    to_user = _make_player("to_user", "TO User")
    _make_to(tournament.url, to_user.id)
    p = _make_player()
    _confirm_event_registration(tournament.url, p.id)
    sc = SideComp(event=tournament.url, name="Duel", type="DUELING", registration_open=True, active=True)
    db.session.add(sc)
    db.session.commit()

    reg = SideCompService.register_player(sc.id, player_id=p.id, weapon="LONG").unwrap()
    r1 = SideCompService.log_result(sc.id, registration_id=reg.id, points=1, ref_user_id="ref").unwrap()
    SideCompService.log_result(sc.id, registration_id=reg.id, points=1, ref_user_id="ref").unwrap()

    SideCompService.set_result_valid(
        r1.uuid,
        valid=False,
        actor_user_id=to_user.id,
        actor_user_type="player",
    ).unwrap()

    standings = SideCompService.standings(sc.id).unwrap()
    assert standings["finalized"] is False
    assert standings["tables"][0]["rows"][0]["wins"] == 1


def test_finalize_requires_inactive_and_is_irreversible(test_db, tournament):
    from app.error_values import Err
    from app.services.sidecomp_service import SideCompService

    to_user = _make_player("to_user", "TO User")
    _make_to(tournament.url, to_user.id)
    sc = SideComp(event=tournament.url, name="Duel", type="DUELING", active=True)
    db.session.add(sc)
    db.session.commit()

    res = SideCompService.finalize(sc.id, actor_user_id=to_user.id, actor_user_type="player")
    assert isinstance(res, Err)
    assert "deactivate" in str(res.unwrap_err()).lower()

    SideCompService.update(
        sc.id,
        actor_user_id=to_user.id,
        actor_user_type="player",
        active=False,
    ).unwrap()

    SideCompService.finalize(sc.id, actor_user_id=to_user.id, actor_user_type="player").unwrap()
    db.session.refresh(sc)
    assert sc.finalized is True

    res = SideCompService.update(
        sc.id,
        actor_user_id=to_user.id,
        actor_user_type="player",
        active=True,
    )
    assert isinstance(res, Err)
    assert "finalized" in str(res.unwrap_err()).lower()

    res = SideCompService.finalize(sc.id, actor_user_id=to_user.id, actor_user_type="player")
    assert isinstance(res, Err)
    assert "already" in str(res.unwrap_err()).lower()

    standings = SideCompService.standings(sc.id).unwrap()
    assert standings["finalized"] is True


def test_manage_results_lists_chronological_points(test_db, tournament):
    from app.services.sidecomp_service import SideCompService

    to_user = _make_player("to_user", "TO User")
    _make_to(tournament.url, to_user.id)
    p = _make_player()
    _confirm_event_registration(tournament.url, p.id)
    sc = SideComp(
        event=tournament.url,
        name="Duel",
        type="DUELING",
        registration_open=True,
        active=True,
        track_losses=True,
    )
    db.session.add(sc)
    db.session.commit()

    reg = SideCompService.register_player(sc.id, player_id=p.id, weapon="LONG").unwrap()
    SideCompService.log_result(sc.id, registration_id=reg.id, points=1, ref_user_id="ref-a").unwrap()
    SideCompService.log_result(sc.id, registration_id=reg.id, points=-1, ref_user_id="ref-b").unwrap()

    payload = SideCompService.manage_results(sc.id, actor_user_id=to_user.id, actor_user_type="player").unwrap()
    assert payload["name"] == "Duel"
    assert payload["finalized"] is False
    assert len(payload["results"]) == 2
    assert payload["results"][0]["points"] == 1
    assert payload["results"][1]["points"] == -1
    assert payload["results"][0]["entry_number"] == reg.entry_number
    assert payload["results"][0]["weapon"] == "LONG"
    assert [r["untracked"] for r in payload["results"]] == [False, False]

    sc.track_losses = False
    db.session.commit()
    payload = SideCompService.manage_results(sc.id, actor_user_id=to_user.id, actor_user_type="player").unwrap()
    assert [r["untracked"] for r in payload["results"]] == [False, True]

    row = SideCompService.standings(sc.id).unwrap()["tables"][0]["rows"][0]
    assert row["wins"] == 1
    assert row["losses"] is None
    assert row["win_percentage"] is None


def test_log_loss_rejected_per_chain_breaking_group(test_db, tournament):
    from app.error_values import Err
    from app.services.sidecomp_service import SideCompService

    chain = _make_player("chain_p", "Chain")
    staff = _make_player("staff_p", "Staff")
    for pl in (chain, staff):
        _confirm_event_registration(tournament.url, pl.id)
    sc = SideComp(
        event=tournament.url,
        name="CB",
        type="CHAIN_BREAKING",
        registration_open=True,
        active=True,
        track_losses=True,
        track_chain_losses=False,
        track_break_losses=True,
    )
    db.session.add(sc)
    db.session.commit()

    r_chain = SideCompService.register_player(sc.id, player_id=chain.id, weapon="CHAIN").unwrap()
    r_staff = SideCompService.register_player(sc.id, player_id=staff.id, weapon="STAFF").unwrap()

    res = SideCompService.log_result(sc.id, registration_id=r_chain.id, points=-1, ref_user_id="ref")
    assert isinstance(res, Err)
    assert "not tracked" in str(res.unwrap_err()).lower()
    SideCompService.log_result(sc.id, registration_id=r_staff.id, points=-1, ref_user_id="ref").unwrap()

    roster = SideCompService.enter_results_roster(sc.id).unwrap()
    track_by_reg = {p["registration_id"]: p["track_losses"] for p in roster["registrants"]}
    assert track_by_reg == {r_chain.id: False, r_staff.id: True}

    tables = {t["id"]: t["track_losses"] for t in SideCompService.standings(sc.id).unwrap()["tables"]}
    assert tables == {"chains": False, "breaks": True}


def test_standings_rank_by_win_percentage_when_tracking_losses(test_db, tournament):
    from app.services.sidecomp_service import SideCompService

    players = [_make_player(f"p_{name}", name) for name in ("Perfect", "Busy", "Idle", "Mid")]
    for pl in players:
        _confirm_event_registration(tournament.url, pl.id)
    sc = SideComp(
        event=tournament.url,
        name="Duel",
        type="DUELING",
        registration_open=True,
        active=True,
        track_losses=True,
    )
    db.session.add(sc)
    db.session.commit()

    perfect, busy, idle, mid = (
        SideCompService.register_player(sc.id, player_id=pl.id, weapon="LONG").unwrap() for pl in players
    )

    def log(reg, wins, losses):
        for points in [1] * wins + [-1] * losses:
            SideCompService.log_result(sc.id, registration_id=reg.id, points=points, ref_user_id="ref").unwrap()

    log(perfect, 1, 0)
    log(busy, 6, 2)
    log(mid, 3, 1)

    rows = SideCompService.standings(sc.id).unwrap()["tables"][0]["rows"]
    assert [r["registration_id"] for r in rows] == [perfect.id, busy.id, mid.id, idle.id]
    assert [r["rank"] for r in rows] == [1, 2, 3, 4]
    assert rows[0]["win_percentage"] == pytest.approx(100.0)
    assert rows[3]["win_percentage"] is None

    sc.track_losses = False
    db.session.commit()
    rows = SideCompService.standings(sc.id).unwrap()["tables"][0]["rows"]
    assert [r["registration_id"] for r in rows] == [busy.id, mid.id, perfect.id, idle.id]


def _make_two_comps_with_scoped_player(tournament):
    """Return ``(registered_comp, other_comp, player, registration)`` where the
    player holds a scoped entry number via ``registered_comp`` only."""
    from app.services.sidecomp_service import SideCompService

    p = _make_player()
    _confirm_event_registration(tournament.url, p.id)
    registered_comp = SideComp(event=tournament.url, name="A", type="DUELING", registration_open=True)
    other_comp = SideComp(event=tournament.url, name="B", type="DUELING", active=True)
    db.session.add_all([registered_comp, other_comp])
    db.session.commit()
    reg = SideCompService.register_player(registered_comp.id, player_id=p.id, weapon="LONG").unwrap()
    return registered_comp, other_comp, p, reg


def test_log_result_by_player_id_creates_incomplete_registration(test_db, tournament):
    from app.services.sidecomp_service import SideCompService

    _, other_comp, p, reg = _make_two_comps_with_scoped_player(tournament)

    roster = SideCompService.enter_results_roster(other_comp.id).unwrap()["registrants"]
    assert len(roster) == 1
    assert roster[0]["player_id"] == p.id
    assert roster[0]["registration_id"] is None
    assert roster[0]["registered"] is False
    assert roster[0]["entry_number"] == reg.entry_number
    assert roster[0]["weapon"] is None

    first = SideCompService.log_result(other_comp.id, player_id=p.id, points=1, ref_user_id="ref").unwrap()
    second = SideCompService.log_result(other_comp.id, player_id=p.id, points=1, ref_user_id="ref").unwrap()
    assert first.player == second.player

    incomplete = SideCompRegistration.query.get(first.player)
    assert incomplete.comp == other_comp.id
    assert incomplete.weapon is None
    assert incomplete.entry_number == reg.entry_number

    roster_row = SideCompService.enter_results_roster(other_comp.id).unwrap()["registrants"][0]
    assert roster_row["registration_id"] == incomplete.id
    assert roster_row["registered"] is False


def test_log_result_by_player_id_requires_scoped_entry_number(test_db, tournament):
    from app.services.sidecomp_service import SideCompService

    _, other_comp, _, _ = _make_two_comps_with_scoped_player(tournament)
    stranger = _make_player("stranger", "Stranger")

    res = SideCompService.log_result(other_comp.id, player_id=stranger.id, points=1, ref_user_id="ref")
    assert isinstance(res, Err)
    assert SideCompRegistration.query.filter_by(comp=other_comp.id).count() == 0


def test_incomplete_registration_hidden_from_standings_and_registrants(app, client, tournament):
    from app.services.sidecomp_service import SideCompService

    with app.app_context():
        to_user = _make_player("to_user", "TO User")
        _make_to(tournament.url, to_user.id)
        _, other_comp, p, _ = _make_two_comps_with_scoped_player(tournament)
        other_comp_id = other_comp.id
        tournament_url = tournament.url
        SideCompService.log_result(other_comp_id, player_id=p.id, points=1, ref_user_id="ref").unwrap()

        assert SideCompService.standings(other_comp_id).unwrap()["tables"][0]["rows"] == []

        manage = SideCompService.manage_results(
            other_comp_id, actor_user_id=to_user.id, actor_user_type="player"
        ).unwrap()
        assert [(r["player_id"], r["registered"]) for r in manage["results"]] == [(p.id, False)]

        login_as(client, p)

    detail = client.get(f"/_api/sidecomps/{other_comp_id}").get_json()
    assert detail["registrants"] == []
    assert detail["viewer_registration_incomplete"] is True

    summaries = client.get(f"/_api/{tournament_url}/sidecomps").get_json()
    summary = next(s for s in summaries if s["id"] == other_comp_id)
    assert summary["registrant_count"] == 0
    assert summary["viewer_registration_incomplete"] is True


def test_completing_registration_sets_weapon(test_db, tournament):
    from app.services.sidecomp_service import SideCompService

    _, other_comp, p, reg = _make_two_comps_with_scoped_player(tournament)
    result = SideCompService.log_result(other_comp.id, player_id=p.id, points=1, ref_user_id="ref").unwrap()

    completed = SideCompService.update_player_weapon(other_comp.id, player_id=p.id, weapon="STAFF").unwrap()
    assert completed.id == result.player
    assert completed.weapon_name() == "STAFF"
    assert completed.entry_number == reg.entry_number

    rows = SideCompService.standings(other_comp.id).unwrap()["tables"][0]["rows"]
    assert [(r["player_id"], r["wins"]) for r in rows] == [(p.id, 1)]


def _make_pending_player(tournament_url):
    """Return a player whose event registration is pending approval by a confirmed team."""
    from models import Team, TeamRegistration

    team = Team(id="requested_team", name="Requested Team", pw_hash="dummy_hash")
    db.session.add(team)
    db.session.flush()
    db.session.add(
        TeamRegistration(
            event=tournament_url,
            team=team.id,
            pseudonym="Requested Team",
            shortname="REQ",
            status="CONFIRMED",
            paid=True,
        )
    )
    p = _make_player("pending_p", "Pending")
    db.session.add(
        PlayerRegistration(
            event=tournament_url,
            player=p.id,
            team=team.id,
            jersey_number="3",
            jersey_name="Pendo",
            status=RegistrationStatus.PENDING_TEAM_APPROVAL,
            paid=True,
        )
    )
    db.session.commit()
    return p


def test_pending_player_can_self_register_and_shows_unattached(test_db, tournament):
    from app.services.sidecomp_service import SideCompService

    p = _make_pending_player(tournament.url)
    sc = SideComp(event=tournament.url, name="A", type="DUELING", registration_open=True, active=True)
    db.session.add(sc)
    db.session.commit()

    reg = SideCompService.register_player(sc.id, player_id=p.id, weapon="LONG").unwrap()
    SideCompService.log_result(sc.id, registration_id=reg.id, points=1, ref_user_id="ref").unwrap()

    roster_row = SideCompService.enter_results_roster(sc.id).unwrap()["registrants"][0]
    assert roster_row["jersey_name"] == "Pendo"
    assert roster_row["team_id"] is None
    assert roster_row["team_shortname"] == "merc"

    standings_row = SideCompService.standings(sc.id).unwrap()["tables"][0]["rows"][0]
    assert standings_row["player_id"] == p.id
    assert standings_row["team_shortname"] == "merc"


def test_to_can_register_pending_player_listed_as_unattached(app, client, tournament):
    from app.services.sidecomp_service import SideCompService

    with app.app_context():
        to_user = _make_player("to_user", "TO User")
        _make_to(tournament.url, to_user.id)
        p = _make_pending_player(tournament.url)
        pending_player_id = p.id
        sc = SideComp(event=tournament.url, name="A", type="DUELING")
        db.session.add(sc)
        db.session.commit()
        comp_id = sc.id

        SideCompService.register_player_as_to(
            comp_id,
            actor_user_id=to_user.id,
            actor_user_type="player",
            player_id=pending_player_id,
            weapon="LONG",
        ).unwrap()
        login_as(client, to_user)

    players = client.get(f"/_api/sidecomps/{comp_id}/eligible-players").get_json()["players"]
    row = next(r for r in players if r["player_id"] == pending_player_id)
    assert row["sidecomp_registered"] is True
    assert row["team_id"] is None
    assert row["team_shortname"] is None


def test_route_detail_pending_player_can_register(app, client, tournament):
    with app.app_context():
        p = _make_pending_player(tournament.url)
        sc = SideComp(event=tournament.url, name="A", type="DUELING", registration_open=True)
        db.session.add(sc)
        db.session.commit()
        comp_id = sc.id
        login_as(client, p)

    payload = client.get(f"/_api/sidecomps/{comp_id}").get_json()
    assert payload["viewer_can_register"] is True
