"""Request-scoped league facts for public documents.

The directory selects one bounded page and batches registration counts and the
requesting account's status. League descriptions, fees and limits are readable
without loading rosters, contact details or organizer configuration.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from urllib.parse import quote, urlencode

from sqlalchemy import and_, case, func, or_
from sqlalchemy.orm import joinedload, load_only

from app.domain.enums import TeamRegistrationStatus
from app.error_values import Err, Ok, Result
from app.exceptions import ArctosError, NotFoundError
from app.filters import render_markdown
from app.services.permission_service import PermissionService
from app.services.public_page_service import (
    LabelValue,
    Link,
    RegistrationSummary,
    _date_label,
    _fee,
    _limit,
    _registration_summary,
    local_url,
    safe_terms_url,
)
from app.utils.user_helpers import is_player, is_team


@dataclass(frozen=True)
class LeagueCard:
    url: str
    name: str
    date_start: str | None
    date_end: str | None
    date_label: str
    event_count: int
    team_count: int
    max_teams: int | None
    registration_open: bool
    registration_label: str
    published: bool
    account_status: RegistrationSummary | None


@dataclass(frozen=True)
class LeagueDirectoryPage:
    query: str
    view: str
    page: int
    page_size: int
    total: int
    active_count: int
    past_count: int
    cards: tuple[LeagueCard, ...]
    previous_url: str | None
    next_url: str | None
    active_url: str
    past_url: str
    clear_url: str


@dataclass(frozen=True)
class LeaguePage:
    card: LeagueCard
    description_html: str
    facts: tuple[LabelValue, ...]
    limits: tuple[LabelValue, ...]
    nav_links: tuple[Link, ...]
    registration_action: Link | None
    registration_message: str
    account_status: RegistrationSummary | None
    roster_action: Link | None
    organizer_links: tuple[Link, ...]
    waiver_required: bool
    terms_url: str | None
    banner_url: str | None
    indexable_app_sections: tuple[str, ...]


def league_directory_url(query: str = "", view: str = "active", page: int = 1, prefix: str = "") -> str:
    params: dict[str, str | int] = {}
    if query:
        params["q"] = query
    if view == "past":
        params["view"] = view
    if page > 1:
        params["page"] = page
    return local_url("leagues", prefix) + (f"?{urlencode(params)}" if params else "")


def _visible_query(user):
    from models import League, Tournament, db

    _, organized = PermissionService.organized_scopes(user)
    # Date ranges and event counts expose only rounds visible to this viewer.
    dates = (
        db.session.query(
            Tournament.league_id.label("league_id"),
            func.min(Tournament.start_date).label("start_date"),
            func.max(func.coalesce(Tournament.end_date, Tournament.start_date)).label("end_date"),
            func.count(Tournament.url).label("event_count"),
        )
        .filter(or_(Tournament.published.is_(True), Tournament.league_id.in_(organized)))
        .group_by(Tournament.league_id)
        .subquery()
    )
    return (
        db.session.query(League, dates.c.start_date, dates.c.end_date, dates.c.event_count)
        .outerjoin(dates, dates.c.league_id == League.url)
        .filter(or_(League.published.is_(True), League.url.in_(organized))),
        dates,
    )


def _supplements(leagues, user):
    from models import PlayerRegistration, TeamRegistration, db

    ids = [league.url for league in leagues]
    if not ids:
        return {}, {}
    counts = dict(
        db.session.query(TeamRegistration.league_id, func.count(TeamRegistration.id))
        .filter(TeamRegistration.league_id.in_(ids), TeamRegistration.status == TeamRegistrationStatus.CONFIRMED)
        .group_by(TeamRegistration.league_id)
        .all()
    )
    personal = {}
    if user is not None and getattr(user, "is_authenticated", False) and (is_player(user) or is_team(user)):
        model = TeamRegistration if is_team(user) else PlayerRegistration
        owner = model.team if is_team(user) else model.player
        personal = {row.league_id: row for row in model.query.filter(model.league_id.in_(ids), owner == user.id).all()}
    return counts, personal


def _card(row, counts, personal, user, prefix):
    league, start, end, event_count = row
    cfg = league.registrable_config
    team_open = bool(cfg and cfg.team_registration_open)
    player_open = bool(cfg and cfg.player_registration_open)
    start = start.date() if start else None
    end = end.date() if end else None
    return LeagueCard(
        url=local_url("leagues/" + quote(league.url, safe=""), prefix),
        name=league.name,
        date_start=start.isoformat() if start else None,
        date_end=end.isoformat() if end else None,
        date_label=_date_label(start, end) if start else "Dates to be announced",
        event_count=int(event_count or 0),
        team_count=int(counts.get(league.url, 0)),
        max_teams=cfg.n_max_teams if cfg else None,
        registration_open=team_open or player_open,
        registration_label=(
            "Registration open"
            if team_open and player_open
            else "Team registration open"
            if team_open
            else "Player registration open"
            if player_open
            else "Registration closed"
        ),
        published=bool(league.published),
        account_status=_registration_summary(personal.get(league.url), cfg, user, include_cancelled=True),
    )


class PublicLeagueService:
    @staticmethod
    def directory(
        *,
        query: str = "",
        view: str = "active",
        page: int = 1,
        page_size: int = 24,
        user=None,
        today: date | None = None,
        prefix: str = "",
    ) -> LeagueDirectoryPage:
        from models import League, Tournament, db

        today = today or datetime.now(timezone.utc).date()
        page = max(1, page)
        page_size = max(1, min(48, page_size))
        query = query.strip()[:200]
        view = "past" if view == "past" else "active"
        q, dates = _visible_query(user)
        # A league remains active while any round is unfinished, including a
        # draft. This classification never exposes a draft round's own dates.
        final_day = func.coalesce(Tournament.end_date, Tournament.start_date)
        seasons = (
            db.session.query(
                Tournament.league_id.label("league_id"),
                func.count(Tournament.url).label("round_count"),
                func.count(final_day).label("dated_count"),
                func.max(final_day).label("final_day"),
            )
            .group_by(Tournament.league_id)
            .subquery()
        )
        q = q.outerjoin(seasons, seasons.c.league_id == League.url)
        is_past = func.coalesce(
            and_(
                seasons.c.round_count > 0,
                seasons.c.round_count == seasons.c.dated_count,
                seasons.c.final_day < datetime.combine(today, datetime.min.time()),
            ),
            False,
        )
        if query:
            literal = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            pattern = f"%{literal}%"
            q = q.filter(
                func.arctos_lower(League.name).like(pattern.lower(), escape="\\")
                if db.engine.dialect.name == "sqlite"
                else League.name.ilike(pattern, escape="\\")
            )
        total_count, past_count = q.with_entities(
            func.count(League.url),
            func.sum(case((is_past, 1), else_=0)),
        ).one()
        past_count = int(past_count or 0)
        active_count = int(total_count or 0) - past_count
        total = past_count if view == "past" else active_count
        page = min(page, max(1, (total + page_size - 1) // page_size))
        name_order = func.arctos_lower(League.name) if db.engine.dialect.name == "sqlite" else func.lower(League.name)
        ordering = (
            (func.date(dates.c.end_date).desc(), name_order.asc(), League.url.asc())
            if view == "past"
            else (dates.c.start_date.is_(None), func.date(dates.c.start_date).asc(), name_order.asc(), League.url.asc())
        )
        rows = (
            q.filter(is_past if view == "past" else ~is_past)
            .options(
                load_only(League.url, League.name, League.published, League.registrable_config_id),
                joinedload(League.registrable_config),
            )
            .order_by(*ordering)
            .limit(page_size)
            .offset((page - 1) * page_size)
            .all()
        )
        counts, personal = _supplements([row[0] for row in rows], user)
        return LeagueDirectoryPage(
            query,
            view,
            page,
            page_size,
            total,
            active_count,
            past_count,
            tuple(_card(row, counts, personal, user, prefix) for row in rows),
            league_directory_url(query, view, page - 1, prefix) if page > 1 else None,
            league_directory_url(query, view, page + 1, prefix) if page * page_size < total else None,
            league_directory_url(query, "active", prefix=prefix),
            league_directory_url(query, "past", prefix=prefix),
            league_directory_url(view=view, prefix=prefix),
        )

    @staticmethod
    def league(slug: str, *, user=None, prefix: str = "") -> Result[LeaguePage, ArctosError]:
        from models import League

        q, _ = _visible_query(user)
        row = q.options(joinedload(League.registrable_config)).filter(League.url == slug).first()
        if row is None:
            return Err(NotFoundError("League not found"))
        league = row[0]
        cfg = league.registrable_config
        counts, personal = _supplements([league], user)
        card = _card(row, counts, personal, user, prefix)
        url = card.url
        registration_url = url + "/register"
        status = card.account_status
        registered = bool(status and status.status in {"CONFIRMED", "PENDING_TEAM_APPROVAL"})
        authenticated = user is not None and getattr(user, "is_authenticated", False)
        open_for_user = bool(
            cfg
            and (
                cfg.team_registration_open
                if is_team(user)
                else cfg.player_registration_open
                if is_player(user)
                else cfg.team_registration_open or cfg.player_registration_open
            )
        )
        if registered:
            action = Link("Edit registration", registration_url)
        elif open_for_user:
            action = Link(
                "Register" if authenticated else "Log in to register",
                registration_url
                if authenticated
                else local_url("login", prefix) + "?" + urlencode({"next": registration_url}),
            )
        else:
            action = None
        message = (
            "Registration covers every event in this league."
            if action
            else "Team registration closed"
            if is_team(user)
            else "Player registration closed"
            if is_player(user)
            else "Registration closed"
        )
        organizer = PermissionService.is_league_organizer(slug, user)
        banner_url = None
        if league.banner_photo:
            path = league.banner_photo.removeprefix("/static/").removeprefix("static/")
            if not path.startswith("/") and ":" not in path and "\\" not in path and ".." not in path.split("/"):
                banner_url = local_url("static/" + quote(path, safe="/"), prefix)
        return Ok(
            LeaguePage(
                card=card,
                description_html=render_markdown(league.about),
                facts=(
                    LabelValue("Team registration", "Open" if cfg and cfg.team_registration_open else "Closed"),
                    LabelValue("Player registration", "Open" if cfg and cfg.player_registration_open else "Closed"),
                    LabelValue("Team fee", _fee(cfg.team_reg_fee if cfg else None)),
                    LabelValue("Player fee", _fee(cfg.player_reg_fee if cfg else None)),
                ),
                limits=(
                    LabelValue("Team capacity", _limit(cfg.n_max_teams if cfg else None)),
                    LabelValue("Players per roster", _limit(cfg.max_team_size_roster if cfg else None)),
                    LabelValue("Players on field", _limit(cfg.max_team_size_field if cfg else None)),
                ),
                nav_links=(
                    Link("Info", url),
                    Link("Events", url + "?tab=events"),
                    Link("Teams", url + "?tab=teams"),
                    Link("Results", url + "/results"),
                ),
                registration_action=action,
                registration_message=message,
                account_status=status,
                roster_action=Link("Manage roster", url + "/invitations") if registered and is_team(user) else None,
                organizer_links=(
                    Link("Settings", url + "/settings"),
                    Link("Add Event", url + "/new-tournament"),
                    Link("Registration Management", url + "/manage"),
                )
                if organizer
                else (),
                waiver_required=bool(cfg and cfg.waiver_filepath),
                terms_url=safe_terms_url(cfg.terms_link if cfg else None),
                banner_url=banner_url,
                indexable_app_sections=("info", "events", "teams", "results") if league.published else (),
            )
        )
