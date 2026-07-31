"""
RegistrationResolver: centralizes queries for teams/players registered for a tournament.

Supports both standalone tournaments (event=tournament.url) and league tournaments
(league_id=tournament.league_id). Single source of truth for registration queries.
"""

from __future__ import annotations


from app.domain.enums import RegistrationStatus, TeamRegistrationStatus


def _scope_to_filter(scope) -> tuple[str | None, str | None]:
    """Translate a :class:`~app.services._common.Scope` to a ``(event, league_id)`` filter tuple.

    Args:
        scope: A :class:`~app.services._common.Scope` instance.

    Returns:
        ``(None, league_url)`` for league scopes, ``(event_url, None)`` for event scopes.
    """
    if scope.is_league:
        return None, scope.league_url
    return scope.event_url, None


def team_registrations_for_scope(scope, status=TeamRegistrationStatus.CONFIRMED, exclude_cancelled=False):
    """Return team registrations for a :class:`~app.services._common.Scope`.

    Args:
        scope: A :class:`~app.services._common.Scope` identifying event or league.
        status: Filter by this :class:`~app.domain.enums.TeamRegistrationStatus` (default
            ``CONFIRMED``). Ignored when *exclude_cancelled* is ``True``.
        exclude_cancelled: When ``True``, return all statuses except ``CANCELLED``.

    Returns:
        List of :class:`~app.models.registration.TeamRegistration` records.
    """
    from models import TeamRegistration

    event, league_id = _scope_to_filter(scope)
    q = TeamRegistration.query
    if league_id is not None:
        q = q.filter_by(league_id=league_id)
    else:
        q = q.filter_by(event=event)
    if exclude_cancelled:
        q = q.filter(TeamRegistration.status != TeamRegistrationStatus.CANCELLED)
    else:
        q = q.filter_by(status=status)
    return q.all()


def player_registrations_for_scope(scope, team_id=None, unattached_only=False, statuses=None):
    """Return player registrations for a :class:`~app.services._common.Scope`.

    Args:
        scope: A :class:`~app.services._common.Scope` identifying event or league.
        team_id: When supplied, limit results to players registered for this team.
            Ignored when *unattached_only* is ``True``.
        unattached_only: When ``True``, return only players without a team.
        statuses: List of :class:`~app.domain.enums.RegistrationStatus` values to include.
            Defaults to ``[PENDING_TEAM_APPROVAL, CONFIRMED]``.

    Returns:
        List of :class:`~app.models.registration.PlayerRegistration` records.
    """
    from models import PlayerRegistration

    if statuses is None:
        statuses = [
            RegistrationStatus.PENDING_TEAM_APPROVAL,
            RegistrationStatus.CONFIRMED,
        ]

    event, league_id = _scope_to_filter(scope)
    q = PlayerRegistration.query
    if league_id is not None:
        q = q.filter_by(league_id=league_id)
    else:
        q = q.filter_by(event=event)
    q = q.filter(PlayerRegistration.status.in_(statuses))
    if unattached_only:
        q = q.filter(PlayerRegistration.team.is_(None))
    elif team_id is not None:
        q = q.filter_by(team=team_id)
    return q.all()


def _registrable_filter(tournament) -> tuple[str | None, str | None]:
    """Determine the appropriate registration scope for a tournament.

    Args:
        tournament: A :class:`~app.models.tournament.Tournament` instance.

    Returns:
        A ``(event, league_id)`` tuple where exactly one value is non-``None``:
        ``(tournament.url, None)`` for standalone tournaments, or
        ``(None, tournament.league_id)`` for league tournaments.
    """
    if tournament.league_id:
        return None, tournament.league_id
    return tournament.url, None


def team_registration_for_tournament(tournament, team_id: str):
    """Return the confirmed :class:`~app.models.registration.TeamRegistration` for a team.

    Handles both standalone (event-scoped) and league-scoped registration
    lookup transparently.

    Args:
        tournament: The :class:`~app.models.tournament.Tournament` instance.
        team_id: ID of the team to look up.

    Returns:
        The :class:`~app.models.registration.TeamRegistration` with
        ``CONFIRMED`` status, or ``None`` if not found.
    """
    from models import TeamRegistration

    event, league_id = _registrable_filter(tournament)
    if league_id is not None:
        return TeamRegistration.query.filter_by(
            league_id=league_id,
            team=team_id,
            status=TeamRegistrationStatus.CONFIRMED,
        ).first()
    return TeamRegistration.query.filter_by(event=event, team=team_id, status=TeamRegistrationStatus.CONFIRMED).first()


