"""Tests for the TO-driven player and team registration feature."""

from datetime import datetime, timezone

import pytest

from app.exceptions import UnauthorizedError, ValidationError
from app.error_values import Err, Ok
from app.domain.enums import RegistrationStatus, TeamRegistrationStatus
from app.services.registration_service import RegistrationService
from models import (
    Player,
    PlayerRegistration,
    Team,
    TeamRegistration,
    TO,
    Tournament,
    db,
)
from tests.utils import make_registrable_config, login_as


@pytest.fixture
def checkin_tournament(test_db):
    """A tournament with no waiver configured."""
    cfg = make_registrable_config(
        team_registration_open=False,
        player_registration_open=False,
    )
    t = Tournament(
        url="checkin-event",
        name="Checkin Event",
        start_date=datetime.now(timezone.utc),
        registrable_config_id=cfg.id,
        published=True,
    )
    db.session.add(t)
    db.session.commit()
    db.session.refresh(t)
    return t


@pytest.fixture
def to_player(test_db, checkin_tournament):
    """A player who is a TO for ``checkin_tournament``."""
    p = Player(id="alice-to", name="Alice TO")
    p.set_password("pw")
    db.session.add(p)
    db.session.add(TO(user_id=p.id, user_type="player", event=checkin_tournament.url))
    db.session.commit()
    return p


@pytest.fixture
def target_player(test_db):
    """A player who exists but is not a TO."""
    p = Player(id="bob-target", name="Bob Target")
    p.set_password("pw")
    db.session.add(p)
    db.session.commit()
    return p


@pytest.fixture
def confirmed_team(test_db, checkin_tournament):
    """A team registered (CONFIRMED) for ``checkin_tournament``."""
    team = Team(id="blue-team", name="Blue Team")
    team.set_password("pw")
    db.session.add(team)
    db.session.add(
        TeamRegistration(
            event=checkin_tournament.url,
            team=team.id,
            pseudonym="Blue Team",
            status=TeamRegistrationStatus.CONFIRMED,
        )
    )
    db.session.commit()
    return team


@pytest.mark.integration
def test_checkin_rejects_non_to_actor(checkin_tournament, target_player):
    res = RegistrationService.register_player_as_to(
        checkin_tournament.url,
        actor_user_id="random-user",
        actor_user_type="player",
        player_id=target_player.id,
        team_id=None,
    )
    assert isinstance(res, Err)
    err = res.unwrap_err()
    assert isinstance(err, UnauthorizedError)
    assert err.status_code == 403


@pytest.mark.integration
def test_checkin_happy_path_no_team(checkin_tournament, to_player, target_player):
    res = RegistrationService.register_player_as_to(
        checkin_tournament.url,
        actor_user_id=to_player.id,
        actor_user_type="player",
        player_id=target_player.id,
        team_id=None,
    )
    assert isinstance(res, Ok)
    reg = res.unwrap()
    assert reg.status == RegistrationStatus.CONFIRMED
    assert reg.paid is True
    assert reg.amount_paid == 0
    assert reg.team is None
    assert reg.jersey_name == "N/A"
    assert reg.jersey_number == "0"


@pytest.mark.integration
def test_checkin_happy_path_with_team(checkin_tournament, to_player, target_player, confirmed_team):
    res = RegistrationService.register_player_as_to(
        checkin_tournament.url,
        actor_user_id=to_player.id,
        actor_user_type="player",
        player_id=target_player.id,
        team_id=confirmed_team.id,
        jersey_name="BOB",
        jersey_number="7",
    )
    assert isinstance(res, Ok)
    reg = res.unwrap()
    assert reg.team == confirmed_team.id
    assert reg.jersey_name == "BOB"
    assert reg.jersey_number == "7"


@pytest.mark.integration
def test_checkin_unknown_player(checkin_tournament, to_player):
    res = RegistrationService.register_player_as_to(
        checkin_tournament.url,
        actor_user_id=to_player.id,
        actor_user_type="player",
        player_id="ghost",
        team_id=None,
    )
    assert isinstance(res, Err)
    err = res.unwrap_err()
    assert isinstance(err, ValidationError)
    assert "Player not found" in err.message


