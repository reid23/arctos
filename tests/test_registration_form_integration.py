"""Registration prerequisites are checked before state changes and stay private."""

from datetime import datetime, timedelta
import hashlib
import sqlite3
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
from flask import g

from app.domain.enums import RegistrationStatus
from app.error_values import Err, Ok
from app.exceptions import ArctosError, ValidationError
from app.services._common import Scope
from app.services.registration_form_service import RegistrationFormService
from app.services.registration_service import RegistrationService
from models import League, Player, PlayerRegistration, Team, TeamManager, TeamRegistration, TO, Tournament, db
from tests.utils import login_as as _login_as, make_registrable_config

pytestmark = pytest.mark.integration

FORM_FIELDS = {
    "registration_form_url": "https://docs.google.com/forms/d/e/test-form/viewform",
    "registration_form_entry_id": "123456",
    "registration_form_signing_key": hashlib.sha256(b"Integration form configuration").hexdigest(),
    "registration_form_sheet_id": "private-response-sheet",
    "registration_form_sheet_gid": 0,
    "registration_form_code_column": "Registration code",
}
PRIVATE_KEYS = {
    "registration_form_sheet_url",
    "registration_form_code_column",
    "registration_form_service_account_email",
    "registration_form_available",
    "registration_form_sheet_id",
    "registration_form_sheet_gid",
    "registration_form_entry_id",
    "registration_form_signing_key",
}

FORM_SETUP_URL = FORM_FIELDS["registration_form_url"] + "?usp=pp_url&entry.123456=ARCTOS_CODE"


@pytest.fixture(params=["tournament", "league"])
def scope(request, tournament, player, team):
    kind = request.param
    if kind == "tournament":
        entity = tournament
        root = f"/_api/{entity.url}"
        detail = f"/_api/tournaments/{entity.url}"
        service_scope = Scope.event(entity.url)
        registration_fields = {"event": entity.url}
        owner_fields = {"event": entity.url}
        settings_url = root + "/update-settings"
    else:
        entity = League(
            url="forms-league",
            name="Forms League",
            published=True,
            registrable_config_id=make_registrable_config(player_registration_open=True).id,
        )
        db.session.add(entity)
        db.session.flush()
        root = f"/_api/leagues/{entity.url}"
        detail = root
        service_scope = Scope.league(entity.url)
        registration_fields = {"league_id": entity.url}
        owner_fields = {"league_id": entity.url}
        settings_url = root + "/settings"
    config = entity.registrable_config
    for key, value in FORM_FIELDS.items():
        setattr(config, key, value)
    organizer = Player(id="forms-organizer", name="Form Organizer", pw_hash="test")
    db.session.add(organizer)
    db.session.add(TO(user_id=organizer.id, user_type="player", **owner_fields))
    db.session.add(TeamRegistration(team=team.id, pseudonym=team.name, status="CONFIRMED", **registration_fields))
    db.session.commit()
    return SimpleNamespace(
        kind=kind,
        entity=entity,
        config=config,
        root=root,
        detail=detail,
        settings_url=settings_url,
        service_scope=service_scope,
        registration_fields=registration_fields,
        organizer=organizer,
        player=player,
        team=team,
        edit_url=detail + "/registrations/player/me",
        form_url=detail + "/registration-form",
    )


def login_as(client, user):
    g.pop("_login_user", None)
    _login_as(client, user)


def requirement_stub(monkeypatch, scope, outcome):
    calls = []

    def require(config, username):
        assert config is scope.config
        calls.append(username)
        return outcome

    monkeypatch.setattr(RegistrationFormService, "require_response", staticmethod(require))
    return calls


def unavailable():
    return Err(ArctosError("Form verification is unavailable. Try again shortly.", status_code=503))


def registration(scope, *, status=RegistrationStatus.PENDING_TEAM_APPROVAL):
    row = PlayerRegistration(
        **scope.registration_fields,
        player=scope.player.id,
        team=scope.team.id,
        jersey_name="Original",
        jersey_number="7",
        status=status,
        paid=False,
        amount_paid=12,
        waiver_legal_name_signature="Original signature",
        waiver_legal_name_signature_sha256="original-checksum",
    )
    db.session.add(row)
    db.session.commit()
    return row


