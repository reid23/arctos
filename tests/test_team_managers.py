"""Team delegation keeps player identity and enforces team and event boundaries."""

import pytest
from sqlalchemy import delete

from app.domain.enums import RegistrationStatus, WinnerSide
from app.services.dual_write import get_match_player_ids, set_match_players
from app.services.permission_service import PermissionService
from app.services.registration_resolver import player_registration_for_tournament
from models import League, Match, Player, PlayerRegistration, Team, TeamManager, TeamRegistration, TO, Tournament, db
from tests.utils import login_as as set_session_user, make_registrable_config


def login_as(client, user):
    from flask import g

    g.pop("_login_user", None)
    set_session_user(client, user)


@pytest.fixture
def managed(client, team, player, tournament):
    manager = player
    other = Team(id="other-team", name="Other Team")
    outsider = Player(id="outsider", name="Outside Player")
    member = Player(id="member", name="Roster Member", email="private@example.test", phone="private-phone")
    db.session.add_all([other, outsider, member])
    db.session.flush()
    db.session.add(TeamManager(team_id=team.id, player_id=manager.id))
    db.session.commit()
    return team, manager, other, outsider, member, tournament


@pytest.fixture(params=["events", "leagues"])
def scope(request, managed):
    team, manager, other, outsider, member, tournament = managed
    kind = request.param
    if kind == "leagues":
        cfg = make_registrable_config(
            team_registration_open=True, player_registration_open=True, max_team_size_roster=2
        )
        parent = League(url="test-league", name="Test League", published=True, registrable_config_id=cfg.id)
        db.session.add(parent)
        db.session.commit()
        filters = {"league_id": parent.url}
    else:
        parent = tournament
        parent.registrable_config.max_team_size_roster = 2
        filters = {"event": parent.url}
    db.session.add(TeamRegistration(team=team.id, pseudonym=team.name, status="CONFIRMED", **filters))
    registration = PlayerRegistration(
        player=member.id,
        team=None,
        status="CONFIRMED",
        paid=True,
        amount_paid=25,
        jersey_name="Member",
        jersey_number="12",
        waiver_legal_name_signature="Private legal name",
        **filters,
    )
    db.session.add(registration)
    db.session.commit()
    return kind, parent, filters, registration


def test_owner_alone_controls_managers_and_duplicate_grant_is_idempotent(client, managed):
    team, manager, other, outsider, member, _ = managed
    path = f"/_api/teams/{team.id}/managers/{outsider.id}"
    for actor in (manager, other, outsider):
        login_as(client, actor)
        assert client.put(path, json={}).status_code == 403
        assert client.delete(path).status_code == 403
        assert client.get(f"/_api/teams/{team.id}/managers").status_code == 403
    login_as(client, team)
    assert client.put(path, json={}).status_code == 200
    assert client.put(path, json={}).status_code == 200
    assert TeamManager.query.filter_by(team_id=team.id, player_id=outsider.id).count() == 1
    assert client.put(f"/_api/teams/{team.id}/managers/{other.id}", json={}).status_code == 404
    candidates = client.get(f"/_api/teams/{team.id}/manager-candidates?search=Roster").get_json()["players"]
    assert candidates == [{"id": member.id, "name": member.name, "profile_photo": None}]
    assert client.delete(path).status_code == 200


def test_revocation_applies_to_open_session_and_typed_identity(client, managed):
    team, manager, other, outsider, _, _ = managed
    # Same slug in the other account table grants no ownership.
    collision = Player(id=team.id, name="Same slug")
    db.session.add(collision)
    db.session.commit()
    login_as(client, collision)
    assert client.put(f"/_api/teams/{team.id}", json={"name": "Wrong owner"}).status_code == 403
    login_as(client, manager)
    assert client.put(f"/_api/teams/{team.id}", json={"name": "Managed team"}).status_code == 200
    assert client.put(f"/_api/teams/{other.id}", json={"name": "Wrong team"}).status_code == 403
    for suffix, method in [("banner-photo", "delete"), ("profile-photo", "delete")]:
        assert getattr(client, method)(f"/_api/teams/{other.id}/{suffix}").status_code == 403
    db.session.execute(delete(TeamManager).where(TeamManager.team_id == team.id, TeamManager.player_id == manager.id))
    db.session.commit()
    assert client.put(f"/_api/teams/{team.id}", json={"name": "Revoked"}).status_code == 403
    denied = client.get(f"/_api/teams/{team.id}/management")
    assert denied.status_code == 403
    assert denied.get_json()["error"] == "You do not manage this team."
    assert client.get("/_api/me").get_json()["id"] == manager.id


