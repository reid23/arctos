"""Side competition service.

Encapsulates side-competition CRUD, player self-registration, and TO-driven
registration. Mirrors the style of :class:`~app.services.registration_service.RegistrationService`.
"""

from __future__ import annotations

from sqlalchemy.exc import IntegrityError

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


def _side_comp_team_id(event_reg) -> str | None:
    """Return the team a player represents in side comps.

    Players pending team approval are eligible for side comps but count as
    unattached until the team confirms them.
    """
    from app.domain.enums import RegistrationStatus

    if event_reg is None or event_reg.status != RegistrationStatus.CONFIRMED:
        return None
    return event_reg.team


@dataclass(frozen=True)
class SideCompService:
    """Side competition workflows. Static methods, namespace dataclass."""

    @staticmethod
    def _get_tournament(tournament_url: str) -> Result["Tournament", ArctosError]:
        from app.services._common import get_tournament_or_err

        return get_tournament_or_err(tournament_url)

    @staticmethod
    def _entry_number_scope(sc: "SideComp") -> str:
        """Return the entry-number scope (league, else tournament) for *sc*."""
        from models import SideCompEntryNumber, Tournament

        tournament = Tournament.query.get(sc.event)
        return SideCompEntryNumber.scope_for_tournament(sc.event, tournament.league_id if tournament else None)

    @staticmethod
    def _next_entry_number(scope: str) -> int:
        """Return one more than the highest entry number assigned in *scope*, or ``1``."""
        from sqlalchemy import func

        from models import SideCompEntryNumber, db

        current_max = db.session.query(func.max(SideCompEntryNumber.number)).filter_by(scope=scope).scalar()
        return (current_max or 0) + 1

    @staticmethod
    def _entry_number_for_player(sc: "SideComp", player_id: str) -> int:
        """Return *player_id*'s entry number in *sc*'s scope, assigning one if needed.

        A new assignment is committed immediately. Retries once on
        IntegrityError to handle a concurrent assignment of the same number
        (uq_sidecomp_entry_numbers_scope_number) or to the same player.
        """
        from sqlalchemy.exc import IntegrityError

        from models import SideCompEntryNumber, db

        scope = SideCompService._entry_number_scope(sc)
        last_exc: Exception | None = None
        for _ in range(2):
            existing = SideCompEntryNumber.query.filter_by(scope=scope, player=player_id).first()
            if existing is not None:
                return existing.number
            assigned = SideCompEntryNumber(
                scope=scope,
                player=player_id,
                number=SideCompService._next_entry_number(scope),
            )
            db.session.add(assigned)
            try:
                db.session.commit()
                return assigned.number
            except IntegrityError as exc:
                db.session.rollback()
                last_exc = exc
        assert last_exc is not None
        raise last_exc

    @staticmethod
    def _add_registration(
        sc: "SideComp",
        *,
        player_id: str,
        weapon: Optional["Pompfen"],
        registered_by_to: bool,
    ) -> "SideCompRegistration":
        """Add (without committing) a registration carrying the player's scoped entry number.

        Pass ``weapon=None`` for an incomplete registration. Caller is
        responsible for any pre-insert validation.
        """
        from models import SideCompRegistration, db

        reg = SideCompRegistration(
            comp=sc.id,
            player=player_id,
            entry_number=SideCompService._entry_number_for_player(sc, player_id),
            weapon=weapon.value if weapon is not None else None,
            registered_by_to=registered_by_to,
        )
        db.session.add(reg)
        return reg

    @staticmethod
    def _require_to(tournament_url: str, actor_user_id: str, actor_user_type: str) -> Result[None, ArctosError]:
        from app.services._common import resolve_actor
        from app.services.permission_service import PermissionService

        actor = resolve_actor(actor_user_id, actor_user_type)
        if not PermissionService.is_tournament_organizer(tournament_url, actor):
            return Err(UnauthorizedError("Only tournament organizers can do that"))
        return Ok(None)

    @staticmethod
    def _eligible_player_registration_for_tournament(tournament_url: str, player_id: str):
        """Return *player_id*'s confirmed or pending-team-approval event registration, if any."""
        from app.services.registration_resolver import player_registrations_for_tournament
        from models import Tournament

        tournament = Tournament.query.get(tournament_url)
        if tournament is None:
            return None

        registrations = player_registrations_for_tournament(tournament)
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
        schedule: Optional[dict] = None,
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
            only_show_top_n_results: Optional public standings limit.
                ``None`` means show all; ``0`` hides results. A selected
                weapon shows its leader when public results are limited.
            schedule: Optional start/end times, field, and location. The
                competition and its schedule entry are saved together.

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

        schedule_values = None
        if schedule is not None:
            from app.services.schedule_item_service import validate_schedule_item, schedule_item_matches

            try:
                schedule_values = validate_schedule_item(tournament_url, schedule, linked=True)
            except ValueError as error:
                return Err(ValidationError(str(error)))
            from models import ScheduleItem

            request_id = schedule_values.get("request_id")
            existing_item = (
                ScheduleItem.query.filter_by(event=tournament_url, request_id=request_id).first()
                if request_id
                else None
            )

            def creation_replay(item):
                comp = item.sidecomp
                if not (
                    comp is not None
                    and schedule_item_matches(item, schedule_values, new_sidecomp=True)
                    and comp.name == name_value
                    and comp.type == parsed_type
                    and comp.description == description_value
                    and comp.get_allowed_weapons() == parsed_weapons
                    and comp.only_show_top_n_results == top_n_value
                ):
                    return Err(
                        ValidationError(
                            "This activity was already saved. Close this form and edit it from the schedule.",
                            status_code=409,
                        )
                    )
                return Ok(comp)

            if existing_item:
                return creation_replay(existing_item)

        sc = SideComp(
            event=tournament_url,
            name=name_value,
            type=parsed_type,
            description=description_value,
            only_show_top_n_results=top_n_value,
        )
        sc.set_allowed_weapons(parsed_weapons)
        db.session.add(sc)
        if schedule_values is not None:
            from models import ScheduleItem

            db.session.flush()
            schedule_values["sidecomp_id"] = sc.id
            db.session.add(ScheduleItem(event=tournament_url, created_sidecomp=True, **schedule_values))
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            request_id = schedule_values.get("request_id") if schedule_values else None
            existing_item = (
                ScheduleItem.query.filter_by(event=tournament_url, request_id=request_id).first()
                if request_id
                else None
            )
            if existing_item:
                return creation_replay(existing_item)
            raise
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
    def list_with_registrant_counts(tournament_url: str) -> list[tuple]:
        """Return ``(SideComp, registrant count)`` for *tournament_url*, oldest first.

        Incomplete registrations are not counted.
        """
        from sqlalchemy import func

        from models import SideComp, SideCompRegistration, db

        return (
            db.session.query(SideComp, func.count(SideCompRegistration.id))
            .outerjoin(
                SideCompRegistration,
                (SideCompRegistration.comp == SideComp.id) & SideCompRegistration.weapon.isnot(None),
            )
            .filter(SideComp.event == tournament_url)
            .group_by(SideComp.id)
            .order_by(SideComp.created_at.asc())
            .all()
        )

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
        track_losses: Optional[bool] = None,
        track_chain_losses: Optional[bool] = None,
        track_break_losses: Optional[bool] = None,
        track_matchups: Optional[bool] = None,
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
            track_losses: New value for loss tracking (non chain/breaking).
                If ``None``, the field is left untouched.
            track_chain_losses: New value for loss tracking of chains in
                chain/breaking. If ``None``, the field is left untouched.
            track_break_losses: New value for loss tracking of breakers in
                chain/breaking. If ``None``, the field is left untouched.
            track_matchups: New value for matchup scoring. Requires loss
                tracking. If ``None``, the field is left untouched. Turning
                loss tracking off clears it.
            allowed_weapons: New list of enabled :class:`~app.domain.enums.Pompfen`
                names. If ``None``, the field is left untouched.
            only_show_top_n_results: Sentinel ``...`` leaves the field untouched.
                Explicit ``None`` clears it (show all). ``0`` hides results.
                Positive ints limit public tables to the top N; a selected
                weapon shows its leader.

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
            want_active = bool(active)
            if want_active and sc.finalized:
                return Err(ValidationError("This side competition has been finalized and cannot be reactivated"))
            sc.active = want_active

        if track_losses is not None:
            sc.track_losses = bool(track_losses)
        if track_chain_losses is not None:
            sc.track_chain_losses = bool(track_chain_losses)
        if track_break_losses is not None:
            sc.track_break_losses = bool(track_break_losses)
        if not sc.loss_tracking_enabled():
            sc.track_matchups = False
        if track_matchups is not None:
            want_matchups = bool(track_matchups)
            if want_matchups and not sc.loss_tracking_enabled():
                return Err(ValidationError("Track matchups requires tracking losses"))
            sc.track_matchups = want_matchups

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

        from models import ScheduleItem

        ScheduleItem.query.filter_by(sidecomp_id=comp_id).delete(synchronize_session=False)
        SideCompResult.query.filter_by(comp=comp_id).delete(synchronize_session=False)
        SideCompRegistration.query.filter_by(comp=comp_id).delete(synchronize_session=False)
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
            db,
        )

        sc = SideComp.query.get(comp_id)
        if sc is None:
            return Err(NotFoundError("Side competition not found"))

        if not sc.registration_open:
            return Err(RegistrationClosedError("This side competition is not open for registration"))

        event_reg = SideCompService._eligible_player_registration_for_tournament(sc.event, player_id)
        if not event_reg:
            return Err(ValidationError("You must be registered for the event before joining a side competition"))

        existing = SideCompRegistration.query.filter_by(comp=comp_id, player=player_id).first()
        if existing is not None and existing.is_complete():
            return Err(ValidationError("You are already registered for this side competition"))

        parsed_weapon = _parse_weapon(weapon)
        if parsed_weapon is None:
            return Err(ValidationError("A valid weapon selection is required"))
        SideCompService._validate_weapon_for_comp(sc, parsed_weapon).Q()

        if existing is not None:
            existing.weapon = parsed_weapon.value
            reg = existing
        else:
            reg = SideCompService._add_registration(
                sc,
                player_id=player_id,
                weapon=parsed_weapon,
                registered_by_to=False,
            )
        db.session.commit()
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
            db,
        )

        sc = SideComp.query.get(comp_id)
        if sc is None:
            return Err(NotFoundError("Side competition not found"))

        SideCompService._require_to(sc.event, actor_user_id, actor_user_type).Q()

        target = Player.query.get(player_id)
        if target is None:
            return Err(ValidationError("Player not found"))

        event_reg = SideCompService._eligible_player_registration_for_tournament(sc.event, player_id)
        if not event_reg:
            return Err(ValidationError("Player is not registered for this event"))

        existing = SideCompRegistration.query.filter_by(comp=comp_id, player=player_id).first()
        if existing is not None and existing.is_complete():
            return Err(ValidationError("Player is already registered for this side competition"))

        parsed_weapon = _parse_weapon(weapon)
        if parsed_weapon is None:
            return Err(ValidationError("A valid weapon selection is required"))
        SideCompService._validate_weapon_for_comp(sc, parsed_weapon).Q()

        if existing is not None:
            existing.weapon = parsed_weapon.value
            reg = existing
        else:
            reg = SideCompService._add_registration(
                sc,
                player_id=player_id,
                weapon=parsed_weapon,
                registered_by_to=True,
            )
        db.session.commit()
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
        """Return the cached roster payload used by the enter-results UI.

        Includes every registration in the comp (complete or incomplete) and
        every player holding an entry number in the comp's scope without a
        registration here (``registration_id`` is ``None``). Fully
        registered players come first, then by entry number.
        """
        from app.services.registration_resolver import (
            player_registrations_for_tournament,
            team_registrations_for_tournament,
        )
        from models import Player, SideComp, SideCompEntryNumber, SideCompRegistration, Team, Tournament

        sc = SideComp.query.get(comp_id)
        if sc is None:
            return Err(NotFoundError("Side competition not found"))

        tournament = Tournament.query.get(sc.event)
        if tournament is None:
            return Err(NotFoundError("Tournament not found"))

        regs_by_player = {r.player: r for r in SideCompRegistration.query.filter_by(comp=comp_id).all()}
        numbers_by_player = {
            n.player: n.number
            for n in SideCompEntryNumber.query.filter_by(scope=SideCompService._entry_number_scope(sc)).all()
        }
        numbers_by_player.update({player_id: reg.entry_number for player_id, reg in regs_by_player.items()})

        player_ids = list(numbers_by_player)
        players_by_id = {p.id: p for p in Player.query.filter(Player.id.in_(player_ids)).all()} if player_ids else {}

        event_regs = {
            er.player: er for er in player_registrations_for_tournament(tournament) if er.player in players_by_id
        }
        team_ids = {team_id for team_id in map(_side_comp_team_id, event_regs.values()) if team_id}
        teams_by_id = {t.id: t for t in Team.query.filter(Team.id.in_(team_ids)).all()} if team_ids else {}
        team_shortnames = {}
        team_pseudonyms = {}
        if team_ids:
            for tr in team_registrations_for_tournament(tournament):
                if tr.team in team_ids:
                    team_shortnames[tr.team] = tr.shortname
                    team_pseudonyms[tr.team] = tr.pseudonym

        registrants = []
        for player_id, entry_number in numbers_by_player.items():
            reg = regs_by_player.get(player_id)
            player = players_by_id.get(player_id)
            event_reg = event_regs.get(player_id)
            team_id = _side_comp_team_id(event_reg)
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
                    "registration_id": reg.id if reg else None,
                    "registered": reg is not None and reg.is_complete(),
                    "entry_number": entry_number,
                    "weapon": reg.weapon_name() if reg else None,
                    "player_id": player_id,
                    "display_name": player.name if player else player_id,
                    "profile_photo": player.profile_photo if player else None,
                    "jersey_name": event_reg.jersey_name if event_reg else None,
                    "jersey_number": event_reg.jersey_number if event_reg else None,
                    "team_id": team_id,
                    "team_shortname": shortname,
                    "team_profile_photo": team.profile_photo if team else None,
                    "track_losses": sc.tracks_losses_for_weapon(reg.weapon if reg else None),
                }
            )
        registrants.sort(key=lambda r: (not r["registered"], r["entry_number"]))

        return Ok(
            {
                "id": sc.id,
                "name": sc.name,
                "type": str(sc.type),
                "active": bool(sc.active),
                "track_matchups": bool(sc.track_matchups),
                "registrants": registrants,
            }
        )

    @staticmethod
    def _existing_registration(
        sc: "SideComp",
        *,
        registration_id: Optional[int],
        player_id: Optional[str],
    ) -> Result[Optional["SideCompRegistration"], ArctosError]:
        """Find the registration a point would be logged against.

        ``Ok(None)`` means *player_id* holds an entry number here but has no
        registration yet. The caller creates that registration only after the
        point itself is accepted.
        """
        from models import SideCompEntryNumber, SideCompRegistration

        if registration_id is not None:
            reg = SideCompRegistration.query.filter_by(id=registration_id, comp=sc.id).first()
            if reg is None:
                return Err(NotFoundError("Registration not found"))
            return Ok(reg)
        if not player_id:
            return Err(ValidationError("registration_id or player_id is required"))
        reg = SideCompRegistration.query.filter_by(player=player_id, comp=sc.id).first()
        if reg is not None:
            return Ok(reg)
        holds_number = SideCompEntryNumber.query.filter_by(
            scope=SideCompService._entry_number_scope(sc), player=player_id
        ).first()
        if holds_number is None:
            return Err(NotFoundError("Player has no side competition entry number here"))
        return Ok(None)

    @staticmethod
    def _open_for_results(sc: "SideComp") -> Result[None, ArctosError]:
        if sc.finalized:
            return Err(ValidationError("This side competition has been finalized; results cannot be entered"))
        if not sc.active:
            return Err(ValidationError("This side competition is not active; results cannot be entered"))
        return Ok(None)

    @staticmethod
    @allow_Q
    def log_result(
        comp_id: int,
        *,
        registration_id: Optional[int] = None,
        player_id: Optional[str] = None,
        points: int,
        ref_user_id: str,
    ) -> Result["SideCompResult", ArctosError]:
        """Persist a ``+1`` / ``-1`` point for a registrant.

        Identify the registrant by *registration_id*, or by *player_id* for a
        player holding an entry number in the comp's scope. A *player_id*
        without a registration in this comp gets an incomplete one (no
        weapon) created alongside the result.
        """
        from models import SideComp, SideCompResult, db

        if points not in (1, -1):
            return Err(ValidationError("points must be +1 or -1"))

        sc = SideComp.query.get(comp_id)
        if sc is None:
            return Err(NotFoundError("Side competition not found"))

        SideCompService._open_for_results(sc).Q()
        reg = SideCompService._existing_registration(sc, registration_id=registration_id, player_id=player_id).Q()

        weapon_value = reg.weapon if reg is not None else None
        if points < 0 and not sc.tracks_losses_for_weapon(weapon_value):
            return Err(ValidationError("Losses are not tracked for this player"))

        if reg is None:
            reg = SideCompService._add_registration(sc, player_id=player_id, weapon=None, registered_by_to=False)
            db.session.flush()

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
    def log_matchup(
        comp_id: int,
        *,
        winner_registration_id: Optional[int] = None,
        winner_player_id: Optional[str] = None,
        loser_registration_id: Optional[int] = None,
        loser_player_id: Optional[str] = None,
        ref_user_id: str,
    ) -> Result[list["SideCompResult"], ArctosError]:
        """Record a bout. Either person may be unknown.

        A known winner gets ``+1``. A known loser gets ``-1``. When both are
        known the rows share a timestamp and point at each other, so undoing
        either one removes the bout. At least one person is required.
        """
        from app.utils.datetime_helpers import now_utc_naive
        from models import SideComp, SideCompResult, db

        sc = SideComp.query.get(comp_id)
        if sc is None:
            return Err(NotFoundError("Side competition not found"))
        if not sc.track_matchups:
            return Err(ValidationError("Matchups are not tracked for this side competition"))

        SideCompService._open_for_results(sc).Q()
        winner_known = winner_registration_id is not None or bool(winner_player_id)
        loser_known = loser_registration_id is not None or bool(loser_player_id)
        if not winner_known and not loser_known:
            return Err(ValidationError("Pick at least one person"))

        winner = None
        if winner_known:
            winner = SideCompService._existing_registration(
                sc, registration_id=winner_registration_id, player_id=winner_player_id
            ).Q()
        loser = None
        if loser_known:
            loser = SideCompService._existing_registration(
                sc, registration_id=loser_registration_id, player_id=loser_player_id
            ).Q()
        if winner_known and loser_known:
            winner_player = winner.player if winner is not None else winner_player_id
            loser_player = loser.player if loser is not None else loser_player_id
            if winner_player == loser_player:
                return Err(ValidationError("Pick two different people"))

        if winner_known and winner is None:
            if not winner_player_id:
                return Err(ValidationError("registration_id or player_id is required"))
            winner = SideCompService._add_registration(
                sc, player_id=winner_player_id, weapon=None, registered_by_to=False
            )
        if loser_known and loser is None:
            if not loser_player_id:
                return Err(ValidationError("registration_id or player_id is required"))
            loser = SideCompService._add_registration(
                sc, player_id=loser_player_id, weapon=None, registered_by_to=False
            )
        db.session.flush()

        stamp = now_utc_naive()
        rows = []
        if winner is not None:
            rows.append(
                SideCompResult(
                    comp=comp_id,
                    player=winner.id,
                    opponent=loser.id if loser is not None else None,
                    points=1,
                    ref=ref_user_id,
                    flagged=False,
                    valid=True,
                    stamp=stamp,
                )
            )
        if loser is not None:
            rows.append(
                SideCompResult(
                    comp=comp_id,
                    player=loser.id,
                    opponent=winner.id if winner is not None else None,
                    points=-1,
                    ref=ref_user_id,
                    flagged=False,
                    valid=True,
                    stamp=stamp,
                )
            )
        db.session.add_all(rows)
        db.session.commit()
        return Ok(rows)

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
    @allow_Q
    def delete_result(
        result_uuid: str,
        *,
        actor_user_id: str,
    ) -> Result[None, ArctosError]:
        """Undo a logged point. Only the ref who logged it may delete it, and only
        while the side competition is still accepting results.

        Deleting one side of a matchup also deletes the other side, matched by
        the shared timestamp and the swapped opponent link.
        """
        from models import SideComp, SideCompResult, db

        result = SideCompResult.query.get(result_uuid)
        if result is None:
            return Err(NotFoundError("Result not found"))

        if result.ref != actor_user_id:
            return Err(UnauthorizedError("You can only undo points you entered"))

        sc = SideComp.query.get(result.comp)
        if sc is None:
            return Err(NotFoundError("Side competition not found"))

        if sc.finalized or not sc.active:
            return Err(ValidationError("This side competition is not active; results cannot be changed"))

        if result.opponent is not None:
            pair = SideCompResult.query.filter(
                SideCompResult.comp == result.comp,
                SideCompResult.player == result.opponent,
                SideCompResult.opponent == result.player,
                SideCompResult.ref == result.ref,
                SideCompResult.points == -result.points,
                SideCompResult.stamp == result.stamp,
                SideCompResult.uuid != result.uuid,
            ).first()
            if pair is not None:
                db.session.delete(pair)

        db.session.delete(result)
        db.session.commit()
        return Ok(None)

    @staticmethod
    @allow_Q
    def set_result_valid(
        result_uuid: str,
        *,
        valid: bool,
        actor_user_id: str,
        actor_user_type: str,
    ) -> Result["SideCompResult", ArctosError]:
        """TO-only: toggle whether a result counts toward standings."""
        from models import SideComp, SideCompResult, db

        result = SideCompResult.query.get(result_uuid)
        if result is None:
            return Err(NotFoundError("Result not found"))

        sc = SideComp.query.get(result.comp)
        if sc is None:
            return Err(NotFoundError("Side competition not found"))

        SideCompService._require_to(sc.event, actor_user_id, actor_user_type).Q()

        result.valid = bool(valid)
        db.session.commit()
        return Ok(result)

    @staticmethod
    @allow_Q
    def finalize(
        comp_id: int,
        *,
        actor_user_id: str,
        actor_user_type: str,
    ) -> Result["SideComp", ArctosError]:
        """TO-only: permanently mark results as final.

        Requires the side competition to be inactive. Once finalized, it
        cannot be reactivated and the flag cannot be cleared.
        """
        from models import SideComp, db

        sc = SideComp.query.get(comp_id)
        if sc is None:
            return Err(NotFoundError("Side competition not found"))

        SideCompService._require_to(sc.event, actor_user_id, actor_user_type).Q()

        if sc.finalized:
            return Err(ValidationError("This side competition has already been finalized"))

        if sc.active:
            return Err(ValidationError("Deactivate the side competition before finalizing results"))

        sc.finalized = True
        db.session.commit()
        return Ok(sc)

    @staticmethod
    @allow_Q
    def manage_results(
        comp_id: int,
        *,
        actor_user_id: str,
        actor_user_type: str,
    ) -> Result[dict, ArctosError]:
        """TO-only: every logged point for *comp_id*, oldest first."""
        from app.services.registration_resolver import (
            player_registrations_for_tournament,
            team_registrations_for_tournament,
        )
        from models import Player, SideComp, SideCompRegistration, SideCompResult, Team, Tournament

        sc = SideComp.query.get(comp_id)
        if sc is None:
            return Err(NotFoundError("Side competition not found"))

        SideCompService._require_to(sc.event, actor_user_id, actor_user_type).Q()

        tournament = Tournament.query.get(sc.event)
        regs = {r.id: r for r in SideCompRegistration.query.filter_by(comp=comp_id).all()}
        player_ids = [r.player for r in regs.values()]
        players_by_id = {p.id: p for p in Player.query.filter(Player.id.in_(player_ids)).all()} if player_ids else {}

        event_regs = {}
        team_shortnames = {}
        team_pseudonyms = {}
        teams_by_id = {}
        if tournament is not None and player_ids:
            for er in player_registrations_for_tournament(tournament):
                if er.player in players_by_id:
                    event_regs[er.player] = er
            team_ids = {team_id for team_id in map(_side_comp_team_id, event_regs.values()) if team_id}
            if team_ids:
                teams_by_id = {t.id: t for t in Team.query.filter(Team.id.in_(team_ids)).all()}
                for tr in team_registrations_for_tournament(tournament):
                    if tr.team in team_ids:
                        team_shortnames[tr.team] = tr.shortname
                        team_pseudonyms[tr.team] = tr.pseudonym

        results_out = []
        rows = (
            SideCompResult.query.filter_by(comp=comp_id)
            .order_by(SideCompResult.stamp.asc(), SideCompResult.uuid.asc())
            .all()
        )
        for result in rows:
            reg = regs.get(result.player)
            player_id = reg.player if reg else None
            player = players_by_id.get(player_id) if player_id else None
            event_reg = event_regs.get(player_id) if player_id else None
            team_id = _side_comp_team_id(event_reg)
            team = teams_by_id.get(team_id) if team_id else None
            display_team_name = None
            if team_id:
                display_team_name = team_pseudonyms.get(team_id) or (team.name if team else None)
            shortname = _resolve_team_short_label(
                team_id=team_id,
                shortname=team_shortnames.get(team_id) if team_id else None,
                team_name=display_team_name,
            )
            results_out.append(
                {
                    **result.to_payload(),
                    "entry_number": reg.entry_number if reg else None,
                    "weapon": reg.weapon_name() if reg else None,
                    "registered": reg is not None and reg.is_complete(),
                    "player_id": player_id,
                    "display_name": player.name if player else (player_id or ""),
                    "jersey_name": event_reg.jersey_name if event_reg else None,
                    "jersey_number": event_reg.jersey_number if event_reg else None,
                    "team_shortname": shortname,
                    "untracked": result.points < 0 and reg is not None and not sc.tracks_losses_for_weapon(reg.weapon),
                }
            )

        return Ok(
            {
                "id": sc.id,
                "name": sc.name,
                "type": str(sc.type),
                "active": bool(sc.active),
                "finalized": bool(sc.finalized),
                "results": results_out,
            }
        )

    @staticmethod
    def standings(
        comp_id: int,
        *,
        include_below_cut: bool = False,
        viewer_player_id: str | None = None,
        application_view: bool = False,
        table_id: str | None = None,
        weapon: str | None = None,
        sort_by: str | None = None,
    ) -> Result[dict, ArctosError]:
        """Compute standings tables for *comp_id*.

        ``application_view`` limits weapon choices to complete registrations
        with valid results. The public table shows the configured number of
        leaders, or one leader when a weapon is selected. Filtered table ranks
        are relative to that weapon. Personal standings retain the whole-table
        rank and are independent of the selected weapon. The external API uses
        the default summary with the configured limit for every selection.

        ``include_below_cut`` returns complete standings for an organizer.
        ``viewer_player_id`` adds only that player's own standing. Callers must
        derive both values from the authenticated viewer. A zero limit hides
        every result, including personal standings. Only valid results count
        toward wins and losses.
        """
        from sqlalchemy import case, func

        from app.domain.enums import Pompfen, SideCompType
        from app.services.registration_resolver import (
            player_registrations_for_tournament,
            team_registrations_for_tournament,
        )
        from models import Player, SideComp, SideCompRegistration, SideCompResult, Team, Tournament, db

        sc = SideComp.query.get(comp_id)
        if sc is None:
            return Err(NotFoundError("Side competition not found"))

        table_ids = ("chains", "breaks") if sc.type == SideCompType.CHAIN_BREAKING else ("all",)
        if table_id is not None and table_id not in table_ids:
            return Err(ValidationError("Invalid standings table"))
        selected_weapon = Pompfen.from_name(weapon)
        if weapon is not None and selected_weapon is None:
            return Err(ValidationError("Invalid standings weapon"))
        if sort_by is not None and sort_by not in ("wins", "win_percentage"):
            return Err(ValidationError("Invalid standings sort"))

        tournament = Tournament.query.get(sc.event)
        regs = SideCompRegistration.query.filter(
            SideCompRegistration.comp == comp_id,
            SideCompRegistration.weapon.isnot(None),
        ).all()

        score_rows = (
            db.session.query(
                SideCompResult.player,
                func.coalesce(func.sum(case((SideCompResult.points > 0, 1), else_=0)), 0),
                func.coalesce(func.sum(case((SideCompResult.points < 0, 1), else_=0)), 0),
            )
            .filter(
                SideCompResult.comp == comp_id,
                SideCompResult.valid.is_(True),
            )
            .group_by(SideCompResult.player)
            .all()
        )
        wins_by_reg = {reg_id: int(wins or 0) for reg_id, wins, _ in score_rows}
        losses_by_reg = {reg_id: int(losses or 0) for reg_id, _, losses in score_rows}
        # A valid loss also establishes participation, even when wins are zero.
        scored_registration_ids = {reg_id for reg_id, _, _ in score_rows}

        player_ids = [r.player for r in regs]
        players_by_id = {p.id: p for p in Player.query.filter(Player.id.in_(player_ids)).all()} if player_ids else {}
        event_regs = {}
        team_shortnames = {}
        team_pseudonyms = {}
        teams_by_id = {}
        if tournament is not None and player_ids:
            for er in player_registrations_for_tournament(tournament):
                if er.player in players_by_id:
                    event_regs[er.player] = er
            team_ids = {team_id for team_id in map(_side_comp_team_id, event_regs.values()) if team_id}
            if team_ids:
                teams_by_id = {t.id: t for t in Team.query.filter(Team.id.in_(team_ids)).all()}
                for tr in team_registrations_for_tournament(tournament):
                    if tr.team in team_ids:
                        team_shortnames[tr.team] = tr.shortname
                        team_pseudonyms[tr.team] = tr.pseudonym

        def row_for(reg: SideCompRegistration, rank: int, track_losses: bool) -> dict:
            wins = wins_by_reg.get(reg.id, 0)
            losses = losses_by_reg.get(reg.id, 0) if track_losses else None
            win_percentage = 100 * wins / (wins + losses) if losses is not None and wins + losses > 0 else None
            player = players_by_id.get(reg.player)
            event_reg = event_regs.get(reg.player)
            team_id = _side_comp_team_id(event_reg)
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
                "team_id": team_id,
                "team_shortname": shortname,
                "team_profile_photo": teams_by_id[team_id].profile_photo
                if team_id and team_id in teams_by_id
                else None,
                "wins": wins,
                "losses": losses,
                "win_percentage": win_percentage,
                "rank": rank,
            }

        def build_table(table_id: str, title: str, members: list, track_losses: bool) -> dict:
            selectable_members = [reg for reg in members if not application_view or reg.id in scored_registration_ids]
            available_weapons = [p.name for p in Pompfen if any(reg.weapon == p.value for reg in selectable_members)]
            effective_sort = "win_percentage" if track_losses and sort_by != "wins" else "wins"

            def sort_key(reg: SideCompRegistration):
                wins = wins_by_reg.get(reg.id, 0)
                if effective_sort == "wins":
                    return (-wins, reg.entry_number)
                games = wins + losses_by_reg.get(reg.id, 0)
                if games == 0:
                    return (1, 0.0, 0, reg.entry_number)
                return (0, -wins / games, -wins, reg.entry_number)

            ranked = sorted(members, key=sort_key)
            top_n = sc.only_show_top_n_results
            personal_row = None
            if top_n == 0:
                visible = []
            else:
                for rank, reg in enumerate(ranked, start=1):
                    if reg.player == viewer_player_id:
                        personal_row = row_for(reg, rank, track_losses)
                        break
                visible = ranked
                if selected_weapon is not None:
                    visible = [reg for reg in visible if reg.weapon == selected_weapon.value]
                    if application_view and selected_weapon.name not in available_weapons:
                        visible = []
                if not include_below_cut and top_n is not None:
                    limit = 1 if application_view and selected_weapon is not None else top_n
                    visible = visible[:limit]
            return {
                "id": table_id,
                "title": title,
                "track_losses": track_losses,
                "available_weapons": available_weapons,
                "weapon": weapon,
                "sort": effective_sort,
                "rows": [row_for(reg, rank, track_losses) for rank, reg in enumerate(visible, start=1)],
                "personal_row": personal_row,
            }

        if sc.type == SideCompType.CHAIN_BREAKING:
            chains = [r for r in regs if r.weapon == Pompfen.CHAIN.value]
            breaks = [r for r in regs if r.weapon != Pompfen.CHAIN.value]
            tables = [
                build_table("chains", "Chains", chains, bool(sc.track_chain_losses or sc.track_matchups)),
                build_table("breaks", "Breaks", breaks, bool(sc.track_break_losses or sc.track_matchups)),
            ]
        else:
            tables = [build_table("all", "Standings", regs, bool(sc.track_losses))]
        if table_id is not None:
            tables = [table for table in tables if table["id"] == table_id]

        return Ok(
            {
                "id": sc.id,
                "name": sc.name,
                "type": str(sc.type),
                "finalized": bool(sc.finalized),
                "only_show_top_n_results": sc.only_show_top_n_results,
                "showing_all_results": sc.results_page_enabled()
                and (include_below_cut or sc.only_show_top_n_results is None),
                "results_page_enabled": sc.results_page_enabled(),
                "tables": tables,
            }
        )