def row_values(row):
    return (
        row.team,
        row.jersey_name,
        row.jersey_number,
        row.status,
        row.paid,
        row.amount_paid,
        row.paid_at,
        row.waiver_legal_name_signature,
        row.waiver_legal_name_signature_sha256,
    )


@pytest.mark.parametrize("team_request", [False, True])
@pytest.mark.parametrize("failure", ["missing", "unavailable"])
def test_registration_requires_response_and_preserves_http_status(client, scope, monkeypatch, team_request, failure):
    failure_result = Err(ValidationError("Complete the registration form.")) if failure == "missing" else unavailable()
    calls = requirement_stub(monkeypatch, scope, failure_result)
    login_as(client, scope.player)
    data = {"jersey_name": "New", "jersey_number": "12"}
    if team_request:
        data["team"] = scope.team.id
    response = client.post(scope.root + "/register-player", data=data)
    assert response.status_code == (400 if failure == "missing" else 503)
    assert calls == [scope.player.id]
    assert PlayerRegistration.query.filter_by(**scope.registration_fields).count() == 0

    requirement_stub(monkeypatch, scope, Ok(None))
    response = client.post(scope.root + "/register-player", data=data)
    assert response.status_code == 200, response.get_json()
    row = PlayerRegistration.query.filter_by(**scope.registration_fields).one()
    expected = RegistrationStatus.PENDING_TEAM_APPROVAL if team_request else RegistrationStatus.CONFIRMED
    assert row.status == expected
    assert row.jersey_name == "New"


@pytest.mark.parametrize("status", [RegistrationStatus.CANCELLED, RegistrationStatus.REJECTED])
def test_failed_reregistration_leaves_existing_row_clean(scope, monkeypatch, status):
    row = registration(scope, status=status)
    before = row_values(row)
    scope.config.waiver_filepath = "waiver.pdf"
    scope.config.waiver_sha256 = "current-checksum"
    db.session.commit()
    requirement_stub(monkeypatch, scope, unavailable())
    result = RegistrationService.register_player(
        scope.service_scope,
        scope.player.id,
        None,
        jersey_name="Changed",
        jersey_number="99",
        waiver_legal_name_signature="New signature",
    )
    assert isinstance(result, Err)
    assert not db.session.is_modified(row)
    db.session.commit()
    db.session.refresh(row)
    assert row_values(row) == before


def test_local_validation_precedes_form_lookup(scope, monkeypatch):
    calls = requirement_stub(monkeypatch, scope, unavailable())
    scope.config.waiver_filepath = "waiver.pdf"
    scope.config.waiver_sha256 = "checksum"
    db.session.commit()
    result = RegistrationService.register_player(scope.service_scope, scope.player.id, None)
    assert isinstance(result, Err)
    assert "Waiver signature" in result.unwrap_err().message
    assert calls == []


@pytest.fixture
def manager(scope):
    actor = Player(id="forms-manager", name="Team Manager", pw_hash="test")
    db.session.add(actor)
    db.session.flush()
    db.session.add(TeamManager(team_id=scope.team.id, player_id=actor.id))
    db.session.commit()
    return actor


def roster_endpoint(scope, row, action, route="managed"):
    if route == "legacy":
        legacy_action = "accept" if action == "approve" else action
        return f"{scope.root}/invitation/{row.id}/{legacy_action}"
    kind = "leagues" if scope.kind == "league" else "events"
    return f"/_api/teams/{scope.team.id}/{kind}/{scope.entity.url}/roster/{row.id}/{action}"


@pytest.mark.parametrize("action", ["approve", "decline"])
@pytest.mark.parametrize("route", ["legacy", "managed"])
@pytest.mark.parametrize("actor", ["owner", "manager"])
@pytest.mark.parametrize("failure", ["missing", "unavailable"])
def test_pending_team_decisions_verify_before_mutation(
    client, scope, manager, monkeypatch, action, route, actor, failure
):
    row = registration(scope)
    before = row_values(row)
    team_row = TeamRegistration.query.filter_by(team=scope.team.id, **scope.registration_fields).one()
    team_row.paid, team_row.amount_paid = True, 43
    db.session.commit()
    login_as(client, scope.team if actor == "owner" else manager)
    outcome = Err(ValidationError("Complete the registration form.")) if failure == "missing" else unavailable()
    calls = requirement_stub(monkeypatch, scope, outcome)
    endpoint = roster_endpoint(scope, row, action, route)
    data = {"team_id": scope.team.id}
    response = client.post(endpoint, data=data)
    assert response.status_code == (400 if failure == "missing" else 503)
    assert calls == [scope.player.id]
    db.session.refresh(row)
    db.session.refresh(team_row)
    assert row_values(row) == before
    assert team_row.paid and team_row.amount_paid == 43

    requirement_stub(monkeypatch, scope, Ok(None))
    assert client.post(endpoint, data=data).status_code == 200
    db.session.refresh(row)
    assert row.status == RegistrationStatus.CONFIRMED
    assert row.team == (scope.team.id if action == "approve" else None)
    # Only team and status change when confirming a pending registration.
    assert row_values(row)[1:3] == before[1:3]
    assert row_values(row)[4:] == before[4:]


