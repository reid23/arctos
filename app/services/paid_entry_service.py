"""Prepaid registration entries: the only writer of entries and their history.

A registration is paid while an ``APPLIED``
:class:`~app.models.payments.PaidEntry` covers it. Deregistering releases
the entry back to its holder, who may re-register without paying or
transfer it to another account of the same kind. See
:mod:`app.models.payments` for the data model.

Functions here flush but never commit; callers own the transaction.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from app.domain.enums import (
    PaidEntryAction,
    PaidEntrySource,
    PaidEntryStatus,
    RegistrationStatus,
    TeamRegistrationStatus,
    UserType,
)
from app.error_values import Err, Null, Ok, Option, Result, Some
from app.exceptions import ArctosError, ValidationError
from app.services._common import Scope
from app.utils.datetime_helpers import now_utc_naive
from models import (
    PaidEntry,
    PaidEntryEvent,
    Payment,
    Player,
    PlayerRegistration,
    Team,
    TeamRegistration,
    db,
)

ACTIVE_PLAYER_STATUSES = (RegistrationStatus.CONFIRMED, RegistrationStatus.PENDING_TEAM_APPROVAL)


def scope_of(row: TeamRegistration | PlayerRegistration | PaidEntry) -> Scope:
    return Scope.league(row.league_id) if row.league_id else Scope.event(row.event)


def _scope_filters(scope: Scope) -> dict[str, str]:
    return {"league_id": scope.league_url} if scope.is_league else {"event": scope.event_url}


def _kind_of(registration: TeamRegistration | PlayerRegistration) -> UserType:
    return UserType.TEAM if isinstance(registration, TeamRegistration) else UserType.PLAYER


def _holder_of(registration: TeamRegistration | PlayerRegistration) -> str:
    return registration.team if isinstance(registration, TeamRegistration) else registration.player


def _holder_filters(kind: UserType, holder_id: str) -> dict[str, str]:
    return {"holder_team": holder_id} if kind == UserType.TEAM else {"holder_player": holder_id}


def _actor_fields(actor) -> dict[str, str | None]:
    from app.services.permission_service import PermissionService

    match PermissionService.user_type(actor):
        case Some(user_type):
            return {"actor_user_id": actor.id, "actor_user_type": str(user_type)}
        case _:
            return {"actor_user_id": None, "actor_user_type": None}


def _record(
    entry: PaidEntry,
    action: PaidEntryAction,
    *,
    actor=None,
    from_holder: str | None = None,
    to_holder: str | None = None,
) -> None:
    db.session.add(
        PaidEntryEvent(
            entry_id=entry.id,
            action=action,
            from_holder=from_holder,
            to_holder=to_holder,
            **_actor_fields(actor),
        )
    )


def _refresh_paid_state(registration: TeamRegistration | PlayerRegistration) -> None:
    db.session.flush()
    db.session.expire(registration, ["applied_entry", "payments"])


def held_entry(scope: Scope, kind: UserType, holder_id: str) -> Option[PaidEntry]:
    """The holder's non-voided entry for *scope*, if any (there is at most one)."""
    entry = (
        PaidEntry.query.filter_by(**_scope_filters(scope), kind=kind.value, **_holder_filters(kind, holder_id))
        .filter(PaidEntry.status != PaidEntryStatus.VOIDED)
        .first()
    )
    return Some(entry) if entry is not None else Null()


def active_registration(scope: Scope, kind: UserType, holder_id: str) -> Option[TeamRegistration | PlayerRegistration]:
    """The holder's current team or player registration for *scope*, if any."""
    if kind == UserType.TEAM:
        registration = TeamRegistration.query.filter_by(
            **_scope_filters(scope), team=holder_id, status=TeamRegistrationStatus.CONFIRMED
        ).first()
    else:
        registration = (
            PlayerRegistration.query.filter_by(**_scope_filters(scope), player=holder_id)
            .filter(PlayerRegistration.status.in_(ACTIVE_PLAYER_STATUSES))
            .first()
        )
    return Some(registration) if registration is not None else Null()


def reserved_team_slots(scope: Scope, *, excluding_team: str | None = None) -> int:
    """Unclaimed transferred team entries that hold a place under the team cap."""
    query = PaidEntry.query.filter_by(
        **_scope_filters(scope),
        kind=UserType.TEAM.value,
        status=PaidEntryStatus.AVAILABLE,
        reserves_team_slot=True,
    )
    if excluding_team is not None:
        query = query.filter(PaidEntry.holder_team != excluding_team)
    return query.count()