def test_multiple_teams_and_personal_registration_remain_independent(client, managed):
    team, manager, other, _, _, tournament = managed
    db.session.add(TeamManager(team_id=other.id, player_id=manager.id))
    db.session.commit()
    login_as(client, manager)
    me = client.get("/_api/me").get_json()
    assert me["type"] == "player"
    assert {item["id"] for item in me["managed_teams"]} == {team.id, other.id}
    assert (
        client.post(f"/_api/{tournament.url}/register-team", data={"pseudonym": "Missing context"}).status_code == 403
    )
    for selected in (team, other):
        assert (
            client.post(
                f"/_api/{tournament.url}/register-team", data={"team_id": selected.id, "pseudonym": selected.name}
            ).status_code
            == 200
        )
    assert (
        client.post(
            f"/_api/{tournament.url}/register-player", data={"jersey_name": "Own player", "jersey_number": "9"}
        ).status_code
        == 200
    )
    assert PlayerRegistration.query.filter_by(event=tournament.url, player=manager.id).one().team is None
    assert client.get("/_api/me").get_json()["id"] == manager.id


def test_manager_can_change_personal_league_team_without_changing_played_match(client, managed):
    team, manager, other, _, _, tournament = managed
    next_team = Team(id="next-team", name="Next Team")
    cfg = make_registrable_config(player_registration_open=True)
    league = League(url="manager-league", name="Manager League", published=True, registrable_config_id=cfg.id)
    first_round = Tournament(
        url="manager-round-one",
        name="Round One",
        published=True,
        league_id=league.url,
        start_date=tournament.start_date,
    )
    next_round = Tournament(
        url="manager-round-two",
        name="Round Two",
        published=True,
        league_id=league.url,
        start_date=tournament.start_date,
    )
    db.session.add_all([next_team, league, first_round, next_round])
    db.session.flush()
    db.session.add_all(
        [
            TeamRegistration(league_id=league.url, team=other.id, pseudonym=other.name, status="CONFIRMED"),
            TeamRegistration(league_id=league.url, team=next_team.id, pseudonym=next_team.name, status="CONFIRMED"),
        ]
    )
    registration = PlayerRegistration(
        league_id=league.url,
        player=manager.id,
        team=other.id,
        status=RegistrationStatus.CONFIRMED,
    )
    match = Match(name="First round match", event=first_round.url, team1=other.id, team2=team.id)
    db.session.add_all([registration, match])
    db.session.flush()
    set_match_players(match, [manager.id], [])
    db.session.commit()
    registration_id = registration.id

    login_as(client, manager)
    assert client.get("/_api/me").get_json()["managed_teams"] == [
        {"id": team.id, "name": team.name, "profile_photo": None}
    ]
    response = client.put(f"/_api/leagues/{league.url}/registrations/player/me", json={"team": next_team.id})
    assert response.status_code == 200
    db.session.expire_all()
    assert registration.id == registration_id
    assert registration.team == next_team.id
    assert registration.status == RegistrationStatus.PENDING_TEAM_APPROVAL
    assert player_registration_for_tournament(next_round, manager.id).id == registration_id
    assert match.team1 == other.id
    assert get_match_player_ids(match, WinnerSide.TEAM1) == [manager.id]