@pytest.mark.parametrize("actor", ["owner", "manager"])
def test_confirmed_roster_changes_do_not_repeat_form_verification(client, scope, manager, monkeypatch, actor):
    row = registration(scope, status=RegistrationStatus.CONFIRMED)
    row.team = None
    db.session.commit()
    before = row_values(row)
    login_as(client, scope.team if actor == "owner" else manager)
    calls = requirement_stub(monkeypatch, scope, unavailable())
    for action in ("add", "remove"):
        response = client.post(roster_endpoint(scope, row, action))
        assert response.status_code == 200, response.get_json()
    db.session.refresh(row)
    assert row_values(row) == before
    assert calls == []


@pytest.mark.parametrize("action", ["approve", "decline"])
@pytest.mark.parametrize("change", ["player", "team", "status", "manager", "form", "config", "enable"])
def test_pending_decision_rechecks_changes_during_provider_read(client, scope, manager, monkeypatch, action, change):
    row = registration(scope)
    before = row_values(row)
    alternate_config = make_registrable_config(player_registration_open=True, **FORM_FIELDS)
    if change == "enable":
        for field in FORM_FIELDS:
            setattr(scope.config, field, None)
    db.session.commit()
    login_as(client, manager)
    row_id, config_id, entity_id = row.id, scope.config.id, scope.entity.url
    manager_id, team_id, alternate_id = manager.id, scope.team.id, alternate_config.id
    replacement_player = scope.organizer.id

    def verify(config, player_id):
        assert player_id == scope.player.id
        # A separate writer must succeed during Google I/O. A roster write lock
        # held too early makes this fail within one second instead of blocking.
        with sqlite3.connect(db.engine.url.database, timeout=1) as connection:
            if change in {"player", "team", "status"}:
                value = {"player": replacement_player, "team": None, "status": "CANCELLED"}[change]
                connection.execute(f"UPDATE player_registrations SET {change}=? WHERE id=?", (value, row_id))
            elif change == "manager":
                connection.execute("DELETE FROM team_managers WHERE team_id=? AND player_id=?", (team_id, manager_id))
            elif change == "form":
                connection.execute(
                    "UPDATE registrable_configs SET registration_form_signing_key=? WHERE id=?", ("a" * 64, config_id)
                )
            elif change == "config":
                table = "leagues" if scope.kind == "league" else "tournaments"
                connection.execute(f"UPDATE {table} SET registrable_config_id=? WHERE url=?", (alternate_id, entity_id))
            else:
                for field, value in FORM_FIELDS.items():
                    connection.execute(f"UPDATE registrable_configs SET {field}=? WHERE id=?", (value, config_id))
        return Ok(None)

    monkeypatch.setattr(RegistrationFormService, "require_response", staticmethod(verify))
    response = client.post(roster_endpoint(scope, row, action))
    assert response.status_code == (403 if change == "manager" else 400), response.get_json()
    db.session.refresh(row)
    expected = list(before)
    if change == "team":
        expected[0] = None
    elif change == "status":
        expected[3] = RegistrationStatus.CANCELLED
    assert row_values(row) == tuple(expected)
    assert row.player == (replacement_player if change == "player" else scope.player.id)


def test_team_decision_checks_ownership_before_lookup(client, scope, monkeypatch):
    row = registration(scope)
    other_team = Team(id="other-team", name="Other", pw_hash="test")
    db.session.add(other_team)
    db.session.commit()
    login_as(client, other_team)
    calls = requirement_stub(monkeypatch, scope, unavailable())
    assert client.post(f"{scope.root}/invitation/{row.id}/accept").status_code == 404
    assert calls == []