@pytest.mark.integration
def test_checkin_rejects_already_confirmed(checkin_tournament, to_player, target_player):
    first = RegistrationService.register_player_as_to(
        checkin_tournament.url,
        actor_user_id=to_player.id,
        actor_user_type="player",
        player_id=target_player.id,
        team_id=None,
    )
    assert isinstance(first, Ok)

    second = RegistrationService.register_player_as_to(
        checkin_tournament.url,
        actor_user_id=to_player.id,
        actor_user_type="player",
        player_id=target_player.id,
        team_id=None,
    )
    assert isinstance(second, Err)
    err = second.unwrap_err()
    assert isinstance(err, ValidationError)
    assert "already registered" in err.message


@pytest.mark.integration
def test_checkin_reuses_cancelled_row(checkin_tournament, to_player, target_player):
    cancelled = PlayerRegistration(
        event=checkin_tournament.url,
        player=target_player.id,
        team=None,
        jersey_number="",
        jersey_name="",
        status=RegistrationStatus.CANCELLED,
    )
    db.session.add(cancelled)
    db.session.commit()
    cancelled_id = cancelled.id

    res = RegistrationService.register_player_as_to(
        checkin_tournament.url,
        actor_user_id=to_player.id,
        actor_user_type="player",
        player_id=target_player.id,
        team_id=None,
    )
    assert isinstance(res, Ok)
    reg = res.unwrap()
    assert reg.id == cancelled_id  # row reused, not duplicated
    assert reg.status == RegistrationStatus.CONFIRMED
    assert reg.paid is True
    assert reg.jersey_name == "N/A"
    assert reg.jersey_number == "0"

    rows = PlayerRegistration.query.filter_by(event=checkin_tournament.url, player=target_player.id).count()
    assert rows == 1


@pytest.mark.integration
def test_checkin_rejects_unregistered_team(checkin_tournament, to_player, target_player):
    res = RegistrationService.register_player_as_to(
        checkin_tournament.url,
        actor_user_id=to_player.id,
        actor_user_type="player",
        player_id=target_player.id,
        team_id="ghost-team",
    )
    assert isinstance(res, Err)
    err = res.unwrap_err()
    assert isinstance(err, ValidationError)
    assert "Selected team" in err.message


@pytest.mark.integration
def test_checkin_waiver_required(test_db, target_player):
    cfg = make_registrable_config(
        team_registration_open=False,
        player_registration_open=False,
        waiver_filepath="/waivers/test.pdf",
        waiver_sha256="a" * 64,
    )
    t = Tournament(
        url="waiver-event",
        name="W",
        start_date=datetime.now(timezone.utc),
        registrable_config_id=cfg.id,
    )
    to_player = Player(id="to1", name="TO")
    to_player.set_password("pw")
    db.session.add_all([t, to_player])
    db.session.flush()
    db.session.add(TO(user_id=to_player.id, user_type="player", event=t.url))
    db.session.commit()

    blank = RegistrationService.register_player_as_to(
        t.url,
        actor_user_id=to_player.id,
        actor_user_type="player",
        player_id=target_player.id,
        team_id=None,
        waiver_legal_name_signature="",
    )
    assert isinstance(blank, Err)
    assert "Waiver signature" in blank.unwrap_err().message

    signed = RegistrationService.register_player_as_to(
        t.url,
        actor_user_id=to_player.id,
        actor_user_type="player",
        player_id=target_player.id,
        team_id=None,
        waiver_legal_name_signature="Bob Target",
    )
    assert isinstance(signed, Ok)
    reg = signed.unwrap()
    assert reg.waiver_legal_name_signature == "Bob Target"
    assert reg.waiver_legal_name_signature_sha256 == "a" * 64
    assert reg.waiver_signature_submitted_at is not None


