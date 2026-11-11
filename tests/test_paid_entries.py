"""Prepaid entries: keeping, transferring, and re-using a paid registration."""

from decimal import Decimal

import pytest

from app.domain.enums import (
    PaidEntryAction,
    PaidEntrySource,
    PaidEntryStatus,
    RegistrationStatus,
    TeamRegistrationStatus,
    UserType,
)
from app.error_values import Err, Ok
from app.services import paid_entry_service
from app.services._common import Scope
from app.services.registration_service import RegistrationService
from models import League, PaidEntry, PaidEntryEvent, Player, PlayerRegistration, Team, TeamRegistration, db
from tests.utils import login_as, make_registrable_config, mark_paid

pytestmark = pytest.mark.integration


def _player(player_id: str) -> Player:
    player = Player(id=player_id, name=player_id.title(), pw_hash="x")
    db.session.add(player)
    db.session.commit()
    return player


def _team(team_id: str) -> Team:
    team = Team(id=team_id, name=team_id.title(), pw_hash="x")
    db.session.add(team)
    db.session.commit()
    return team


@pytest.fixture(params=["event", "league"])
def paid_scope(request, tournament):
    """A scope with nonzero fees and open registration, as an event or a league."""
    if request.param == "event":
        tournament.registrable_config.team_reg_fee = Decimal("40.00")
        tournament.registrable_config.player_reg_fee = Decimal("10.00")
        db.session.commit()
        return Scope.event(tournament.url)
    cfg = make_registrable_config(
        team_registration_open=True,
        player_registration_open=True,
        team_reg_fee=Decimal("40.00"),
        player_reg_fee=Decimal("10.00"),
        n_max_teams=8,
    )
    db.session.add(League(url="paid-league", name="Paid League", published=True, registrable_config_id=cfg.id))
    db.session.commit()
    return Scope.league("paid-league")


def _registration(scope: Scope, model, **filters):
    scope_filters = {"league_id": scope.league_url} if scope.is_league else {"event": scope.event_url}
    return model.query.filter_by(**scope_filters, **filters).one()


def _register_paid_player(scope: Scope, player_id: str) -> PlayerRegistration:
    registration = RegistrationService.register_player(scope, player_id, None).unwrap()
    assert not registration.paid
    return mark_paid(registration, 1000)


def _register_paid_team(scope: Scope, team_id: str) -> TeamRegistration:
    registration = RegistrationService.register_team(scope, team_id, team_id.title()).unwrap()
    assert not registration.paid
    return mark_paid(registration, 4000)


def test_deregistered_player_keeps_entry_and_reregisters_paid(paid_scope, player):
    registration = _register_paid_player(paid_scope, player.id)

    assert RegistrationService.deregister_player(paid_scope, player.id).is_ok()
    entry = PaidEntry.query.one()
    assert entry.status == PaidEntryStatus.AVAILABLE
    assert not _registration(paid_scope, PlayerRegistration, player=player.id).paid

    again = RegistrationService.register_player(paid_scope, player.id, None).unwrap()
    assert again.id == registration.id
    assert again.paid
    assert again.applied_entry.id == entry.id
    assert again.amount_paid == Decimal("10.00")


def test_deregistered_team_keeps_entry_and_reregisters_paid(paid_scope, team):
    _register_paid_team(paid_scope, team.id)

    assert RegistrationService.deregister_team(paid_scope, team.id).is_ok()
    assert PaidEntry.query.one().status == PaidEntryStatus.AVAILABLE

    assert RegistrationService.register_team(paid_scope, team.id, "Back Again").unwrap().paid