def test_pending_self_removal_preserves_all_fields_on_verification_failure(client, scope, monkeypatch):
    row = registration(scope)
    before = row_values(row)
    login_as(client, scope.player)
    requirement_stub(monkeypatch, scope, unavailable())
    data = {"team": None, "jersey_name": "Changed", "jersey_number": "99"}
    response = client.put(scope.edit_url, json=data)
    assert response.status_code == 503
    db.session.refresh(row)
    assert row_values(row) == before

    requirement_stub(monkeypatch, scope, Ok(None))
    assert client.put(scope.edit_url, json=data).status_code == 200
    db.session.refresh(row)
    assert row.status == RegistrationStatus.CONFIRMED and row.team is None
    assert row.jersey_name == "Changed"


def test_confirmed_players_keep_registration_when_requirement_changes(client, scope, monkeypatch):
    row = registration(scope, status=RegistrationStatus.CONFIRMED)
    login_as(client, scope.player)
    calls = requirement_stub(monkeypatch, scope, unavailable())
    assert client.put(scope.edit_url, json={"team": None, "jersey_name": "Updated"}).status_code == 200
    db.session.refresh(row)
    assert row.status == RegistrationStatus.CONFIRMED
    assert row.jersey_name == "Updated"
    assert calls == []


@pytest.mark.parametrize("scope", ["tournament"], indirect=True)
def test_organizer_registration_verifies_target_before_changes(client, scope, monkeypatch):
    row = registration(scope, status=RegistrationStatus.CANCELLED)
    before = row_values(row)
    calls = requirement_stub(monkeypatch, scope, unavailable())
    login_as(client, scope.organizer)
    response = client.post(scope.root + "/register-player-as-to", json={"player_id": scope.player.id})
    assert response.status_code == 503
    assert calls == [scope.player.id]
    db.session.refresh(row)
    assert row_values(row) == before
    requirement_stub(monkeypatch, scope, Ok(None))
    assert client.post(scope.root + "/register-player-as-to", json={"player_id": scope.player.id}).status_code == 200
    db.session.refresh(row)
    assert row.status == RegistrationStatus.CONFIRMED


def settings_payload(scope, **changes):
    data = {
        "registration_form_required": "true" if scope.kind == "tournament" else True,
        "registration_form_url": FORM_SETUP_URL,
        "registration_form_sheet_url": "https://docs.google.com/spreadsheets/d/private-response-sheet/edit#gid=0",
        "registration_form_code_column": "Registration code",
        "about": "Updated information",
    }
    if scope.kind == "tournament":
        data.update(name=scope.entity.name, end_date="2030-12-31", player_registration_open="on")
    data.update(changes)
    return data


def post_settings(client, scope, data):
    return client.post(scope.settings_url, **({"data": data} if scope.kind == "tournament" else {"json": data}))


def test_settings_verify_before_committing_other_fields(client, scope, monkeypatch):
    before_about = scope.entity.about
    before_config = {key: getattr(scope.config, key) for key in FORM_FIELDS}
    calls = []

    def validate(**values):
        calls.append(values)
        return unavailable()

    monkeypatch.setattr(RegistrationFormService, "validate_settings", staticmethod(validate))
    login_as(client, scope.organizer)
    response = post_settings(client, scope, settings_payload(scope))
    assert response.status_code == 503
    assert calls[0]["enabled"] is True
    assert calls[0]["code_column"] == "Registration code"
    assert calls[0]["form_url"] == FORM_SETUP_URL
    assert calls[0]["current_config"] is scope.config
    db.session.refresh(scope.entity)
    db.session.refresh(scope.config)
    assert scope.entity.about == before_about
    assert {key: getattr(scope.config, key) for key in FORM_FIELDS} == before_config

    monkeypatch.setattr(RegistrationFormService, "validate_settings", staticmethod(lambda **_: Ok(FORM_FIELDS)))
    response = post_settings(client, scope, settings_payload(scope))
    assert response.status_code == 200, response.get_json()
    db.session.refresh(scope.entity)
    assert scope.entity.about == "Updated information"