@pytest.mark.integration
def test_post_checkin_unauthenticated(client, checkin_tournament, target_player):
    res = client.post(
        f"/_api/{checkin_tournament.url}/register-player-as-to",
        json={"player_id": target_player.id},
    )
    assert res.status_code == 401


@pytest.mark.integration
def test_post_checkin_non_to(client, checkin_tournament, target_player, test_db):
    # A player who exists but is not a TO of this event.
    other = Player(id="not-a-to", name="Not a TO")
    other.set_password("pw")
    db.session.add(other)
    db.session.commit()
    login_as(client, other)

    res = client.post(
        f"/_api/{checkin_tournament.url}/register-player-as-to",
        json={"player_id": target_player.id},
    )
    assert res.status_code == 403
    assert res.json["success"] is False
    assert isinstance(res.json["error"], str)
    assert res.json["error"]


@pytest.mark.integration
def test_post_checkin_happy_path(client, checkin_tournament, to_player, target_player, confirmed_team):
    login_as(client, to_player)

    res = client.post(
        f"/_api/{checkin_tournament.url}/register-player-as-to",
        json={
            "player_id": target_player.id,
            "team": confirmed_team.id,
            "jersey_name": "BOB",
            "jersey_number": "7",
        },
    )
    assert res.status_code == 200
    body = res.json
    assert body["success"] is True
    assert body["player_id"] == target_player.id
    assert body["player_name"] == target_player.name
    assert body["team"] == confirmed_team.id
    assert body["jersey_name"] == "BOB"
    assert body["jersey_number"] == "7"


@pytest.mark.integration
def test_post_checkin_applies_defaults(client, checkin_tournament, to_player, target_player):
    login_as(client, to_player)

    res = client.post(
        f"/_api/{checkin_tournament.url}/register-player-as-to",
        json={"player_id": target_player.id},
    )
    assert res.status_code == 200
    assert res.json["jersey_name"] == "N/A"
    assert res.json["jersey_number"] == "0"


@pytest.mark.integration
def test_post_checkin_missing_player_id(client, checkin_tournament, to_player):
    login_as(client, to_player)

    res = client.post(
        f"/_api/{checkin_tournament.url}/register-player-as-to",
        json={"team": None},
    )
    assert res.status_code == 400
    assert res.json["success"] is False


@pytest.mark.integration
def test_post_checkin_requires_json(client, checkin_tournament, to_player):
    login_as(client, to_player)

    res = client.post(
        f"/_api/{checkin_tournament.url}/register-player-as-to",
        data="player_id=anyone",
        content_type="application/x-www-form-urlencoded",
    )
    assert res.status_code == 415


@pytest.mark.integration
def test_post_checkin_already_confirmed(client, checkin_tournament, to_player, target_player):
    login_as(client, to_player)

    first = client.post(
        f"/_api/{checkin_tournament.url}/register-player-as-to",
        json={"player_id": target_player.id},
    )
    assert first.status_code == 200

    second = client.post(
        f"/_api/{checkin_tournament.url}/register-player-as-to",
        json={"player_id": target_player.id},
    )
    assert second.status_code == 400
    assert second.json["success"] is False
    assert "already registered" in second.json["error"]


def _make_team(test_db, *, team_id="green-team", name="Green Team"):
    team = Team(id=team_id, name=name)
    team.set_password("pw")
    db.session.add(team)
    db.session.commit()
    return team


@pytest.mark.integration
def test_register_team_rejects_non_to_actor(checkin_tournament, test_db):
    target_team = _make_team(test_db, team_id="reject-team", name="Reject Team")

    res = RegistrationService.register_team_as_to(
        checkin_tournament.url,
        actor_user_id="random-user",
        actor_user_type="player",
        team_id=target_team.id,
    )
    assert isinstance(res, Err)
    err = res.unwrap_err()
    assert isinstance(err, UnauthorizedError)
    assert err.status_code == 403