def _apply(entry: PaidEntry, registration: TeamRegistration | PlayerRegistration, *, actor=None) -> None:
    if isinstance(registration, TeamRegistration):
        entry.team_registration_id = registration.id
    else:
        entry.player_registration_id = registration.id
    entry.status = PaidEntryStatus.APPLIED
    entry.applied_at = now_utc_naive()
    entry.transferred_from = None
    entry.reserves_team_slot = False
    _record(entry, PaidEntryAction.APPLIED, actor=actor)


def cover_registration(
    registration: TeamRegistration | PlayerRegistration,
    *,
    source_if_new: PaidEntrySource | None,
    actor=None,
) -> Option[PaidEntry]:
    """Mark *registration* paid with the holder's entry, or a new one from *source_if_new*.

    Returns the covering entry, or ``Null`` when the holder has no entry and
    *source_if_new* is ``None``.
    """
    db.session.flush()
    scope = scope_of(registration)
    kind = _kind_of(registration)
    holder_id = _holder_of(registration)
    match held_entry(scope, kind, holder_id):
        case Some(entry):
            if not (entry.status == PaidEntryStatus.APPLIED and _covers(entry, registration)):
                _apply(entry, registration, actor=actor)
        case _:
            if source_if_new is None:
                return Null()
            entry = PaidEntry(
                **_scope_filters(scope),
                kind=kind.value,
                **_holder_filters(kind, holder_id),
                status=PaidEntryStatus.AVAILABLE,
                source=source_if_new,
            )
            db.session.add(entry)
            db.session.flush()
            _record(entry, PaidEntryAction.CREATED, actor=actor, to_holder=holder_id)
            _apply(entry, registration, actor=actor)
    _refresh_paid_state(registration)
    return Some(entry)


def _covers(entry: PaidEntry, registration: TeamRegistration | PlayerRegistration) -> bool:
    if isinstance(registration, TeamRegistration):
        return entry.team_registration_id == registration.id
    return entry.player_registration_id == registration.id


def release_registration(registration: TeamRegistration | PlayerRegistration, *, actor=None) -> Option[PaidEntry]:
    """Return the entry covering *registration* to its holder as ``AVAILABLE``."""
    entry = registration.applied_entry
    if entry is None:
        return Null()
    entry.status = PaidEntryStatus.AVAILABLE
    entry.team_registration_id = None
    entry.player_registration_id = None
    _record(entry, PaidEntryAction.RELEASED, actor=actor)
    _refresh_paid_state(registration)
    return Some(entry)


def void_entry(entry: PaidEntry, *, actor=None) -> None:
    """Stop *entry* from granting a registration. Its history is kept."""
    if entry.status == PaidEntryStatus.VOIDED:
        return
    entry.status = PaidEntryStatus.VOIDED
    entry.team_registration_id = None
    entry.player_registration_id = None
    entry.transferred_from = None
    entry.reserves_team_slot = False
    _record(entry, PaidEntryAction.VOIDED, actor=actor)
    db.session.flush()


def transfer_entry(
    entry: PaidEntry,
    recipient_id: str,
    *,
    actor=None,
    reserve_team_slot: bool = False,
) -> Result[PaidEntry, ArctosError]:
    """Move an unapplied *entry* to *recipient_id*, an account of the same kind.

    When the recipient is already registered but unpaid, the entry covers
    that registration at once. Otherwise it waits for them as ``AVAILABLE``
    and the giver may revoke it until it is claimed.
    """
    kind = UserType(entry.kind)
    giver_id = entry.holder
    if entry.status != PaidEntryStatus.AVAILABLE:
        return Err(ValidationError("Only an entry that isn't covering a registration can be transferred."))
    recipient_id = (recipient_id or "").strip()
    if recipient_id == giver_id:
        return Err(ValidationError("Choose someone else to transfer this entry to."))
    if kind == UserType.TEAM:
        recipient = db.session.get(Team, recipient_id)
        if recipient is None or recipient.placeholder_event is not None:
            return Err(ValidationError("Team not found."))
    elif db.session.get(Player, recipient_id) is None:
        return Err(ValidationError("Player not found."))
    scope = scope_of(entry)
    if held_entry(scope, kind, recipient_id).is_some():
        return Err(ValidationError("They already have a paid entry for this event."))

    if kind == UserType.TEAM:
        entry.holder_team = recipient_id
    else:
        entry.holder_player = recipient_id
    entry.transferred_from = giver_id
    entry.reserves_team_slot = reserve_team_slot and kind == UserType.TEAM
    _record(entry, PaidEntryAction.TRANSFERRED, actor=actor, from_holder=giver_id, to_holder=recipient_id)
    db.session.flush()
    match active_registration(scope, kind, recipient_id):
        case Some(registration):
            cover_registration(registration, source_if_new=None, actor=actor)
    return Ok(entry)


