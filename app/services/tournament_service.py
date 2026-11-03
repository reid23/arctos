"""Tournament-page context assembly.

Builds the data the SPA index endpoint needs to render a tournament
home page: tournament metadata, registered teams / players, schedule
preview, and standings.  The route handlers stay thin and delegate
here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List

from app.domain.enums import TeamRegistrationStatus
from app.services.permission_service import PermissionService
from app.utils.user_helpers import is_player, is_team
from app.utils.datetime_helpers import now_utc_naive


@dataclass(frozen=True)
class TournamentService:
    """Application-level service for tournament homepage data.

    All methods are static; the class acts as a typed namespace.
    """

    @staticmethod
    def get_homepage_context(user=None) -> Dict[str, Any]:
        """Build the homepage context dict for the ``index.html`` template.

        Queries published tournaments visible to *user*, computes per-tournament
        team registration counts, and determines the current user's registration
        and waiver status for each tournament.

        Args:
            user: Authenticated Flask-Login user (player or team), or ``None``
                for unauthenticated requests.

        Returns:
            A dict with the following keys:

            * ``tournaments``: All accessible tournaments (legacy; prefer
              ``upcoming_tournaments`` / ``past_tournaments``).
            * ``upcoming_tournaments``: Tournaments that have not yet ended.
            * ``past_tournaments``: Tournaments that have already ended, sorted
              most-recent-first.
            * ``to_tournaments``: Empty list (legacy placeholder).
            * ``team_counts``: Mapping of tournament URL → confirmed team count.
            * ``user_reg_status``: Mapping of tournament URL → registration
              status dict for the current user.
        """
        from models import Tournament, TeamRegistration, PlayerRegistration, db

        published_tournaments = Tournament.query.filter_by(published=True).all()

        to_tournament_urls, league_urls = PermissionService.organized_scopes(user)
        if league_urls:
            to_tournament_urls.update(
                row.url for row in Tournament.query.filter(Tournament.league_id.in_(league_urls)).all()
            )

        all_tournament_urls = {t.url for t in published_tournaments}
        all_tournament_urls.update(to_tournament_urls or [])

        tournaments = (
            Tournament.query.filter(Tournament.url.in_(list(all_tournament_urls)))
            .order_by(Tournament.start_date.asc())
            .all()
        )

        # Compute registered team counts per tournament (event-based and league-based)
        from sqlalchemy import func

        team_counts: Dict[str, int] = {t.url: 0 for t in tournaments}
        if tournaments:
            event_urls = [t.url for t in tournaments]
            league_ids = list({t.league_id for t in tournaments if t.league_id})
            # Count by event (standalone tournaments)
            event_counts = (
                db.session.query(TeamRegistration.event, func.count(TeamRegistration.id))
                .filter(TeamRegistration.status == TeamRegistrationStatus.CONFIRMED)
                .filter(TeamRegistration.event.in_(event_urls))
                .filter(TeamRegistration.event.isnot(None))
                .group_by(TeamRegistration.event)
                .all()
            )
            for event, count in event_counts:
                if event:
                    team_counts[event] = int(count or 0)
            # Count by league (league events: all events in same league share one count)
            if league_ids:
                league_counts = (
                    db.session.query(TeamRegistration.league_id, func.count(TeamRegistration.id))
                    .filter(TeamRegistration.status == TeamRegistrationStatus.CONFIRMED)
                    .filter(TeamRegistration.league_id.in_(league_ids))
                    .group_by(TeamRegistration.league_id)
                    .all()
                )
                league_count_map = {lid: int(c or 0) for lid, c in league_counts}
                for t in tournaments:
                    if t.league_id:
                        team_counts[t.url] = league_count_map.get(t.league_id, 0)

        user_reg_status: Dict[str, Any] = {}
        if user is not None and getattr(user, "is_authenticated", False):
            for t in tournaments:
                if is_team(user):
                    if t.league_id:
                        reg = TeamRegistration.query.filter_by(league_id=t.league_id, team=user.id).first()
                    else:
                        reg = TeamRegistration.query.filter_by(event=t.url, team=user.id).first()
                    if reg:
                        user_reg_status[t.url] = {
                            "type": "team",
                            "status": (reg.status.value if hasattr(reg.status, "value") else str(reg.status or "")),
                            "paid": bool(reg.paid),
                            "amount_paid": reg.amount_paid or 0.0,
                        }
                elif is_player(user):
                    if t.league_id:
                        reg = PlayerRegistration.query.filter_by(league_id=t.league_id, player=user.id).first()
                    else:
                        reg = PlayerRegistration.query.filter_by(event=t.url, player=user.id).first()
                    if reg:
                        reg_status_val = reg.status.value if hasattr(reg.status, "value") else str(reg.status or "")
                        # If a player's registration is cancelled, hide all status/waiver badges.
                        if reg_status_val == "CANCELLED":
                            continue

                        from app.utils.helpers import get_registrable_config

                        cfg = get_registrable_config(t)
                        waiver_required = bool(getattr(cfg, "waiver_filepath", None)) if cfg else False
                        waiver_sha_current = getattr(cfg, "waiver_sha256", None) if cfg else None
                        stored_signature_sha = (
                            getattr(reg, "waiver_legal_name_signature_sha256", None) if waiver_required else None
                        )
                        if not waiver_required:
                            waiver_status = None
                        elif not stored_signature_sha:
                            waiver_status = "NOT_SIGNED"
                        elif waiver_sha_current is not None and stored_signature_sha == waiver_sha_current:
                            waiver_status = "VALID"
                        else:
                            waiver_status = "OUT_OF_DATE"

                        user_reg_status[t.url] = {
                            "type": "player",
                            "status": (reg.status.value if hasattr(reg.status, "value") else str(reg.status or "")),
                            "paid": bool(reg.paid),
                            "amount_paid": reg.amount_paid or 0.0,
                            "waiver_required": waiver_required,
                            "waiver_status": waiver_status,
                        }

        now = now_utc_naive()
        upcoming_tournaments: List[Any] = []
        past_tournaments: List[Any] = []
        for t in tournaments:
            effective_end = t.end_date if t.end_date is not None else t.start_date
            if effective_end < now:
                past_tournaments.append(t)
            else:
                upcoming_tournaments.append(t)
        past_tournaments.sort(key=lambda t: t.end_date or t.start_date, reverse=True)

        return {
            "tournaments": tournaments,  # legacy; template can use upcoming/past
            "upcoming_tournaments": upcoming_tournaments,
            "past_tournaments": past_tournaments,
            "to_tournaments": [],  # legacy
            "team_counts": team_counts,
            "user_reg_status": user_reg_status,
        }