@pytest.mark.integration
def test_register_team_happy_path_default_pseudonym(checkin_tournament, to_player, test_db):
    target_team = _make_team(test_db, team_id="orange-team", name="Orange Team")

    res = RegistrationService.register_team_as_to(
        checkin_tournament.url,
        actor_user_id=to_player.id,
        actor_user_type="player",
        team_id=target_team.id,
    )
    assert isinstance(res, Ok)
    reg = res.unwrap()
    assert reg.event == checkin_tournament.url
    assert reg.team == target_team.id
    assert reg.pseudonym == "Orange Team"
    assert reg.status == TeamRegistrationStatus.CONFIRMED
    assert reg.paid is True
    assert reg.amount_paid == 0


@pytest.mark.integration
def test_register_team_happy_path_custom_pseudonym(checkin_tournament, to_player, test_db):
    target_team = _make_team(test_db, team_id="purple-team", name="Purple Team")

    res = RegistrationService.register_team_as_to(
        checkin_tournament.url,
        actor_user_id=to_player.id,
        actor_user_type="player",
        team_id=target_team.id,
        pseudonym="Purple Reign",
    )
    assert isinstance(res, Ok)
    assert res.unwrap().pseudonym == "Purple Reign"


@pytest.mark.integration
def test_register_team_unknown_team(checkin_tournament, to_player):
    res = RegistrationService.register_team_as_to(
        checkin_tournament.url,
        actor_user_id=to_player.id,
        actor_user_type="player",
        team_id="ghost-team",
    )
    assert isinstance(res, Err)
    err = res.unwrap_err()
    assert isinstance(err, ValidationError)
    assert "Team not found" in err.message


@pytest.mark.integration
def test_register_team_rejects_already_confirmed(checkin_tournament, to_player, confirmed_team):
    res = RegistrationService.register_team_as_to(
        checkin_tournament.url,
        actor_user_id=to_player.id,
        actor_user_type="player",
        team_id=confirmed_team.id,
    )
    assert isinstance(res, Err)
    err = res.unwrap_err()
    assert isinstance(err, ValidationError)
    assert "already registered" in err.message


@pytest.mark.integration
def test_register_team_reuses_cancelled_row(checkin_tournament, to_player, test_db):
    target_team = _make_team(test_db, team_id="recycle-team", name="Recycle Team")
    cancelled = TeamRegistration(
        event=checkin_tournament.url,
        team=target_team.id,
        pseudonym="Old Name",
        status=TeamRegistrationStatus.CANCELLED,
    )
    db.session.add(cancelled)
    db.session.commit()
    cancelled_id = cancelled.id

    res = RegistrationService.register_team_as_to(
        checkin_tournament.url,
        actor_user_id=to_player.id,
        actor_user_type="player",
        team_id=target_team.id,
        pseudonym="New Name",
    )
    assert isinstance(res, Ok)
    reg = res.unwrap()
    assert reg.id == cancelled_id  # row reused, not duplicated
    assert reg.status == TeamRegistrationStatus.CONFIRMED
    assert reg.pseudonym == "New Name"
    assert reg.paid is True

    rows = TeamRegistration.query.filter_by(event=checkin_tournament.url, team=target_team.id).count()
    assert rows == 1


@pytest.mark.integration
def test_register_team_n_max_cap_with_count_in_message(test_db):
    cfg = make_registrable_config(n_max_teams=2)
    t = Tournament(
        url="capped-event",
        name="Capped",
        start_date=datetime.now(timezone.utc),
        registrable_config_id=cfg.id,
    )
    organizer = Player(id="capped-to", name="Capped TO")
    organizer.set_password("pw")
    db.session.add_all([t, organizer])
    db.session.flush()
    db.session.add(TO(user_id=organizer.id, user_type="player", event=t.url))

    # Pre-fill to the cap with CONFIRMED registrations.
    for i in range(2):
        team = Team(id=f"cap-team-{i}", name=f"Cap Team {i}")
        team.set_password("pw")
        db.session.add(team)
        db.session.flush()
        db.session.add(
            TeamRegistration(
                event=t.url,
                team=team.id,
                pseudonym=team.name,
                status=TeamRegistrationStatus.CONFIRMED,
            )
        )
    overflow = Team(id="overflow-team", name="Overflow Team")
    overflow.set_password("pw")
    db.session.add(overflow)
    db.session.commit()

    res = RegistrationService.register_team_as_to(
        t.url,
        actor_user_id=organizer.id,
        actor_user_type="player",
        team_id=overflow.id,
    )
    assert isinstance(res, Err)
    err = res.unwrap_err()
    assert isinstance(err, ValidationError)
    assert "2/2" in err.message


