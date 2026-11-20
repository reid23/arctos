"""Merging one player or team account into another."""

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from app.domain.enums import RegistrationStatus, TeamRegistrationStatus
from app.error_values import Err
from app.services.account_merge_service import PLAYER_REFERENCES, TEAM_REFERENCES, AccountMergeService
from app.utils.datetime_helpers import now_utc_naive
from models import (
    TO,
    AccountMerge,
    Injury,
    Match,
    MatchNote,
    MatchPlayer,
    MatchReferee,
    OAuthClient,
    OAuthToken,
    Player,
    PlayerRegistration,
    ScriptVariable,
    Tag,
    Team,
    TeamRegistration,
    Tournament,
    db,
)
from tests.utils import issue_access_token, login_as, make_registrable_config


def _player(account_id: str, **fields) -> Player:
    player = Player(id=account_id, name=account_id.title(), **fields)
    player.set_password("secret")
    db.session.add(player)
    return player


def _team(account_id: str, **fields) -> Team:
    team = Team(id=account_id, name=account_id.title(), **fields)
    team.set_password("secret")
    db.session.add(team)
    return team


def _event(url: str) -> Tournament:
    tournament = Tournament(
        url=url,
        name=url.title(),
        start_date=datetime(2026, 6, 1),
        registrable_config_id=make_registrable_config().id,
    )
    db.session.add(tournament)
    return tournament


def _merge(source_id: str, target_id: str, model=Player):
    return AccountMergeService.merge(db.session.get(model, source_id), db.session.get(model, target_id))


@pytest.mark.unit
def test_every_foreign_key_to_an_account_is_handled(app):
    handled = {(column.class_.__table__.name, column.key) for column, _ in PLAYER_REFERENCES + TEAM_REFERENCES}
    for table in db.metadata.sorted_tables:
        for column in table.columns:
            if any(fk.target_fullname in ("players.id", "teams.id") for fk in column.foreign_keys):
                assert (table.name, column.name) in handled, f"{table.name}.{column.name} isn't moved by a merge"


@pytest.mark.integration
def test_player_merge_moves_history_and_reserves_the_old_id(app, test_db):
    _player("old", phone="555-0100", google_id="google-old")
    _player("new", bio="Hi")
    for url in ("spring", "fall"):
        _event(url)
    db.session.flush()
    db.session.add_all(
        [
            PlayerRegistration(event="spring", player="old", status=RegistrationStatus.CONFIRMED),
            PlayerRegistration(event="fall", player="new", status=RegistrationStatus.CONFIRMED),
            Match(name="Final", event="spring"),
            Injury(player="old", message="Sprained ankle"),
            TO(user_id="old", user_type="player", event="spring"),
            TO(user_id="old", user_type="player", event="fall"),
            TO(user_id="new", user_type="player", event="fall"),
        ]
    )
    db.session.flush()
    final = Match.query.filter_by(name="Final").one()
    final.started_by = "old"
    db.session.add_all(
        [
            MatchPlayer(match_uuid=final.uuid, player_id="old", side="TEAM1"),
            MatchNote(match=final.uuid, text="Nice", created_by="old"),
        ]
    )
    db.session.commit()
    issue_access_token(db.session.get(Player, "old"), scope="profile")

    assert _merge("old", "new").is_ok()

    assert db.session.get(Player, "old") is None
    new = db.session.get(Player, "new")
    assert (new.bio, new.phone, new.google_id) == ("Hi", "555-0100", "google-old")
    assert {row.event for row in PlayerRegistration.query.filter_by(player="new")} == {"spring", "fall"}
    assert MatchPlayer.query.filter_by(player_id="new").count() == 1
    assert MatchNote.query.filter_by(created_by="new").count() == 1
    assert Injury.query.filter_by(player="new").count() == 1
    assert Match.query.filter_by(name="Final").one().started_by == "new"
    assert sorted(role.event for role in TO.query.filter_by(user_id="new")) == ["fall", "spring"]
    assert OAuthClient.query.filter_by(owner_id="new").count() == 1
    assert all(
        token.access_token_revoked and token.refresh_token_revoked
        for token in OAuthToken.query.filter_by(user_id="old")
    )
    assert db.session.get(AccountMerge, "old").target_id == "new"


@pytest.mark.integration
def test_merge_is_refused_while_both_accounts_are_registered_for_one_event(app, test_db):
    _player("old")
    _player("new")
    _event("spring")
    db.session.flush()
    for player_id in ("old", "new"):
        db.session.add(PlayerRegistration(event="spring", player=player_id, status=RegistrationStatus.CONFIRMED))
    db.session.commit()

    preview = AccountMergeService.preview(db.session.get(Player, "old"), db.session.get(Player, "new")).unwrap()
    result = _merge("old", "new")

    assert preview.conflicts == ["Both accounts are registered for Spring."]
    assert isinstance(result, Err)
    assert db.session.get(Player, "old") is not None
    assert PlayerRegistration.query.filter_by(player="old").count() == 1


@pytest.mark.integration
def test_a_cancelled_registration_gives_way_to_the_active_one(app, test_db):
    _player("old")
    _player("new")
    _event("spring")
    db.session.flush()
    db.session.add_all(
        [
            PlayerRegistration(event="spring", player="old", status=RegistrationStatus.CONFIRMED),
            PlayerRegistration(event="spring", player="new", status=RegistrationStatus.CANCELLED),
        ]
    )
    db.session.commit()

    assert _merge("old", "new").is_ok()

    remaining = PlayerRegistration.query.filter_by(event="spring").all()
    assert [(row.player, row.status) for row in remaining] == [("new", RegistrationStatus.CONFIRMED)]