def test_player_transfer_waits_for_recipient_who_still_needs_team_approval(paid_scope, player, team):
    recipient = _player("recipient")
    _register_paid_player(paid_scope, player.id)

    assert RegistrationService.deregister_player(paid_scope, player.id, transfer_to=recipient.id).is_ok()
    entry = PaidEntry.query.one()
    assert entry.holder_player == recipient.id
    assert entry.transferred_from == player.id
    assert entry.status == PaidEntryStatus.AVAILABLE

    registration = RegistrationService.register_player(paid_scope, recipient.id, team.id).unwrap()
    assert registration.status == RegistrationStatus.PENDING_TEAM_APPROVAL
    assert registration.paid
    assert entry.transferred_from is None
    assert paid_entry_service.revoke_transfer(entry).is_err()
    actions = [event.action for event in PaidEntryEvent.query.order_by(PaidEntryEvent.id)]
    assert actions[-3:] == [PaidEntryAction.RELEASED, PaidEntryAction.TRANSFERRED, PaidEntryAction.APPLIED]


def test_transfer_to_registered_unpaid_player_applies_at_once(paid_scope, player):
    recipient = _player("recipient")
    _register_paid_player(paid_scope, player.id)
    RegistrationService.register_player(paid_scope, recipient.id, None).unwrap()

    assert RegistrationService.deregister_player(paid_scope, player.id, transfer_to=recipient.id).is_ok()
    assert _registration(paid_scope, PlayerRegistration, player=recipient.id).paid


@pytest.mark.parametrize("return_transfer", [paid_entry_service.revoke_transfer, paid_entry_service.decline_transfer])
def test_unclaimed_transfer_returns_to_giver(paid_scope, player, return_transfer):
    recipient = _player("recipient")
    _register_paid_player(paid_scope, player.id)
    RegistrationService.deregister_player(paid_scope, player.id, transfer_to=recipient.id).unwrap()

    entry = PaidEntry.query.one()
    assert return_transfer(entry).is_ok()
    assert entry.holder_player == player.id
    assert entry.transferred_from is None
    assert RegistrationService.register_player(paid_scope, player.id, None).unwrap().paid


def test_transfer_to_holder_of_an_entry_is_rejected_and_nothing_changes(paid_scope, player):
    recipient = _player("recipient")
    _register_paid_player(paid_scope, player.id)
    _register_paid_player(paid_scope, recipient.id)

    match RegistrationService.deregister_player(paid_scope, player.id, transfer_to=recipient.id):
        case Err(error):
            assert "already have a paid entry" in error.message
        case Ok(_):
            pytest.fail("transfer to an entry holder should fail")
    registration = _registration(paid_scope, PlayerRegistration, player=player.id)
    assert registration.status == RegistrationStatus.CONFIRMED
    assert registration.paid


def test_transfer_without_entry_is_rejected(paid_scope, player):
    recipient = _player("recipient")
    RegistrationService.register_player(paid_scope, player.id, None).unwrap()

    assert RegistrationService.deregister_player(paid_scope, player.id, transfer_to=recipient.id).is_err()
    assert _registration(paid_scope, PlayerRegistration, player=player.id).status == RegistrationStatus.CONFIRMED


def test_team_transfer_reserves_its_place_under_the_cap(tournament):
    tournament.registrable_config.team_reg_fee = Decimal("40.00")
    tournament.registrable_config.n_max_teams = 2
    db.session.commit()
    scope = Scope.event(tournament.url)
    giver, other, recipient, latecomer = (_team(name) for name in ("giver", "other", "recipient", "latecomer"))
    _register_paid_team(scope, giver.id)
    RegistrationService.register_team(scope, other.id, "Other").unwrap()

    RegistrationService.deregister_team(scope, giver.id, transfer_to=recipient.id).unwrap()
    assert paid_entry_service.reserved_team_slots(scope) == 1
    assert RegistrationService.register_team(scope, latecomer.id, "Latecomer").is_err()

    registration = RegistrationService.register_team(scope, recipient.id, "Recipient").unwrap()
    assert registration.paid
    assert paid_entry_service.reserved_team_slots(scope) == 0