@pytest.mark.integration
def test_register_team_invalid_pseudonym_chars(checkin_tournament, to_player, test_db):
    target_team = _make_team(test_db, team_id="bad-name-team", name="Some Team")

    res = RegistrationService.register_team_as_to(
        checkin_tournament.url,
        actor_user_id=to_player.id,
        actor_user_type="player",
        team_id=target_team.id,
        pseudonym="bad,pseudonym",
    )
    assert isinstance(res, Err)
    err = res.unwrap_err()
    assert isinstance(err, ValidationError)
    assert "," in err.message  # error mentions the offending character class


@pytest.mark.integration
def test_post_checkin_team_unauthenticated(client, checkin_tournament, test_db):
    target_team = _make_team(test_db, team_id="anon-team", name="Anon Team")

    res = client.post(
        f"/_api/{checkin_tournament.url}/register-team-as-to",
        json={"team_id": target_team.id},
    )
    assert res.status_code == 401


@pytest.mark.integration
def test_post_checkin_team_non_to(client, checkin_tournament, test_db):
    other = Player(id="not-a-to-team", name="Not a TO")
    other.set_password("pw")
    target_team = _make_team(test_db, team_id="non-to-team", name="Non-TO Team")
    db.session.add(other)
    db.session.commit()
    login_as(client, other)

    res = client.post(
        f"/_api/{checkin_tournament.url}/register-team-as-to",
        json={"team_id": target_team.id},
    )
    assert res.status_code == 403
    assert res.json["success"] is False
    assert isinstance(res.json["error"], str)
    assert res.json["error"]


@pytest.mark.integration
def test_post_checkin_team_happy_path(client, checkin_tournament, to_player, test_db):
    target_team = _make_team(test_db, team_id="happy-team", name="Happy Team")
    login_as(client, to_player)

    res = client.post(
        f"/_api/{checkin_tournament.url}/register-team-as-to",
        json={"team_id": target_team.id, "pseudonym": "Happy Squad"},
    )
    assert res.status_code == 200
    body = res.json
    assert body["success"] is True
    assert body["team_id"] == target_team.id
    assert body["team_name"] == target_team.name
    assert body["pseudonym"] == "Happy Squad"


@pytest.mark.integration
def test_post_checkin_team_default_pseudonym(client, checkin_tournament, to_player, test_db):
    target_team = _make_team(test_db, team_id="default-pn-team", name="Default PN Team")
    login_as(client, to_player)

    res = client.post(
        f"/_api/{checkin_tournament.url}/register-team-as-to",
        json={"team_id": target_team.id},
    )
    assert res.status_code == 200
    assert res.json["pseudonym"] == "Default PN Team"


@pytest.mark.integration
def test_post_checkin_team_missing_team_id(client, checkin_tournament, to_player):
    login_as(client, to_player)

    res = client.post(
        f"/_api/{checkin_tournament.url}/register-team-as-to",
        json={"pseudonym": "x"},
    )
    assert res.status_code == 400
    assert res.json["success"] is False


@pytest.mark.integration
def test_post_checkin_team_requires_json(client, checkin_tournament, to_player):
    login_as(client, to_player)

    res = client.post(
        f"/_api/{checkin_tournament.url}/register-team-as-to",
        data="team_id=anyone",
        content_type="application/x-www-form-urlencoded",
    )
    assert res.status_code == 415