def test_settings_omission_preserves_requirement_and_explicit_disable_clears_it(client, scope):
    login_as(client, scope.organizer)
    data = {key: value for key, value in settings_payload(scope).items() if not key.startswith("registration_form_")}
    assert post_settings(client, scope, data).status_code == 200
    db.session.refresh(scope.config)
    assert scope.config.registration_form_url == FORM_FIELDS["registration_form_url"]
    data["registration_form_required"] = "false" if scope.kind == "tournament" else False
    assert post_settings(client, scope, data).status_code == 200
    db.session.refresh(scope.config)
    assert all(getattr(scope.config, key) is None for key in FORM_FIELDS)


def test_settings_require_boolean_choice_and_permission_before_provider(client, scope, monkeypatch):
    def unexpected(**_):
        pytest.fail("Invalid or unauthorized settings must not contact Google")

    monkeypatch.setattr(RegistrationFormService, "validate_settings", staticmethod(unexpected))
    login_as(client, scope.player)
    assert post_settings(client, scope, settings_payload(scope)).status_code == 403
    login_as(client, scope.organizer)
    assert (
        post_settings(client, scope, settings_payload(scope, registration_form_required="invalid")).status_code == 400
    )
    data = settings_payload(scope)
    del data["registration_form_required"]
    assert post_settings(client, scope, data).status_code == 400


def test_settings_accept_only_parsed_form_fields_and_server_owned_key(client, scope, monkeypatch):
    calls = []

    def validate(**values):
        calls.append(values)
        return Ok(FORM_FIELDS)

    monkeypatch.setattr(RegistrationFormService, "validate_settings", staticmethod(validate))
    login_as(client, scope.organizer)
    response = post_settings(
        client,
        scope,
        settings_payload(
            scope,
            registration_form_signing_key="client-supplied-key",
            registration_form_entry_id="999999",
            registration_form_sheet_id="client-supplied-sheet",
        ),
    )
    assert response.status_code == 200
    assert set(calls[0]) == {"enabled", "form_url", "sheet_url", "code_column", "current_config"}
    assert calls[0]["current_config"] is scope.config
    db.session.refresh(scope.config)
    assert {key: getattr(scope.config, key) for key in FORM_FIELDS} == FORM_FIELDS


def test_response_sheet_settings_are_organizer_only(client, scope):
    for user in (None, scope.player, scope.team):
        g.pop("_login_user", None)
        with client.session_transaction() as session:
            session.clear()
        if user is not None:
            login_as(client, user)
        payload = client.get(scope.detail).get_json()[scope.kind]
        assert payload["registration_form_required"] is True
        assert payload["registration_form_url"] == FORM_FIELDS["registration_form_url"]
        assert PRIVATE_KEYS.isdisjoint(payload)
        assert "private-response-sheet" not in str(payload)
        assert FORM_FIELDS["registration_form_signing_key"] not in str(payload)

    login_as(client, scope.organizer)
    payload = client.get(scope.detail).get_json()[scope.kind]
    assert "private-response-sheet" in payload["registration_form_sheet_url"]
    assert payload["registration_form_code_column"] == "Registration code"
    assert payload["registration_form_url"] == FORM_SETUP_URL
    assert "registration_form_entry_id" not in payload
    assert "registration_form_signing_key" not in payload
    assert FORM_FIELDS["registration_form_signing_key"] not in str(payload)
    listing = client.get("/_api/tournaments" if scope.kind == "tournament" else "/_api/leagues").get_json()
    assert "private-response-sheet" not in str(listing)
    assert FORM_FIELDS["registration_form_signing_key"] not in str(listing)

    # User type is part of organizer identity even when the textual ID matches.
    matching_team = Team(id=scope.organizer.id, name="Unrelated team", pw_hash="test")
    db.session.add(matching_team)
    db.session.commit()
    # Supply the resolved principal directly: legacy session IDs contain no
    # account type, so their loader selects the player when IDs collide.
    g._login_user = matching_team
    assert PRIVATE_KEYS.isdisjoint(client.get(scope.detail).get_json()[scope.kind])