def test_roster_add_remove_preserves_registration_and_private_fields(client, managed, scope):
    team, manager, other, _, member, _ = managed
    kind, parent, filters, registration = scope
    login_as(client, manager)
    base = f"/_api/teams/{team.id}/{kind}/{parent.url}"
    response = client.get(f"{base}/roster-candidates").get_json()
    assert response["players"] == [
        {"id": member.id, "name": member.name, "profile_photo": None, "registration_id": registration.id}
    ]
    assert client.post(f"{base}/roster/{registration.id}/add", json={}).status_code == 200
    assert registration.team == team.id
    detail = client.get(f"{base}/registration").get_json()
    assert detail["roster"][0]["player"]["id"] == member.id
    assert "private" not in str(detail).lower()
    former = PlayerRegistration(
        player=managed[3].id, team=team.id, status="CANCELLED", paid=True, amount_paid=10, **filters
    )
    db.session.add(former)
    db.session.commit()
    former_rows = client.get(f"{base}/registration").get_json()["roster"]
    assert former_rows[-1]["status"] == "CANCELLED"
    assert former_rows[-1]["amount_paid"] == 10
    assert client.post(f"{base}/roster/{former.id}/remove", json={}).status_code == 400
    assert client.post(f"{base}/roster/{registration.id}/remove", json={}).status_code == 200
    assert registration.team is None
    assert registration.status == RegistrationStatus.CONFIRMED
    assert registration.paid and registration.amount_paid == 25
    assert registration.waiver_legal_name_signature == "Private legal name"
    assert registration.jersey_number == "12"
    assert (
        client.post(f"/_api/teams/{other.id}/{kind}/{parent.url}/roster/{registration.id}/add", json={}).status_code
        == 403
    )


def test_roster_capacity_pending_requests_closed_signups_and_cross_scope(client, managed, scope):
    team, manager, other, outsider, member, tournament = managed
    kind, parent, filters, registration = scope
    parent.registrable_config.max_team_size_roster = 1
    registration.team = team.id
    pending = PlayerRegistration(player=outsider.id, team=team.id, status="PENDING_TEAM_APPROVAL", **filters)
    db.session.add(pending)
    db.session.commit()
    login_as(client, manager)
    base = f"/_api/teams/{team.id}/{kind}/{parent.url}"
    assert client.post(f"{base}/roster/{pending.id}/approve", json={}).status_code == 400
    assert pending.status == RegistrationStatus.PENDING_TEAM_APPROVAL
    registration.team = other.id
    db.session.commit()
    assert client.post(f"{base}/roster/{registration.id}/add", json={}).status_code == 400
    parent.registrable_config.player_registration_open = False
    db.session.commit()
    assert client.post(f"{base}/roster/{pending.id}/approve", json={}).status_code == 200
    assert client.post(f"{base}/roster/{pending.id}/remove", json={}).status_code == 400
    # A known registration ID does not work in a different scope.
    extra = Tournament(
        url="another-event",
        name="Another event",
        published=True,
        start_date=tournament.start_date,
        registrable_config_id=make_registrable_config(player_registration_open=True).id,
    )
    db.session.add(extra)
    db.session.flush()
    db.session.add(TeamRegistration(team=team.id, event=extra.url, pseudonym="Team", status="CONFIRMED"))
    db.session.commit()
    assert (
        client.post(f"/_api/teams/{team.id}/events/{extra.url}/roster/{pending.id}/approve", json={}).status_code == 404
    )


def test_legacy_team_routes_require_target_team_and_grant_matching_access(client, managed, scope):
    team, manager, other, outsider, _, _ = managed
    kind, parent, filters, registration = scope
    registration.team = team.id
    registration.status = RegistrationStatus.PENDING_TEAM_APPROVAL
    db.session.commit()
    login_as(client, manager)
    prefix = f"/_api/leagues/{parent.url}" if kind == "leagues" else f"/_api/{parent.url}"
    reg_prefix = prefix if kind == "leagues" else f"/_api/tournaments/{parent.url}"
    assert client.get(f"{reg_prefix}/registrations/team/me?team_id={team.id}").status_code == 200
    assert (
        client.put(
            f"{reg_prefix}/registrations/team/me?team_id={team.id}", json={"pseudonym": "Managed alias"}
        ).status_code
        == 200
    )
    assert client.get(f"{reg_prefix}/registrations/team/me?team_id={other.id}").status_code == 403
    invitation_path = f"{prefix}/invitations" if kind == "leagues" else f"/_api/tournaments/{parent.url}/invitations"
    assert client.get(f"{invitation_path}?team_id={team.id}").status_code == 200
    assert client.post(f"{prefix}/invitation/{registration.id}/accept", data={"team_id": team.id}).status_code == 200
    assert registration.status == RegistrationStatus.CONFIRMED
    login_as(client, outsider)
    assert client.post(f"{prefix}/deregister-team", data={"team_id": team.id}).status_code == 403