def _return_to_giver(entry: PaidEntry, action: PaidEntryAction, *, actor=None) -> Result[PaidEntry, ArctosError]:
    if entry.status != PaidEntryStatus.AVAILABLE or entry.transferred_from is None:
        return Err(ValidationError("This transfer has already been claimed or returned."))
    kind = UserType(entry.kind)
    giver_id = entry.transferred_from
    if held_entry(scope_of(entry), kind, giver_id).is_some():
        return Err(ValidationError("The original holder already has another paid entry for this event."))
    recipient_id = entry.holder
    if kind == UserType.TEAM:
        entry.holder_team = giver_id
    else:
        entry.holder_player = giver_id
    entry.transferred_from = None
    entry.reserves_team_slot = False
    _record(entry, action, actor=actor, from_holder=recipient_id, to_holder=giver_id)
    db.session.flush()
    return Ok(entry)


def revoke_transfer(entry: PaidEntry, *, actor=None) -> Result[PaidEntry, ArctosError]:
    """Take back an unclaimed transfer; the entry returns to the giver."""
    return _return_to_giver(entry, PaidEntryAction.TRANSFER_REVOKED, actor=actor)


def decline_transfer(entry: PaidEntry, *, actor=None) -> Result[PaidEntry, ArctosError]:
    """Refuse an unclaimed transfer; the entry returns to the giver."""
    return _return_to_giver(entry, PaidEntryAction.TRANSFER_DECLINED, actor=actor)


def parse_amount_cents(text: str | None) -> Result[int, ArctosError]:
    """Parse a nonnegative currency amount such as ``"12.50"`` into cents."""
    try:
        amount = Decimal((text or "0").strip() or "0")
    except InvalidOperation:
        return Err(ValidationError("Enter a valid amount of zero or more."))
    if not amount.is_finite() or amount < 0:
        return Err(ValidationError("Enter a valid amount of zero or more."))
    return Ok(int((amount * 100).quantize(Decimal(1))))


def record_manual_payment(
    registration: TeamRegistration | PlayerRegistration,
    *,
    paid: bool,
    amount_cents: int,
    method: str,
    reference: str,
    notes: str,
    actor=None,
) -> None:
    """Apply an organizer's manual payment edit to *registration*.

    The amount and details live on a single manual
    :class:`~app.models.payments.Payment`. Marking paid covers the
    registration with the holder's entry or a new ``MANUAL`` one; marking
    unpaid voids the covering entry.
    """
    db.session.flush()
    manual = next(iter(registration.payments), None)
    has_details = amount_cents > 0 or method != "" or reference != "" or notes != ""
    if has_details:
        if manual is None:
            manual = Payment(
                team_registration_id=registration.id if isinstance(registration, TeamRegistration) else None,
                player_registration_id=registration.id if isinstance(registration, PlayerRegistration) else None,
            )
            db.session.add(manual)
        manual.amount_cents = amount_cents
        manual.method = method
        manual.reference = reference
        manual.notes = notes
    elif manual is not None:
        db.session.delete(manual)
        manual = None

    if paid:
        match cover_registration(registration, source_if_new=PaidEntrySource.MANUAL, actor=actor):
            case Some(entry) if manual is not None:
                manual.entry_id = entry.id
    elif registration.applied_entry is not None:
        void_entry(registration.applied_entry, actor=actor)
    _refresh_paid_state(registration)


def delete_for_scope(scope: Scope) -> None:
    """Delete every entry, entry event, and payment in *scope* before its registrations go."""
    filters = _scope_filters(scope)
    entry_ids = db.session.query(PaidEntry.id).filter_by(**filters)
    PaidEntryEvent.query.filter(PaidEntryEvent.entry_id.in_(entry_ids)).delete(synchronize_session=False)
    team_registration_ids = db.session.query(TeamRegistration.id).filter_by(**filters)
    player_registration_ids = db.session.query(PlayerRegistration.id).filter_by(**filters)
    Payment.query.filter(
        Payment.team_registration_id.in_(team_registration_ids)
        | Payment.player_registration_id.in_(player_registration_ids)
    ).delete(synchronize_session=False)
    PaidEntry.query.filter_by(**filters).delete(synchronize_session=False)