def test_team_deregistration_leaves_roster_registered_and_paid(paid_scope, team, player):
    pending_player = _player("pending")
    _register_paid_team(paid_scope, team.id)
    member = _register_paid_player(paid_scope, player.id)
    member.team = team.id
    db.session.commit()
    RegistrationService.register_player(paid_scope, pending_player.id, team.id).unwrap()

    RegistrationService.deregister_team(paid_scope, team.id).unwrap()

    for player_id, paid in ((player.id, True), (pending_player.id, False)):
        registration = _registration(paid_scope, PlayerRegistration, player=player_id)
        assert registration.team is None
        assert registration.status == RegistrationStatus.CONFIRMED
        assert registration.paid is paid


def test_free_registration_creates_free_entry(tournament, player):
    registration = RegistrationService.register_player(Scope.event(tournament.url), player.id, None).unwrap()
    assert registration.paid
    assert registration.applied_entry.source == PaidEntrySource.FREE


def test_manual_payment_marks_paid_and_unmarking_voids(paid_scope, player):
    registration = RegistrationService.register_player(paid_scope, player.id, None).unwrap()

    paid_entry_service.record_manual_payment(
        registration, paid=True, amount_cents=1000, method="cash", reference="r1", notes="n"
    )
    db.session.commit()
    assert registration.paid
    assert registration.applied_entry.source == PaidEntrySource.MANUAL
    assert registration.payments[0].entry_id == registration.applied_entry.id

    paid_entry_service.record_manual_payment(
        registration, paid=False, amount_cents=1000, method="cash", reference="r1", notes="n"
    )
    db.session.commit()
    assert not registration.paid
    assert registration.amount_paid == Decimal("10.00")
    assert PaidEntry.query.one().status == PaidEntryStatus.VOIDED


@pytest.mark.parametrize("void_entry", [False, True])
def test_organizer_deregistration_keeps_or_voids_entry(paid_scope, team, player, void_entry):
    _register_paid_team(paid_scope, team.id)
    _register_paid_player(paid_scope, player.id)

    RegistrationService.deregister_team_as_organizer(paid_scope, team.id, void_entry=void_entry, actor=player).unwrap()
    RegistrationService.deregister_player_as_organizer(
        paid_scope, player.id, void_entry=void_entry, actor=player
    ).unwrap()

    expected = PaidEntryStatus.VOIDED if void_entry else PaidEntryStatus.AVAILABLE
    assert {entry.status for entry in PaidEntry.query.all()} == {expected}
    assert _registration(paid_scope, TeamRegistration, team=team.id).status == TeamRegistrationStatus.CANCELLED


def test_transfer_endpoint_moves_available_entry_and_sender_can_revoke(client, tournament, player):
    tournament.registrable_config.player_reg_fee = Decimal("10.00")
    db.session.commit()
    scope = Scope.event(tournament.url)
    recipient = _player("recipient")
    _register_paid_player(scope, player.id)
    RegistrationService.deregister_player(scope, player.id).unwrap()
    entry_id = PaidEntry.query.one().id

    login_as(client, recipient)
    assert client.post(f"/_api/paid-entries/{entry_id}/transfer", json={"recipient": "someone"}).status_code == 403

    login_as(client, player)
    response = client.post(f"/_api/paid-entries/{entry_id}/transfer", json={"recipient": recipient.id})
    assert response.status_code == 200, response.get_json()
    assert response.get_json()["entry"]["holder"]["id"] == recipient.id

    login_as(client, recipient)
    held = client.get("/_api/paid-entries").get_json()["held"]
    assert [row["transferred_from"]["id"] for row in held] == [player.id]

    login_as(client, player)
    assert [row["id"] for row in client.get("/_api/paid-entries").get_json()["sent"]] == [entry_id]
    assert client.post(f"/_api/paid-entries/{entry_id}/revoke").status_code == 200
    assert paid_entry_service.held_entry(scope, UserType.PLAYER, player.id).is_some()