def team_registrations_for_tournament(tournament, status=TeamRegistrationStatus.CONFIRMED, exclude_cancelled=False):
    """Return a list of team registrations for a tournament or league.

    Args:
        tournament: The :class:`~app.models.tournament.Tournament` instance.
        status: Filter by this
            :class:`~app.domain.enums.TeamRegistrationStatus` (default
            ``CONFIRMED``).  Ignored when *exclude_cancelled* is ``True``.
        exclude_cancelled: When ``True``, returns all statuses except
            ``CANCELLED``, ignoring the *status* argument.

    Returns:
        List of :class:`~app.models.registration.TeamRegistration` records.
    """
    from models import TeamRegistration

    event, league_id = _registrable_filter(tournament)
    q = TeamRegistration.query
    if league_id is not None:
        q = q.filter_by(league_id=league_id)
    else:
        q = q.filter_by(event=event)
    if exclude_cancelled:
        q = q.filter(TeamRegistration.status != TeamRegistrationStatus.CANCELLED)
    else:
        q = q.filter_by(status=status)
    return q.all()


def player_registrations_for_tournament(
    tournament,
    team_id=None,
    unattached_only=False,
    statuses=None,
):
    """Return player registrations for a tournament, with optional filters.

    Args:
        tournament: The :class:`~app.models.tournament.Tournament` instance.
        team_id: When supplied, limit results to players registered for this
            team.  Ignored when *unattached_only* is ``True``.
        unattached_only: When ``True``, return only players without a team
            (``team IS NULL``).
        statuses: List of
            :class:`~app.domain.enums.RegistrationStatus` values to include.
            Defaults to ``[PENDING_TEAM_APPROVAL, CONFIRMED]``.

    Returns:
        List of :class:`~app.models.registration.PlayerRegistration` records.
    """
    from models import PlayerRegistration

    if statuses is None:
        statuses = [
            RegistrationStatus.PENDING_TEAM_APPROVAL,
            RegistrationStatus.CONFIRMED,
        ]

    event, league_id = _registrable_filter(tournament)
    q = PlayerRegistration.query
    if league_id is not None:
        q = q.filter_by(league_id=league_id)
    else:
        q = q.filter_by(event=event)
    q = q.filter(PlayerRegistration.status.in_(statuses))
    if unattached_only:
        q = q.filter(PlayerRegistration.team.is_(None))
    elif team_id is not None:
        q = q.filter_by(team=team_id)
    return q.all()


def is_team_registered(tournament, team_id: str) -> bool:
    """True if team is registered for this tournament (event or league)."""
    from models import TeamRegistration

    event, league_id = _registrable_filter(tournament)
    if league_id is not None:
        return (
            TeamRegistration.query.filter_by(
                league_id=league_id,
                team=team_id,
                status=TeamRegistrationStatus.CONFIRMED,
            ).first()
            is not None
        )
    return (
        TeamRegistration.query.filter_by(event=event, team=team_id, status=TeamRegistrationStatus.CONFIRMED).first()
        is not None
    )


def player_registration_for_tournament(tournament, player_id: str, *, statuses=None):
    """Single player registration for (tournament, player_id), or None.

    Args:
        tournament: Tournament instance (standalone or league event).
        player_id: Player id to look up.
        statuses: Optional list of :class:`~app.domain.enums.RegistrationStatus`
            values. Defaults to pending + confirmed.
    """
    if statuses is None:
        statuses = [
            RegistrationStatus.PENDING_TEAM_APPROVAL,
            RegistrationStatus.CONFIRMED,
        ]
    prs = player_registrations_for_tournament(tournament, statuses=statuses)
    for pr in prs:
        if pr.player == player_id:
            return pr
    return None


def is_player_registered(tournament, player_id: str) -> bool:
    """True if player has a registration (pending or confirmed) for this tournament."""
    from models import PlayerRegistration

    event, league_id = _registrable_filter(tournament)
    if league_id is not None:
        q = PlayerRegistration.query.filter_by(league_id=league_id, player=player_id).filter(
            PlayerRegistration.status.in_(
                [
                    RegistrationStatus.PENDING_TEAM_APPROVAL,
                    RegistrationStatus.CONFIRMED,
                ]
            )
        )
    else:
        q = PlayerRegistration.query.filter_by(event=event, player=player_id).filter(
            PlayerRegistration.status.in_(
                [
                    RegistrationStatus.PENDING_TEAM_APPROVAL,
                    RegistrationStatus.CONFIRMED,
                ]
            )
        )
    return q.first() is not None


def to_entries_for_tournament(tournament):
    """TO rows for this tournament (event-specific or league-season TOs)."""
    from models import TO

    if tournament.league_id:
        return TO.query.filter_by(league_id=tournament.league_id).all()
    return TO.query.filter_by(event=tournament.url).all()