@pytest.mark.parametrize("scope", ["league"], indirect=True)
def test_league_event_inherits_public_requirement_and_rejects_event_settings(client, scope):
    event = Tournament(
        url="forms-league-round",
        name="League Round",
        league_id=scope.entity.url,
        published=True,
        start_date=datetime(2030, 1, 1),
        end_date=datetime(2030, 1, 1) + timedelta(days=1),
    )
    db.session.add(event)
    db.session.commit()
    payload = client.get(f"/_api/tournaments/{event.url}").get_json()["tournament"]
    assert payload["registration_form_required"] is True
    assert payload["registration_form_url"] == FORM_FIELDS["registration_form_url"]
    assert PRIVATE_KEYS.isdisjoint(payload)
    login_as(client, scope.organizer)
    response = client.post(
        f"/_api/{event.url}/update-settings",
        data={"name": event.name, "end_date": "2030-01-02", "registration_form_required": "false"},
    )
    assert response.status_code == 400
    assert "league settings" in response.get_json()["error"]
    db.session.refresh(scope.config)
    assert scope.config.registration_form_url == FORM_FIELDS["registration_form_url"]


def test_personal_form_requires_a_player_session(client, scope, monkeypatch):
    def unexpected(*_):
        pytest.fail("An unauthenticated or team request must not create a personal form link")

    monkeypatch.setattr(RegistrationFormService, "personal_url", staticmethod(unexpected))
    assert client.get(scope.form_url).status_code == 401
    login_as(client, scope.team)
    assert client.get(scope.form_url).status_code == 403


def test_personal_form_uses_session_identity_without_contacting_google(client, scope, monkeypatch):
    def unexpected(*_, **__):
        pytest.fail("Opening a personalized form must not contact Google")

    monkeypatch.setattr("requests.sessions.Session.request", unexpected)
    locations = []
    for user in (scope.player, scope.organizer):
        login_as(client, user)
        response = client.get(scope.form_url + "?player_id=another-player&username=another-player&entry.123456=forged")
        assert response.status_code == 302
        assert response.headers["Cache-Control"] == "no-store"
        assert response.headers["Referrer-Policy"] == "no-referrer"
        location = response.headers["Location"]
        assert location == RegistrationFormService.personal_url(scope.config, user.id).unwrap()
        parsed = urlsplit(location)
        assert parsed.scheme == "https" and parsed.netloc == "docs.google.com"
        assert parsed.path == "/forms/d/e/test-form/viewform"
        query = parse_qs(parsed.query)
        assert set(query) == {"usp", "entry.123456"}
        assert query["entry.123456"][0].startswith("arctos_")
        assert user.id not in location
        assert FORM_FIELDS["registration_form_signing_key"] not in location
        locations.append(location)
    assert locations[0] != locations[1]
    login_as(client, scope.player)
    assert client.get(scope.form_url).headers["Location"] == locations[0]
    assert PlayerRegistration.query.filter_by(**scope.registration_fields).count() == 0


def test_personal_form_draft_requires_scope_organizer(client, scope):
    scope.entity.published = False
    db.session.commit()
    login_as(client, scope.player)
    assert client.get(scope.form_url).status_code == 403
    login_as(client, scope.organizer)
    assert client.get(scope.form_url).status_code == 302


@pytest.mark.parametrize(
    "field, value, status",
    [
        ("registration_form_url", None, 400),
        ("registration_form_signing_key", None, 503),
        ("registration_form_entry_id", None, 503),
        ("registration_form_url", "https://example.test/form", 503),
    ],
)
def test_personal_form_reports_disabled_or_incomplete_configuration(client, scope, field, value, status):
    setattr(scope.config, field, value)
    db.session.commit()
    login_as(client, scope.player)
    response = client.get(scope.form_url)
    assert response.status_code == status
    assert "Location" not in response.headers
    assert "error" in response.get_json()


def test_personal_form_missing_scope_returns_not_found(client, scope):
    login_as(client, scope.player)
    root = "/_api/tournaments" if scope.kind == "tournament" else "/_api/leagues"
    assert client.get(root + "/missing/registration-form").status_code == 404


@pytest.mark.parametrize("scope", ["league"], indirect=True)
def test_event_personal_form_inherits_league_config_and_draft_permissions(client, scope):
    event = Tournament(
        url="form-link-league-round",
        name="League Round",
        league_id=scope.entity.url,
        published=True,
        start_date=datetime(2030, 1, 1),
        end_date=datetime(2030, 1, 2),
    )
    db.session.add(event)
    db.session.commit()
    event_form = f"/_api/tournaments/{event.url}/registration-form"
    login_as(client, scope.player)
    assert client.get(event_form).headers["Location"] == client.get(scope.form_url).headers["Location"]
    event.published = False
    db.session.commit()
    assert client.get(event_form).status_code == 403
    login_as(client, scope.organizer)
    assert client.get(event_form).status_code == 302
