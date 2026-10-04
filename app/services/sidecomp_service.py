"""Side competition service.

Encapsulates side-competition CRUD, player self-registration, and TO-driven
registration. Mirrors the style of :class:`~app.services.registration_service.RegistrationService`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, List, Optional

from app.error_values import Err, Ok, Result, allow_Q
from app.exceptions import (
    ArctosError,
    NotFoundError,
    RegistrationClosedError,
    UnauthorizedError,
    ValidationError,
)

if TYPE_CHECKING:  # pragma: no cover
    from app.domain.enums import Pompfen, SideCompType
    from models import SideComp, SideCompRegistration, Tournament


def _parse_type(value: object) -> Optional["SideCompType"]:
    """Parse *value* into a :class:`~app.domain.enums.SideCompType` member.

    Returns ``None`` if *value* is not a valid side competition type.
    """
    from app.domain.enums import SideCompType

    if value is None:
        return None
    if isinstance(value, SideCompType):
        return value
    try:
        return SideCompType(str(value))
    except ValueError:
        return None


def _parse_weapon(value: object) -> Optional["Pompfen"]:
    """Parse *value* into a :class:`~app.domain.enums.Pompfen` member.

    Accepts a member instance, enum name (``\"CHAIN\"``), or integer value.
    Returns ``None`` if *value* is not a valid pompfen.
    """
    from app.domain.enums import Pompfen

    if value is None:
        return None
    if isinstance(value, Pompfen):
        return value
    by_name = Pompfen.from_name(value)
    if by_name is not None:
        return by_name
    return Pompfen.from_value(value)


def _parse_allowed_weapons(value: object) -> Optional[List["Pompfen"]]:
    """Parse a list of weapon names/values into :class:`Pompfen` members.

    Returns ``None`` if *value* is not a list or contains any invalid entry.
    An empty list is valid (no weapons enabled).
    """
    if value is None:
        return None
    if not isinstance(value, list):
        return None
    out: List["Pompfen"] = []
    seen: set["Pompfen"] = set()
    for item in value:
        parsed = _parse_weapon(item)
        if parsed is None:
            return None
        if parsed not in seen:
            out.append(parsed)
            seen.add(parsed)
    return out


def _team_display_short(shortname: object, team_name: str | None) -> str:
    """Match frontend ``short_or_truncate``: prefer shortname, else truncate name."""
    if isinstance(shortname, str):
        trimmed = shortname.strip()
        if trimmed:
            return trimmed
    full = (team_name or "").strip()
    if not full:
        return "merc"
    if len(full) <= 8:
        return full
    return f"{full[:7]}..."


def _resolve_team_short_label(
    *,
    team_id: str | None,
    shortname: object,
    team_name: str | None,
) -> str:
    """Return merc for unaffiliated players; otherwise shortname or auto-shortened team name."""
    if not team_id:
        return "merc"
    return _team_display_short(shortname, team_name)


@dataclass(frozen=True)
class SideCompService:
    """Side competition workflows. Static methods, namespace dataclass."""

    @staticmethod
    def _get_tournament(tournament_url: str) -> Result["Tournament", ArctosError]:
        from app.services._common import get_tournament_or_err

        return get_tournament_or_err(tournament_url)

    @staticmethod
    def _next_entry_number(comp_id: int) -> int:
        """Return the next 1-indexed ``entry_number`` for *comp_id*.

        Returns one more than the current max ``entry_number`` for the comp,
        or ``1`` if the comp has no registrations. Numbers are not reused
        when a registrant is removed.
        """
        from sqlalchemy import func

        from models import SideCompRegistration, db

        current_max = db.session.query(func.max(SideCompRegistration.entry_number)).filter_by(comp=comp_id).scalar()
        return (current_max or 0) + 1

    @staticmethod
    def _insert_registration_with_entry_number(
        *,
        comp_id: int,
        player_id: str,
        weapon: "Pompfen",
        registered_by_to: bool,
    ) -> "SideCompRegistration":
        """Insert a SideCompRegistration with a fresh entry_number.

        Retries once on IntegrityError to handle entry_number collision
        under concurrent inserts (uq_sidecomp_registrations_comp_entry_number).
        Caller is responsible for any pre-insert validation.
        """
        from sqlalchemy.exc import IntegrityError

        from models import SideCompRegistration, db

        last_exc: Exception | None = None
        for _ in range(2):
            entry_number = SideCompService._next_entry_number(comp_id)
            reg = SideCompRegistration(
                comp=comp_id,
                player=player_id,
                entry_number=entry_number,
                weapon=weapon.value,
                registered_by_to=registered_by_to,
            )
            db.session.add(reg)
            try:
                db.session.commit()
                return reg
            except IntegrityError as exc:
                db.session.rollback()
                last_exc = exc
                continue
        assert last_exc is not None
        raise last_exc

    @staticmethod
    def _require_to(tournament_url: str, actor_user_id: str, actor_user_type: str) -> Result[None, ArctosError]:
        from app.services._common import resolve_actor
        from app.services.permission_service import PermissionService

        actor = resolve_actor(actor_user_id, actor_user_type)
        if not PermissionService.is_tournament_organizer(tournament_url, actor):
            return Err(UnauthorizedError("Only tournament organizers can do that"))
        return Ok(None)

    @staticmethod
    def _confirmed_player_registration_for_tournament(tournament_url: str, player_id: str):
        from app.domain.enums import RegistrationStatus
        from app.services.registration_resolver import player_registrations_for_tournament
        from models import Tournament

        tournament = Tournament.query.get(tournament_url)
        if tournament is None:
            return None

        registrations = player_registrations_for_tournament(tournament, statuses=[RegistrationStatus.CONFIRMED])
        for registration in registrations:
            if registration.player == player_id:
                return registration
        return None

    @staticmethod
    def _validate_weapon_for_comp(sc: "SideComp", weapon: "Pompfen") -> Result[None, ArctosError]:
        allowed = sc.get_allowed_weapons()
        if not allowed:
            return Err(ValidationError("This side competition has no weapons enabled for registration"))
        if weapon not in allowed:
            return Err(ValidationError("Selected weapon is not enabled for this side competition"))
        return Ok(None)

    @staticmethod
    @allow_Q
    def create(
        tournament_url: str,
        *,
        actor_user_id: str,
        actor_user_type: str,
        name: str,
        type: str,
        description: Optional[str] = None,
        allowed_weapons: Optional[list] = None,
        only_show_top_n_results: Optional[int] = None,
    ) -> Result["SideComp", ArctosError]:
        """Create a new side competition for *tournament_url*.

        Args:
            tournament_url: URL slug of the parent tournament.
            actor_user_id: ID of the user attempting the create. Must be a TO.
            actor_user_type: ``"player"`` or ``"team"``.
            name: Display name of the side competition. Must be non-blank.
            type: One of the :class:`~app.domain.enums.SideCompType` values.
            description: Optional free-form description. Empty/whitespace
                strings are treated as ``None``.
            allowed_weapons: Optional list of :class:`~app.domain.enums.Pompfen`
                names. Defaults to all pompfen options when omitted.
            only_show_top_n_results: Optional top-N standings cut. ``None``
                means show all; ``0`` hides the results page.

        Returns:
            :class:`~app.error_values.Ok` wrapping the persisted
            :class:`~app.models.sidecomp.SideComp`, or an :class:`~app.error_values.Err`
            describing the failure (tournament not found, actor not a TO,
            invalid name, or invalid type).
        """
        from app.domain.enums import Pompfen
        from models import SideComp, db

        SideCompService._get_tournament(tournament_url).Q()
        SideCompService._require_to(tournament_url, actor_user_id, actor_user_type).Q()

        name_value = (name or "").strip()
        if not name_value:
            return Err(ValidationError("Side competition name is required"))

        parsed_type = _parse_type(type)
        if parsed_type is None:
            return Err(ValidationError("Invalid side competition type"))

        description_value: Optional[str] = None
        if description is not None:
            stripped = description.strip()
            description_value = stripped if stripped else None

        if allowed_weapons is None:
            parsed_weapons = list(Pompfen)
        else:
            parsed_weapons = _parse_allowed_weapons(allowed_weapons)
            if parsed_weapons is None:
                return Err(ValidationError("Invalid allowed_weapons"))

        top_n_res = SideCompService._parse_top_n(only_show_top_n_results)
        if top_n_res.is_err():
            return top_n_res
        top_n_value = top_n_res.unwrap()

        sc = SideComp(
            event=tournament_url,
            name=name_value,
            type=parsed_type,
            description=description_value,
            only_show_top_n_results=top_n_value,
        )
        sc.set_allowed_weapons(parsed_weapons)
        db.session.add(sc)
        db.session.commit()
        return Ok(sc)

    @staticmethod
    def list_for_event(tournament_url: str):
        """Return all side competitions for *tournament_url*, oldest first.

        Args:
            tournament_url: URL slug of the parent tournament.

        Returns:
            List of :class:`~app.models.sidecomp.SideComp` rows ordered by
            ``created_at`` ascending. Empty list if there are none.
        """
        from models import SideComp

        return SideComp.query.filter_by(event=tournament_url).order_by(SideComp.created_at.asc()).all()

    @staticmethod
    def get_with_registrants(comp_id: int) -> Result[tuple, ArctosError]:
        """Return a side competition and its registrants by *comp_id*.

        Args:
            comp_id: Primary key of the :class:`~app.models.sidecomp.SideComp`.

        Returns:
            :class:`~app.error_values.Ok` wrapping
            ``(SideComp, list[(SideCompRegistration, Player)])`` ordered by
            registration time, or :class:`~app.error_values.Err` with a
            :class:`~app.exceptions.NotFoundError` if the comp does not exist.
        """
        from models import Player, SideComp, SideCompRegistration

        sc = SideComp.query.get(comp_id)
        if sc is None:
            return Err(NotFoundError("Side competition not found"))

        rows = (
            SideCompRegistration.query.filter_by(comp=comp_id).order_by(SideCompRegistration.registered_at.asc()).all()
        )
        player_ids = [r.player for r in rows]
        players_by_id = {p.id: p for p in Player.query.filter(Player.id.in_(player_ids)).all()} if player_ids else {}
        registrants = [(r, players_by_id.get(r.player)) for r in rows]
        return Ok((sc, registrants))

    @staticmethod
    @allow_Q
    def update(
        comp_id: int,
        *,
        actor_user_id: str,
        actor_user_type: str,
        name: Optional[str] = None,
        type: Optional[str] = None,
        description: Optional[str] = None,
        registration_open: Optional[bool] = None,
        active: Optional[bool] = None,
        allowed_weapons: Optional[list] = None,
        only_show_top_n_results: object = ...,
    ) -> Result["SideComp", ArctosError]:
        """Update fields of an existing side competition.

        When ``allowed_weapons`` is provided, any registrants whose selected
        weapon is no longer enabled are deregistered.

        Args:
            comp_id: Primary key of the :class:`~app.models.sidecomp.SideComp`.
            actor_user_id: ID of the user attempting the update. Must be a TO.
            actor_user_type: ``"player"`` or ``"team"``.
            name: New display name. If ``None``, the field is left untouched.
                Must be non-blank when provided.
            type: New :class:`~app.domain.enums.SideCompType` value. If ``None``,
                the field is left untouched.
            description: New description. If ``None``, the field is left
                untouched. An empty/whitespace string clears it to ``None``.
            registration_open: New value for the registration-open gate. If
                ``None``, the field is left untouched.
            active: New value for the results-entry gate. If ``None``, the
                field is left untouched.
            allowed_weapons: New list of enabled :class:`~app.domain.enums.Pompfen`
                names. If ``None``, the field is left untouched.
            only_show_top_n_results: Sentinel ``...`` leaves the field untouched.
                Explicit ``None`` clears it (show all). ``0`` hides results.
                Positive ints keep the top N.

        Returns:
            :class:`~app.error_values.Ok` wrapping the updated
            :class:`~app.models.sidecomp.SideComp`, or an
            :class:`~app.error_values.Err` describing the failure (comp not
            found, actor not a TO, invalid name, or invalid type).
        """
        from models import SideComp, SideCompRegistration, db

        sc = SideComp.query.get(comp_id)
        if sc is None:
            return Err(NotFoundError("Side competition not found"))

        SideCompService._require_to(sc.event, actor_user_id, actor_user_type).Q()

        if name is not None:
            name_value = name.strip()
            if not name_value:
                return Err(ValidationError("Side competition name is required"))
            sc.name = name_value

        if type is not None:
            parsed_type = _parse_type(type)
            if parsed_type is None:
                return Err(ValidationError("Invalid side competition type"))
            sc.type = parsed_type

        if description is not None:
            stripped = description.strip()
            sc.description = stripped if stripped else None

        if registration_open is not None:
            sc.registration_open = bool(registration_open)

        if active is not None:
            sc.active = bool(active)

        if allowed_weapons is not None:
            parsed_weapons = _parse_allowed_weapons(allowed_weapons)
            if parsed_weapons is None:
                return Err(ValidationError("Invalid allowed_weapons"))
            allowed_values = {w.value for w in parsed_weapons}
            if not allowed_values:
                SideCompRegistration.query.filter_by(comp=comp_id).delete(synchronize_session=False)
            else:
                SideCompRegistration.query.filter(
                    SideCompRegistration.comp == comp_id,
                    ~SideCompRegistration.weapon.in_(allowed_values),
                ).delete(synchronize_session=False)
            sc.set_allowed_weapons(parsed_weapons)

        if only_show_top_n_results is not ...:
            top_n_res = SideCompService._parse_top_n(only_show_top_n_results)
            if top_n_res.is_err():
                return top_n_res
            sc.only_show_top_n_results = top_n_res.unwrap()

        db.session.commit()
        return Ok(sc)

    @staticmethod
    @allow_Q
    def delete(
        comp_id: int,
        *,
        actor_user_id: str,
        actor_user_type: str,
    ) -> Result[None, ArctosError]:
        """Delete a side competition along with its registrations and results.

        Args:
            comp_id: Primary key of the :class:`~app.models.sidecomp.SideComp`.
            actor_user_id: ID of the user attempting the delete. Must be a TO.
            actor_user_type: ``"player"`` or ``"team"``.

        Returns:
            :class:`~app.error_values.Ok` wrapping ``None`` on success, or an
            :class:`~app.error_values.Err` describing the failure (comp not
            found or actor not a TO).
        """
        from models import SideComp, SideCompRegistration, SideCompResult, db

        sc = SideComp.query.get(comp_id)
        if sc is None:
            return Err(NotFoundError("Side competition not found"))

        SideCompService._require_to(sc.event, actor_user_id, actor_user_type).Q()

        SideCompRegistration.query.filter_by(comp=comp_id).delete(synchronize_session=False)
        SideCompResult.query.filter_by(comp=comp_id).delete(synchronize_session=False)
        db.session.delete(sc)
        db.session.commit()
        return Ok(None)

    @staticmethod
    @allow_Q
    def register_player(
        comp_id: int,
        *,
        player_id: str,
        weapon: object,
    ) -> Result["SideCompRegistration", ArctosError]:
        """Register *player_id* for side competition *comp_id* (self-registration).

        Args:
            comp_id: Primary key of the :class:`~app.models.sidecomp.SideComp`.
            player_id: ID of the player registering themselves.
            weapon: Selected :class:`~app.domain.enums.Pompfen` name or value.

        Returns:
            :class:`~app.error_values.Ok` wrapping the persisted
            :class:`~app.models.sidecomp.SideCompRegistration`, or an
            :class:`~app.error_values.Err` describing the failure (comp not
            found, player not registered for the parent event, or duplicate
            registration).
        """
        from models import (
            SideComp,
            SideCompRegistration,
        )

        sc = SideComp.query.get(comp_id)
        if sc is None:
            return Err(NotFoundError("Side competition not found"))

        if not sc.registration_open:
            return Err(RegistrationClosedError("This side competition is not open for registration"))

        event_reg = SideCompService._confirmed_player_registration_for_tournament(sc.event, player_id)
        if not event_reg:
            return Err(ValidationError("You must be registered for the event before joining a side competition"))

        existing = SideCompRegistration.query.filter_by(comp=comp_id, player=player_id).first()
        if existing:
            return Err(ValidationError("You are already registered for this side competition"))

        parsed_weapon = _parse_weapon(weapon)
        if parsed_weapon is None:
            return Err(ValidationError("A valid weapon selection is required"))
        SideCompService._validate_weapon_for_comp(sc, parsed_weapon).Q()

        reg = SideCompService._insert_registration_with_entry_number(
            comp_id=comp_id,
            player_id=player_id,
            weapon=parsed_weapon,
            registered_by_to=False,
        )
        return Ok(reg)

    @staticmethod
    @allow_Q
    def register_player_as_to(
        comp_id: int,
        *,
        actor_user_id: str,
        actor_user_type: str,
        player_id: str,
        weapon: object,
    ) -> Result["SideCompRegistration", ArctosError]:
        """Register *player_id* for side competition *comp_id* via TO-driven registration.

        The resulting :class:`~app.models.sidecomp.SideCompRegistration` row has
        ``registered_by_to=True`` so it is distinguishable from a player's
        self-registration.

        Args:
            comp_id: Primary key of the :class:`~app.models.sidecomp.SideComp`.
            actor_user_id: ID of the TO registering on behalf of the player.
                Must be a TO of the parent event.
            actor_user_type: ``"player"`` or ``"team"``.
            player_id: ID of the player being registered on their behalf.
            weapon: Selected :class:`~app.domain.enums.Pompfen` name or value.

        Returns:
            :class:`~app.error_values.Ok` wrapping the persisted
            :class:`~app.models.sidecomp.SideCompRegistration`, or an
            :class:`~app.error_values.Err` describing the failure (comp not
            found, actor not a TO, target player not found, target not
            registered for the parent event, or duplicate registration).
        """
        from models import (
            Player,
            SideComp,
            SideCompRegistration,
        )

        sc = SideComp.query.get(comp_id)
        if sc is None:
            return Err(NotFoundError("Side competition not found"))

        SideCompService._require_to(sc.event, actor_user_id, actor_user_type).Q()

        target = Player.query.get(player_id)
        if target is None:
            return Err(ValidationError("Player not found"))

        event_reg = SideCompService._confirmed_player_registration_for_tournament(sc.event, player_id)
        if not event_reg:
            return Err(ValidationError("Player is not registered for this event"))

        existing = SideCompRegistration.query.filter_by(comp=comp_id, player=player_id).first()
        if existing:
            return Err(ValidationError("Player is already registered for this side competition"))

        parsed_weapon = _parse_weapon(weapon)
        if parsed_weapon is None:
            return Err(ValidationError("A valid weapon selection is required"))
        SideCompService._validate_weapon_for_comp(sc, parsed_weapon).Q()

        reg = SideCompService._insert_registration_with_entry_number(
            comp_id=comp_id,
            player_id=player_id,
            weapon=parsed_weapon,
            registered_by_to=True,
        )
        return Ok(reg)

    @staticmethod
    @allow_Q
    def update_player_weapon(
        comp_id: int,
        *,
        player_id: str,
        weapon: object,
    ) -> Result["SideCompRegistration", ArctosError]:
        """Update *player_id*'s selected weapon for side competition *comp_id*."""
        from models import SideComp, SideCompRegistration, db

        sc = SideComp.query.get(comp_id)
        if sc is None:
            return Err(NotFoundError("Side competition not found"))

        reg = SideCompRegistration.query.filter_by(comp=comp_id, player=player_id).first()
        if reg is None:
            return Err(NotFoundError("Registration not found"))

        parsed_weapon = _parse_weapon(weapon)
        if parsed_weapon is None:
            return Err(ValidationError("A valid weapon selection is required"))
        SideCompService._validate_weapon_for_comp(sc, parsed_weapon).Q()

        reg.weapon = parsed_weapon.value
        db.session.commit()
        return Ok(reg)

    @staticmethod
    @allow_Q
    def update_player_weapon_as_to(
        comp_id: int,
        *,
        actor_user_id: str,
        actor_user_type: str,
        player_id: str,
        weapon: object,
    ) -> Result["SideCompRegistration", ArctosError]:
        """TO-driven update of *player_id*'s selected weapon."""
        from models import SideComp, SideCompRegistration, db

        sc = SideComp.query.get(comp_id)
        if sc is None:
            return Err(NotFoundError("Side competition not found"))

        SideCompService._require_to(sc.event, actor_user_id, actor_user_type).Q()

        reg = SideCompRegistration.query.filter_by(comp=comp_id, player=player_id).first()
        if reg is None:
            return Err(NotFoundError("Registration not found"))

        parsed_weapon = _parse_weapon(weapon)
        if parsed_weapon is None:
            return Err(ValidationError("A valid weapon selection is required"))
        SideCompService._validate_weapon_for_comp(sc, parsed_weapon).Q()

        reg.weapon = parsed_weapon.value
        db.session.commit()
        return Ok(reg)

    @staticmethod
    @allow_Q
    def deregister_player_as_to(
        comp_id: int,
        *,
        actor_user_id: str,
        actor_user_type: str,
        player_id: str,
    ) -> Result[None, ArctosError]:
        """Deregister *player_id* from side competition *comp_id* via TO-driven registration.

        Idempotent: removing a row that doesn't exist returns
        :class:`~app.error_values.Ok`.

        Args:
            comp_id: Primary key of the :class:`~app.models.sidecomp.SideComp`.
            actor_user_id: ID of the TO deregistering on behalf of the player.
                Must be a TO of the parent event.
            actor_user_type: ``"player"`` or ``"team"``.
            player_id: ID of the player being deregistered on their behalf.

        Returns:
            :class:`~app.error_values.Ok` wrapping ``None`` on success, or an
            :class:`~app.error_values.Err` describing the failure (comp not
            found or actor not a TO).
        """
        from models import SideComp, SideCompRegistration, db

        sc = SideComp.query.get(comp_id)
        if sc is None:
            return Err(NotFoundError("Side competition not found"))

        SideCompService._require_to(sc.event, actor_user_id, actor_user_type).Q()

        SideCompRegistration.query.filter_by(comp=comp_id, player=player_id).delete(synchronize_session=False)
        db.session.commit()
        return Ok(None)

    @staticmethod
    def cancel_player_registrations_in_event(event: str, player: str) -> None:
        """Hard-delete all SideCompRegistration rows for ``(event, player)``.

        Called by RegistrationService when a player's event registration is
        cancelled. No Result wrapper - the caller is already inside a
        transaction.
        """
        from models import SideComp, SideCompRegistration

        comp_ids = [c.id for c in SideComp.query.filter_by(event=event).all()]
        if not comp_ids:
            return
        SideCompRegistration.query.filter(
            SideCompRegistration.comp.in_(comp_ids),
            SideCompRegistration.player == player,
        ).delete(synchronize_session=False)

    @staticmethod
    def cancel_players_in_event(event: str, player_ids: list[str]) -> int:
        """Bulk-cancel side-comp registrations for *player_ids* in *event*.

        Issues at most two SQL statements: one to enumerate side-comp IDs for
        the event, and one ``DELETE WHERE comp IN (...) AND player IN (...)``.
        Use this in place of looping over :meth:`cancel_player_registrations_in_event`
        when cancelling many players at once.

        No transaction commit - the caller is responsible for committing or
        rolling back as part of its own transaction.

        Args:
            event: URL slug of the parent tournament.
            player_ids: List of player IDs to cancel.

        Returns:
            Number of deleted rows.
        """
        from models import SideComp, SideCompRegistration

        if not player_ids:
            return 0
        comp_ids = [c.id for c in SideComp.query.filter_by(event=event).all()]
        if not comp_ids:
            return 0
        return (
            SideCompRegistration.query.filter(SideCompRegistration.comp.in_(comp_ids))
            .filter(SideCompRegistration.player.in_(player_ids))
            .delete(synchronize_session=False)
        )

    @staticmethod
    @allow_Q
    def deregister_player(
        comp_id: int,
        *,
        player_id: str,
    ) -> Result[None, ArctosError]:
        """Remove *player_id*'s registration for side competition *comp_id*.

        Idempotent: removing a row that doesn't exist returns
        :class:`~app.error_values.Ok`.

        Args:
            comp_id: Primary key of the :class:`~app.models.sidecomp.SideComp`.
            player_id: ID of the player deregistering themselves.

        Returns:
            :class:`~app.error_values.Ok` wrapping ``None`` on success, or an
            :class:`~app.error_values.Err` with a
            :class:`~app.exceptions.NotFoundError` if the comp does not exist.
        """
        from models import SideComp, SideCompRegistration, db

        sc = SideComp.query.get(comp_id)
        if sc is None:
            return Err(NotFoundError("Side competition not found"))

        SideCompRegistration.query.filter_by(comp=comp_id, player=player_id).delete(synchronize_session=False)
        db.session.commit()
        return Ok(None)

    @staticmethod
    def _parse_top_n(value: object) -> Result[Optional[int], ArctosError]:
        """Parse ``only_show_top_n_results``.

        ``None`` means show all. Non-negative integers are accepted. Empty
        strings are treated as ``None``.
        """
        if value is None or value == "":
            return Ok(None)
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return Err(ValidationError("only_show_top_n_results must be a non-negative integer or null"))
        if parsed < 0:
            return Err(ValidationError("only_show_top_n_results must be a non-negative integer or null"))
        return Ok(parsed)

    @staticmethod
    def enter_results_roster(comp_id: int) -> Result[dict, ArctosError]:
        """Return the cached roster payload used by the enter-results UI."""
        from app.domain.enums import RegistrationStatus
        from app.services.registration_resolver import (
            player_registrations_for_tournament,
            team_registrations_for_tournament,
        )
        from models import Player, SideComp, SideCompRegistration, Team, Tournament

        sc = SideComp.query.get(comp_id)
        if sc is None:
            return Err(NotFoundError("Side competition not found"))

        tournament = Tournament.query.get(sc.event)
        if tournament is None:
            return Err(NotFoundError("Tournament not found"))

        regs = (
            SideCompRegistration.query.filter_by(comp=comp_id).order_by(SideCompRegistration.entry_number.asc()).all()
        )
        if not regs:
            return Ok(
                {
                    "id": sc.id,
                    "name": sc.name,
                    "type": str(sc.type),
                    "active": bool(sc.active),
                    "registrants": [],
                }
            )

        player_ids = [r.player for r in regs]
        players_by_id = {p.id: p for p in Player.query.filter(Player.id.in_(player_ids)).all()}

        event_regs = {
            er.player: er
            for er in player_registrations_for_tournament(tournament, statuses=[RegistrationStatus.CONFIRMED])
            if er.player in players_by_id
        }
        team_ids = {er.team for er in event_regs.values() if er.team}
        teams_by_id = {t.id: t for t in Team.query.filter(Team.id.in_(team_ids)).all()} if team_ids else {}
        team_shortnames = {}
        team_pseudonyms = {}
        if team_ids:
            for tr in team_registrations_for_tournament(tournament):
                if tr.team in team_ids:
                    team_shortnames[tr.team] = tr.shortname
                    team_pseudonyms[tr.team] = tr.pseudonym

        registrants = []
        for reg in regs:
            player = players_by_id.get(reg.player)
            event_reg = event_regs.get(reg.player)
            team_id = event_reg.team if event_reg else None
            team = teams_by_id.get(team_id) if team_id else None
            display_team_name = None
            if team_id:
                display_team_name = team_pseudonyms.get(team_id) or (team.name if team else None)
            shortname = _resolve_team_short_label(
                team_id=team_id,
                shortname=team_shortnames.get(team_id) if team_id else None,
                team_name=display_team_name,
            )
            registrants.append(
                {
                    "registration_id": reg.id,
                    "entry_number": reg.entry_number,
                    "weapon": reg.weapon_name(),
                    "player_id": reg.player,
                    "display_name": player.name if player else reg.player,
                    "profile_photo": player.profile_photo if player else None,
                    "jersey_name": event_reg.jersey_name if event_reg else None,
                    "jersey_number": event_reg.jersey_number if event_reg else None,
                    "team_id": team_id,
                    "team_shortname": shortname,
                    "team_profile_photo": team.profile_photo if team else None,
                }
            )

        return Ok(
            {
                "id": sc.id,
                "name": sc.name,
                "type": str(sc.type),
                "active": bool(sc.active),
                "registrants": registrants,
            }
        )

    @staticmethod
    @allow_Q
    def log_result(
        comp_id: int,
        *,
        registration_id: int,
        points: int,
        ref_user_id: str,
    ) -> Result["SideCompResult", ArctosError]:
        """Persist a ``+1`` / ``-1`` point for *registration_id*."""
        from models import SideComp, SideCompRegistration, SideCompResult, db

        if points not in (1, -1):
            return Err(ValidationError("points must be +1 or -1"))

        sc = SideComp.query.get(comp_id)
        if sc is None:
            return Err(NotFoundError("Side competition not found"))

        if not sc.active:
            return Err(ValidationError("This side competition is not active; results cannot be entered"))

        reg = SideCompRegistration.query.filter_by(id=registration_id, comp=comp_id).first()
        if reg is None:
            return Err(NotFoundError("Registration not found"))

        result = SideCompResult(
            comp=comp_id,
            player=reg.id,
            points=points,
            ref=ref_user_id,
            flagged=False,
            valid=True,
        )
        db.session.add(result)
        db.session.commit()
        return Ok(result)

    @staticmethod
    @allow_Q
    def set_result_flagged(
        result_uuid: str,
        *,
        flagged: bool,
        actor_user_id: str,
    ) -> Result["SideCompResult", ArctosError]:
        """Toggle the review flag on a side-comp result."""
        from models import SideCompResult, db

        _ = actor_user_id
        result = SideCompResult.query.get(result_uuid)
        if result is None:
            return Err(NotFoundError("Result not found"))

        result.flagged = bool(flagged)
        db.session.commit()
        return Ok(result)

    @staticmethod
    def standings(comp_id: int) -> Result[dict, ArctosError]:
        """Compute public standings tables for *comp_id*.

        Rankings are computed per table over all valid points, then an optional
        top-N cut is applied. Weapon filtering is left to the client so ranks
        remain global within each table.
        """
        from sqlalchemy import func

        from app.domain.enums import Pompfen, RegistrationStatus, SideCompType
        from app.services.registration_resolver import (
            player_registrations_for_tournament,
            team_registrations_for_tournament,
        )
        from models import Player, SideComp, SideCompRegistration, SideCompResult, Team, Tournament, db

        sc = SideComp.query.get(comp_id)
        if sc is None:
            return Err(NotFoundError("Side competition not found"))

        tournament = Tournament.query.get(sc.event)
        regs = SideCompRegistration.query.filter_by(comp=comp_id).all()

        score_rows = (
            db.session.query(
                SideCompResult.player,
                func.coalesce(func.sum(SideCompResult.points), 0),
            )
            .filter(
                SideCompResult.comp == comp_id,
                SideCompResult.valid.is_(True),
            )
            .group_by(SideCompResult.player)
            .all()
        )
        wins_by_reg = {reg_id: int(total or 0) for reg_id, total in score_rows}

        player_ids = [r.player for r in regs]
        players_by_id = {p.id: p for p in Player.query.filter(Player.id.in_(player_ids)).all()} if player_ids else {}
        event_regs = {}
        team_shortnames = {}
        team_pseudonyms = {}
        teams_by_id = {}
        if tournament is not None and player_ids:
            for er in player_registrations_for_tournament(tournament, statuses=[RegistrationStatus.CONFIRMED]):
                if er.player in players_by_id:
                    event_regs[er.player] = er
            team_ids = {er.team for er in event_regs.values() if er.team}
            if team_ids:
                teams_by_id = {t.id: t for t in Team.query.filter(Team.id.in_(team_ids)).all()}
                for tr in team_registrations_for_tournament(tournament):
                    if tr.team in team_ids:
                        team_shortnames[tr.team] = tr.shortname
                        team_pseudonyms[tr.team] = tr.pseudonym

        def row_for(reg: SideCompRegistration, rank: int, wins: int) -> dict:
            player = players_by_id.get(reg.player)
            event_reg = event_regs.get(reg.player)
            team_id = event_reg.team if event_reg else None
            team = teams_by_id.get(team_id) if team_id else None
            display_team_name = None
            if team_id:
                display_team_name = team_pseudonyms.get(team_id) or (team.name if team else None)
            shortname = _resolve_team_short_label(
                team_id=team_id,
                shortname=team_shortnames.get(team_id) if team_id else None,
                team_name=display_team_name,
            )
            return {
                "registration_id": reg.id,
                "entry_number": reg.entry_number,
                "weapon": reg.weapon_name(),
                "player_id": reg.player,
                "display_name": player.name if player else reg.player,
                "profile_photo": player.profile_photo if player else None,
                "jersey_name": event_reg.jersey_name if event_reg else None,
                "jersey_number": event_reg.jersey_number if event_reg else None,
                "team_shortname": shortname,
                "team_profile_photo": teams_by_id[team_id].profile_photo
                if team_id and team_id in teams_by_id
                else None,
                "wins": wins,
                "rank": rank,
            }

        def build_table(table_id: str, title: str, members: list) -> dict:
            scored = [(reg, wins_by_reg.get(reg.id, 0)) for reg in members]
            scored.sort(key=lambda item: (-item[1], item[0].entry_number))
            ranked_rows = []
            for idx, (reg, wins) in enumerate(scored, start=1):
                ranked_rows.append(row_for(reg, idx, wins))
            top_n = sc.only_show_top_n_results
            if top_n is not None and top_n > 0:
                ranked_rows = ranked_rows[:top_n]
            return {"id": table_id, "title": title, "rows": ranked_rows}

        if sc.type == SideCompType.CHAIN_BREAKING:
            chains = [r for r in regs if r.weapon == Pompfen.CHAIN.value]
            breaks = [r for r in regs if r.weapon != Pompfen.CHAIN.value]
            tables = [
                build_table("chains", "Chains", chains),
                build_table("breaks", "Breaks", breaks),
            ]
        else:
            tables = [build_table("all", "Standings", regs)]

        return Ok(
            {
                "id": sc.id,
                "name": sc.name,
                "type": str(sc.type),
                "only_show_top_n_results": sc.only_show_top_n_results,
                "results_page_enabled": sc.results_page_enabled(),
                "tables": tables,
            }
        )
