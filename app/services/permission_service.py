"""
Authorization and permission checks.

This service is intentionally Flask-agnostic: it does not use `current_user`,
`request`, or `flash`. Callers (routes/decorators) handle UI/HTTP concerns.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.utils.user_helpers import is_player, is_team
from app.error_values import Null, Option, Some


@dataclass(frozen=True)
class PermissionService:
    """Flask-agnostic authorization and permission checks.

    All methods are static; the class acts as a typed namespace.  Methods
    never reference ``current_user``, ``request``, or ``flash`` — those
    concerns belong in route handlers and decorators.
    """

    @staticmethod
    def user_type(user) -> "Option[UserType]":
        """Return the account type for *user*.

        Args:
            user: Any Flask-Login user object, or ``None``.

        Returns:
            :class:`~app.error_values.Some` containing
            :class:`~app.domain.enums.UserType`, or
            :class:`~app.error_values.Null` for ``None`` / unsupported types.
        """
        from app.domain.enums import UserType

        if user is None:
            return Null()
        if is_player(user):
            return Some(UserType.PLAYER)
        if is_team(user):
            return Some(UserType.TEAM)
        return Null()

    @staticmethod
    def is_tournament_organizer_of(tournament, user) -> bool:
        """Return whether *user* is a TO for the pre-loaded *tournament*.

        Same semantics as :meth:`is_tournament_organizer` but skips the
        Tournament fetch when the caller already has the row loaded - intended
        for hot service-layer paths where the Tournament was loaded for other
        reasons.

        Args:
            tournament: A loaded :class:`~app.models.tournament.Tournament`, or
                ``None``.
            user: Flask-Login user object, or ``None``.

        Returns:
            ``True`` if a matching :class:`~app.models.tournament.TO` row exists.
        """
        if tournament is None or user is None:
            return False
        match PermissionService.user_type(user):
            case Some(user_type):
                pass
            case _:
                return False

        from models import TO

        q = TO.query.filter_by(user_id=user.id, user_type=str(user_type))
        if tournament.league_id:
            q = q.filter_by(league_id=tournament.league_id)
        else:
            q = q.filter_by(event=tournament.url)
        return q.first() is not None

    @staticmethod
    def is_tournament_organizer(tournament_url: str, user) -> bool:
        """Return whether *user* is a Tournament Organiser for this tournament.

        For tournaments attached to a league (``Tournament.league_id`` is set),
        league-season TOs grant access; otherwise event-specific TOs are
        consulted. Mirrors :func:`~app.services.registration_resolver.to_entries_for_tournament`.

        Args:
            tournament_url: URL slug of the tournament.
            user: Flask-Login user object (player or team), or ``None``.

        Returns:
            ``True`` if a matching :class:`~app.models.tournament.TO` row exists.
        """
        if not tournament_url or user is None:
            return False
        from models import Tournament

        tournament = Tournament.query.filter_by(url=tournament_url).first()
        return PermissionService.is_tournament_organizer_of(tournament, user)

    @staticmethod
    def is_league_organizer(league_url: str, user) -> bool:
        """Return whether *user* is a TO for the league at *league_url*.

        Args:
            league_url: URL slug of the league.
            user: Flask-Login user object, or ``None``.

        Returns:
            ``True`` if a matching :class:`~app.models.tournament.TO` row exists.
        """
        if not league_url or user is None:
            return False
        match PermissionService.user_type(user):
            case Some(user_type):
                pass
            case _:
                return False

        from models import TO

        return TO.query.filter_by(user_id=user.id, user_type=str(user_type), league_id=league_url).first() is not None

    @staticmethod
    def organized_scopes(user) -> tuple[set[str], set[str]]:
        """Return the ``(tournament_urls, league_urls)`` that *user* is a TO of.

        Args:
            user: Flask-Login user object, or ``None``.

        Returns:
            Two sets of URL slugs; both empty for ``None`` / unsupported users.
        """
        match PermissionService.user_type(user):
            case Some(user_type):
                pass
            case _:
                return set(), set()

        from models import TO

        rows = TO.query.filter_by(user_id=user.id, user_type=str(user_type)).all()
        return {row.event for row in rows if row.event}, {row.league_id for row in rows if row.league_id}

    @staticmethod
    def can_view_tournament(tournament_url: str, user) -> bool:
        """Return whether *user* may view the tournament.

        Args:
            tournament_url: URL slug of the tournament.
            user: Flask-Login user, or ``None`` for unauthenticated requests.

        Returns:
            ``True`` if the tournament is published, or if *user* is a TO.
        """
        from models import Tournament

        tournament = Tournament.query.get(tournament_url)
        if not tournament:
            return False
        if tournament.published:
            return True
        return PermissionService.is_tournament_organizer(tournament_url, user)

    @staticmethod
    def can_view_schedule(tournament, user) -> bool:
        """Apply the schedule-publication gate for an already viewable tournament.

        Published schedules are public, including empty schedules. Organizers
        and players with general head-ref permission can preview unpublished
        schedules; permission for a particular match alone is insufficient.
        Callers must also enforce the existing tournament-visibility gate.
        """
        if tournament.schedule_published:
            return True
        if user is None or not getattr(user, "is_authenticated", False):
            return False
        if PermissionService.is_tournament_organizer_of(tournament, user):
            return True
        return PermissionService.can_head_ref_match(tournament.url, user, match=None)

    @staticmethod
    def can_head_ref_match(tournament_url: str, user, match=None) -> bool:
        """Return whether *user* may head-ref *match* in this tournament.

        Delegates to :func:`~app.utils.helpers.can_head_ref_match` to avoid
        diverging from the authoritative policy implementation.

        Args:
            tournament_url: URL slug of the tournament.
            user: Flask-Login user, or ``None``.
            match: The :class:`~app.models.match.Match` to check, or ``None``
                for a general head-ref eligibility check.

        Returns:
            ``True`` if *user* is a player with head-ref permission.
        """
        if user is None or not is_player(user):
            return False
        from app.utils.helpers import can_head_ref_match as _can_head_ref_match

        return _can_head_ref_match(tournament_url, user.id, match=match)