@pytest.mark.integration
def test_team_merge_rewrites_matches_and_schedule_text(app, test_db):
    for team_id in ("old", "new", "rival", "refs"):
        _team(team_id)
    _event("spring")
    db.session.flush()
    db.session.add_all(
        [
            TeamRegistration(event="spring", team="old", pseudonym="Old", status=TeamRegistrationStatus.CONFIRMED),
            Match(name="Final", event="spring", team1="old", team1_initial="old", team2="rival", team2_initial="rival"),
            Tag(event="spring", name="Champion", expression="(winner {Final})", team="old"),
            ScriptVariable(event="spring", name="favorites", expression="(list [old] [rival])"),
        ]
    )
    db.session.flush()
    final = Match.query.filter_by(name="Final").one()
    final.skip_condition = "(won? [old] {Final})"
    db.session.add(MatchReferee(match_uuid=final.uuid, slot=0, team_id="refs", initial="refs"))
    db.session.commit()

    assert _merge("old", "new", Team).is_ok()

    final = Match.query.filter_by(name="Final").one()
    assert (final.team1, final.team1_initial, final.skip_condition) == ("new", "new", "(won? [new] {Final})")
    assert Tag.query.filter_by(name="Champion").one().team == "new"
    assert ScriptVariable.query.filter_by(name="favorites").one().expression == "(list [new] [rival])"
    assert TeamRegistration.query.filter_by(team="new").count() == 1


@pytest.mark.integration
def test_teams_that_meet_in_a_match_cant_merge(app, test_db):
    _team("old")
    _team("new")
    _event("spring")
    db.session.flush()
    db.session.add(Match(name="Final", event="spring", team1="old", team2="new"))
    db.session.commit()

    preview = AccountMergeService.preview(db.session.get(Team, "old"), db.session.get(Team, "new")).unwrap()

    assert preview.conflicts == ["Both accounts are in Final (spring)."]


@pytest.mark.integration
def test_only_accounts_of_the_same_kind_merge(app, test_db):
    _player("someone")
    _team("squad")
    db.session.commit()

    result = AccountMergeService.preview(db.session.get(Team, "squad"), db.session.get(Player, "someone"))

    assert isinstance(result, Err)


@pytest.mark.integration
def test_merge_flow_over_http(client, test_db):
    _player("old")
    target = _player("new")
    db.session.commit()
    login_as(client, target)

    assert client.post("/_api/account/merge/verify", json={"username": "old", "password": "wrong"}).status_code == 401
    assert client.get("/_api/account/merge").get_json() == {"preview": None, "sign_in_error": None}

    preview = client.post("/_api/account/merge/verify", json={"username": "old", "password": "secret"})
    assert preview.status_code == 200, preview.get_json()
    assert preview.get_json()["preview"]["source"] == {"id": "old", "name": "Old"}
    assert preview.get_json()["preview"]["conflicts"] == []

    unconfirmed = client.post("/_api/account/merge", json={"confirm_username": "new"})
    assert unconfirmed.status_code == 400
    assert db.session.get(Player, "old") is not None

    merged = client.post("/_api/account/merge", json={"confirm_username": "old"})
    assert merged.status_code == 200, merged.get_json()
    assert db.session.get(Player, "old") is None
    assert client.get("/_api/me").get_json()["id"] == "new"
    assert client.get("/_api/check-username?username=old").get_json()["available"] is False
    assert client.post("/_api/register", json={"username": "old", "password": "x", "name": "Old"}).status_code == 409


@pytest.mark.integration
def test_proof_of_sign_in_expires(client, test_db):
    _player("old")
    target = _player("new")
    db.session.commit()
    login_as(client, target)
    assert client.post("/_api/account/merge/verify", json={"username": "old", "password": "secret"}).status_code == 200

    with client.session_transaction() as session:
        proof = session["merge_source"]
        proof["verified_at"] = (now_utc_naive() - timedelta(hours=1)).isoformat()
        session["merge_source"] = proof

    assert client.post("/_api/account/merge", json={"confirm_username": "old"}).status_code == 404
    assert db.session.get(Player, "old") is not None


@pytest.mark.integration
def test_google_sign_in_proves_the_other_account_without_switching_accounts(app, client, test_db, monkeypatch):
    from app.routes import auth

    _player("old", google_id="google-old")
    target = _player("new")
    db.session.commit()
    login_as(client, target)
    fake_google = SimpleNamespace(
        authorize_redirect=lambda redirect_uri, **params: auth.redirect("https://accounts.google.example"),
        authorize_access_token=lambda: {},
        server_metadata={"userinfo_endpoint": "https://google.example/userinfo"},
        get=lambda url: SimpleNamespace(json=lambda: {"sub": "google-old", "email": "old@example.com"}),
    )
    monkeypatch.setattr(type(auth.oauth), "google", fake_google, raising=False)
    monkeypatch.setitem(app.config, "GOOGLE_CLIENT_ID", "id")
    monkeypatch.setitem(app.config, "GOOGLE_CLIENT_SECRET", "secret")

    client.get("/_api/auth/google/login?purpose=merge")
    callback = client.get("/_api/auth/google/callback")

    assert callback.status_code == 302
    assert callback.headers["Location"].endswith("/account/merge")
    assert client.get("/_api/me").get_json()["id"] == "new"
    assert client.get("/_api/account/merge").get_json()["preview"]["source"]["id"] == "old"


@pytest.mark.integration
def test_signing_in_to_a_different_kind_of_account_explains_why_it_cant_merge(client, test_db):
    _team("squad")
    target = _player("new")
    db.session.commit()
    login_as(client, target)

    response = client.post("/_api/account/merge/verify", json={"username": "squad", "password": "secret"})

    assert response.get_json()["preview"] is None
    assert "player account" in response.get_json()["sign_in_error"]