@pytest.mark.parametrize("body", [[], "wrong", 3, {"pseudonym": 42}, {"pseudonym": "Valid", "shortname": []}])
def test_invalid_registration_payload_is_rejected(client, managed, body):
    team, manager, _, _, _, tournament = managed
    login_as(client, manager)
    base = f"/_api/teams/{team.id}/events/{tournament.url}/registration"
    assert client.post(base, json=body).status_code == 400
    assert client.put(base, json=body).status_code == 400


def test_team_organizer_inheritance_includes_drafts_without_head_ref_or_account_access(client, managed):
    team, manager, _, outsider, _, tournament = managed
    cfg = make_registrable_config()
    league = League(url="draft-league", name="Draft League", published=False, registrable_config_id=cfg.id)
    db.session.add(league)
    db.session.flush()
    tournament.published = False
    league_round = Tournament(
        url="draft-round", name="Draft Round", published=False, league_id=league.url, start_date=tournament.start_date
    )
    db.session.add(league_round)
    db.session.add_all(
        [
            TO(user_id=team.id, user_type="team", event=tournament.url),
            TO(user_id=team.id, user_type="team", league_id=league.url),
        ]
    )
    db.session.commit()
    assert PermissionService.is_tournament_organizer_of(tournament, manager)
    assert PermissionService.is_tournament_organizer_of(league_round, manager)
    assert not PermissionService.can_head_ref_match(tournament.url, manager)
    assert not PermissionService.is_tournament_organizer_of(tournament, outsider)
    login_as(client, manager)
    assert client.get(f"/_api/tournaments/{tournament.url}").status_code == 200
    assert client.get(f"/_api/leagues/{league.url}").status_code == 200
    assert {item["league_url"] for item in client.get("/_api/leagues/organized").get_json()["leagues"]} == {league.url}
    from app.services.tournament_service import TournamentService

    visible = TournamentService.get_homepage_context(manager)
    assert league_round.url in {item.url for item in visible["tournaments"]}
    entry = TO.query.filter_by(event=tournament.url).one()
    assert client.post(f"/_api/{tournament.url}/remove-to", data={"to_id": entry.id}).status_code == 400


def test_concurrent_approvals_do_not_exceed_roster_limit(app, client, managed, scope):
    """An owner and manager approving together cannot take the same last place."""
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    team, manager, _, outsider, _, _ = managed
    kind, parent, filters, registration = scope
    parent.registrable_config.max_team_size_roster = 1
    registration.team = team.id
    registration.status = RegistrationStatus.PENDING_TEAM_APPROVAL
    second = PlayerRegistration(player=outsider.id, team=team.id, status="PENDING_TEAM_APPROVAL", **filters)
    db.session.add(second)
    db.session.commit()
    urls = [f"/_api/teams/{team.id}/{kind}/{parent.url}/roster/{row.id}/approve" for row in (registration, second)]
    account_ids = (team.id, manager.id)
    ready = Barrier(2)

    def approve(index):
        with app.app_context(), app.test_client() as request_client:
            with request_client.session_transaction() as session:
                session["_user_id"] = account_ids[index]
                session["_fresh"] = True
            ready.wait(timeout=10)
            return request_client.post(urls[index], json={}).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(approve, [0, 1]))
    assert sorted(statuses) == [200, 400]
    db.session.expire_all()
    assert PlayerRegistration.query.filter_by(**filters, team=team.id, status=RegistrationStatus.CONFIRMED).count() == 1
