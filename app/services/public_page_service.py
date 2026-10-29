"""Small, request-scoped view models for public HTML.

These models are deliberately separate from full application API responses: they
contain neither rosters nor contact details, signatures, payment instructions or
organizer configuration. Personal status is attached only for the current user;
the HTTP layer must never share-cache these responses. SQL selects and batches
only the requested directory page, even for signed-in visitors. Dioxus consumes
the same bounded directory projection through its dedicated JSON endpoint.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from urllib.parse import quote, urlencode, urlsplit

from sqlalchemy import and_, case, func, or_
from sqlalchemy.orm import joinedload, load_only

from app.domain.enums import RegistrationStatus, TeamRegistrationStatus
from app.error_values import Err, Ok, Result
from app.exceptions import ArctosError, NotFoundError
from app.filters import render_markdown
from app.services.permission_service import PermissionService
from app.utils.user_helpers import is_player, is_team


@dataclass(frozen=True)
class Link:
    label: str
    url: str


@dataclass(frozen=True)
class LabelValue:
    label: str
    value: str


@dataclass(frozen=True)
class RegistrationSummary:
    """Only the signed-in visitor's own registration state, never a signature."""

    account_type: str
    status: str
    status_label: str
    paid: bool
    waiver_status: str | None


@dataclass(frozen=True)
class EventCard:
    url: str
    name: str
    date_start: str
    date_end: str | None
    date_label: str
    location: str
    league_name: str | None
    league_url: str | None
    team_count: int
    max_teams: int | None
    registration_open: bool
    registration_label: str
    published: bool
    account_status: RegistrationSummary | None


@dataclass(frozen=True)
class DirectoryPage:
    query: str
    view: str
    page: int
    page_size: int
    total: int
    upcoming_count: int
    past_count: int
    cards: tuple[EventCard, ...]
    previous_url: str | None
    next_url: str | None
    upcoming_url: str
    past_url: str
    clear_url: str


@dataclass(frozen=True)
class EventPage:
    card: EventCard
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
    league_registration: bool
    banner_url: str | None
    schedule_available: bool
    indexable_app_sections: tuple[str, ...]


def local_url(path: str, prefix: str = "") -> str:
    """Build an internal URL beneath a trusted request deployment prefix."""
    return f"{prefix.rstrip('/')}/{path.lstrip('/')}"


def directory_url(query: str = "", view: str = "upcoming", page: int = 1, prefix: str = "") -> str:
    params: dict[str, str | int] = {}
    if query:
        params["q"] = query
    if view == "past":
        params["view"] = "past"
    if page > 1:
        params["page"] = page
    return local_url("", prefix) + (f"?{urlencode(params)}" if params else "")


def safe_terms_url(value: str | None) -> str | None:
    """Allow only HTTP(S) or ordinary same-site paths in organizer-supplied links."""
    if not value or any(ord(char) < 32 for char in value) or "\\" in value:
        return None
    value = value.strip()
    try:
        parsed = urlsplit(value)
        if parsed.scheme in {"http", "https"} and parsed.netloc and not parsed.username and not parsed.password:
            return value
        if not parsed.scheme and not parsed.netloc and value.startswith("/") and not value.startswith("//"):
            return value
    except ValueError:
        pass
    return None


def _date_label(start: date, end: date | None) -> str:
    def display(value: date) -> str:
        return f"{value.strftime('%b')} {value.day}, {value.year}"

    return display(start) if end is None or end == start else f"{display(start)} – {display(end)}"


def _fee(value: Decimal | None) -> str:
    if value is None:
        return "Not specified"
    return "Free" if value == 0 else f"${value:.2f}"


def _limit(value: int | None) -> str:
    return "Not specified" if value is None else str(value)


def _scope_key(row) -> tuple[str, str]:
    return ("league", row.league_id) if row.league_id else ("event", row.event)


def _event_scope(event) -> tuple[str, str]:
    return ("league", event.league_id) if event.league_id else ("event", event.url)


def _load_supplements(events: list, user):
    """Batch effective configuration, confirmed counts, and personal status.

    Scope rules match registration_resolver: a league event always uses league
    registration, never legacy event rows. No query is made once per card.
    """
    from models import League, PlayerRegistration, TeamRegistration, db

    event_urls = [event.url for event in events if not event.league_id]
    league_urls = list({event.league_id for event in events if event.league_id})
    leagues = (
        {
            league.url: league
            for league in League.query.options(joinedload(League.registrable_config))
            .filter(League.url.in_(league_urls))
            .all()
        }
        if league_urls
        else {}
    )
    counts: dict[tuple[str, str], int] = {}
    if event_urls:
        rows = (
            db.session.query(TeamRegistration.event, func.count(TeamRegistration.id))
            .filter(
                TeamRegistration.event.in_(event_urls),
                TeamRegistration.status == TeamRegistrationStatus.CONFIRMED,
            )
            .group_by(TeamRegistration.event)
            .all()
        )
        counts.update({("event", scope): count for scope, count in rows})
    if league_urls:
        rows = (
            db.session.query(TeamRegistration.league_id, func.count(TeamRegistration.id))
            .filter(
                TeamRegistration.league_id.in_(league_urls),
                TeamRegistration.status == TeamRegistrationStatus.CONFIRMED,
            )
            .group_by(TeamRegistration.league_id)
            .all()
        )
        counts.update({("league", scope): count for scope, count in rows})
    personal = {}
    if user is not None and getattr(user, "is_authenticated", False) and (is_team(user) or is_player(user)):
        model = TeamRegistration if is_team(user) else PlayerRegistration
        user_column = model.team if is_team(user) else model.player
        rows = model.query.filter(
            user_column == user.id,
            or_(model.event.in_(event_urls), model.league_id.in_(league_urls)),
        ).all()
        personal = {_scope_key(row): row for row in rows}
    return leagues, counts, personal


def _config(event, leagues):
    if event.league_id:
        league = leagues.get(event.league_id)
        return league.registrable_config if league else None
    return event.registrable_config


def _registration_summary(reg, cfg, user, *, include_cancelled: bool = False) -> RegistrationSummary | None:
    if reg is None or (not include_cancelled and is_player(user) and str(reg.status) == RegistrationStatus.CANCELLED):
        return None
    status = str(reg.status)
    labels = {
        "CONFIRMED": "Confirmed",
        "PENDING_TEAM_APPROVAL": "Awaiting Team Approval",
        "REJECTED": "Declined",
        "CANCELLED": "Cancelled",
    }
    waiver_status = None
    if is_player(user) and cfg and cfg.waiver_filepath:
        signed_hash = reg.waiver_legal_name_signature_sha256
        waiver_status = (
            "Not signed"
            if not signed_hash
            else ("Signed" if cfg.waiver_sha256 is not None and signed_hash == cfg.waiver_sha256 else "Needs updating")
        )
    return RegistrationSummary(
        account_type="team" if is_team(user) else "player",
        status=status,
        status_label=labels.get(status, "Status unavailable"),
        paid=bool(reg.paid),
        waiver_status=waiver_status,
    )


def _card(event, leagues, counts, personal, user, organized_leagues, prefix) -> EventCard:
    cfg = _config(event, leagues)
    league = leagues.get(event.league_id)
    # A published event does not publish the parent league's private title.
    visible_league = league if league and (league.published or league.url in organized_leagues) else None
    visible_registration = bool(cfg and (not event.league_id or visible_league))
    team_open = bool(visible_registration and cfg.team_registration_open)
    player_open = bool(visible_registration and cfg.player_registration_open)
    start = event.start_date.date()
    end = event.end_date.date() if event.end_date else None
    return EventCard(
        url=local_url(quote(event.url, safe=""), prefix),
        name=event.name,
        date_start=start.isoformat(),
        date_end=end.isoformat() if end else None,
        date_label=_date_label(start, end),
        location=event.location or "Location not specified",
        league_name=visible_league.name if visible_league else None,
        league_url=local_url(f"leagues/{quote(visible_league.url, safe='')}", prefix) if visible_league else None,
        team_count=int(counts.get(_event_scope(event), 0)),
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
        published=bool(event.published),
        account_status=_registration_summary(personal.get(_event_scope(event)), cfg, user),
    )


@dataclass(frozen=True)
class PublicPageService:
    """Public document projection; HTTP routing/caching remain in the blueprint."""

    @staticmethod
    def directory(
        *,
        query: str = "",
        view: str = "upcoming",
        page: int = 1,
        page_size: int = 24,
        user=None,
        today: date | None = None,
        prefix: str = "",
    ) -> DirectoryPage:
        """Read one page of visible events with deterministic calendar-day ordering.

        The server uses today's UTC date (overridable here for tests). Event dates
        themselves are calendar dates and are never timezone-converted. An event
        stays upcoming throughout its final calendar day. Search is literal:
        SQL LIKE wildcard characters supplied by the visitor are escaped.
        """
        from models import League, Tournament, db

        today = today or datetime.now(timezone.utc).date()
        page = max(1, page)
        page_size = max(1, min(48, page_size))
        query = query.strip()[:200]
        view = "past" if view == "past" else "upcoming"
        event_scopes, league_scopes = PermissionService.organized_scopes(user)
        visible = or_(
            Tournament.published.is_(True),
            and_(Tournament.league_id.is_(None), Tournament.url.in_(event_scopes)),
            Tournament.league_id.in_(league_scopes),
        )
        q = db.session.query(Tournament).outerjoin(League, Tournament.league_id == League.url).filter(visible)
        if query:
            literal = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            pattern = f"%{literal}%"
            sqlite = db.engine.dialect.name == "sqlite"

            def contains(column):
                # Registered on every SQLite connection by the application
                # factory. Keep native ILIKE on databases that support it.
                if sqlite:
                    return func.arctos_lower(column).like(pattern.lower(), escape="\\")
                return column.ilike(pattern, escape="\\")

            q = q.filter(
                or_(
                    contains(Tournament.name),
                    contains(Tournament.location),
                    and_(
                        or_(League.published.is_(True), League.url.in_(league_scopes)),
                        contains(League.name),
                    ),
                )
            )
        effective_end = func.coalesce(Tournament.end_date, Tournament.start_date)
        # Comparing with midnight, rather than SQL date(), preserves index options
        # and the entered day semantics across SQLite and PostgreSQL.
        midnight = datetime.combine(today, datetime.min.time())
        is_past = effective_end < midnight
        totals = q.with_entities(
            func.count(Tournament.url),
            func.sum(case((is_past, 1), else_=0)),
        ).one()
        past_count = int(totals[1] or 0)
        upcoming_count = int(totals[0] or 0) - past_count
        total = past_count if view == "past" else upcoming_count
        # Clamp absurd page values before OFFSET reaches the database. Empty
        # searches stay on page 1; the HTTP URL need not be redirected.
        page = min(page, max(1, (total + page_size - 1) // page_size))
        q = q.filter(is_past if view == "past" else ~is_past)
        ordering = (
            (effective_end.desc(), Tournament.start_date.desc(), Tournament.url.asc())
            if view == "past"
            else (
                Tournament.start_date.asc(),
                Tournament.url.asc(),
            )
        )
        events = (
            q.options(
                load_only(
                    Tournament.url,
                    Tournament.name,
                    Tournament.start_date,
                    Tournament.end_date,
                    Tournament.location,
                    Tournament.league_id,
                    Tournament.registrable_config_id,
                    Tournament.published,
                ),
                joinedload(Tournament.registrable_config),
            )
            .order_by(*ordering)
            .limit(page_size)
            .offset(
                (page - 1) * page_size,
            )
            .all()
        )
        leagues, counts, personal = _load_supplements(events, user)
        cards = tuple(_card(event, leagues, counts, personal, user, league_scopes, prefix) for event in events)
        return DirectoryPage(
            query,
            view,
            page,
            page_size,
            total,
            upcoming_count,
            past_count,
            cards,
            directory_url(query, view, page - 1, prefix) if page > 1 else None,
            directory_url(query, view, page + 1, prefix) if page * page_size < total else None,
            directory_url(query, "upcoming", prefix=prefix),
            directory_url(query, "past", prefix=prefix),
            directory_url(view=view, prefix=prefix),
        )

    @staticmethod
    def event(slug: str, *, user=None, prefix: str = "") -> Result[EventPage, ArctosError]:
        from models import Tournament

        event = Tournament.query.options(joinedload(Tournament.registrable_config)).filter_by(url=slug).first()
        if event is None or not PermissionService.can_view_tournament(slug, user):
            # Missing and private events deliberately have the same response.
            return Err(NotFoundError("Event not found"))
        organizer = PermissionService.is_tournament_organizer_of(event, user)
        _, organized_leagues = PermissionService.organized_scopes(user)
        leagues, counts, personal = _load_supplements([event], user)
        cfg = _config(event, leagues)
        card = _card(event, leagues, counts, personal, user, organized_leagues, prefix)
        event_url = card.url
        scope_url = local_url(f"leagues/{quote(event.league_id, safe='')}", prefix) if event.league_id else event_url
        registration_url = f"{scope_url}/register"
        status = card.account_status
        registered = status and status.status in {"CONFIRMED", "PENDING_TEAM_APPROVAL"}
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
        scope_visible = not event.league_id or card.league_url is not None
        if not scope_visible:
            action = None
            message = "Registration is not available yet."
        elif registered:
            action = Link("Edit registration", registration_url)
            message = "Your registration covers every event in this league." if event.league_id else ""
        elif open_for_user:
            action = Link(
                "Register" if authenticated else "Log in to register",
                registration_url
                if authenticated
                else local_url("login", prefix) + "?" + urlencode({"next": registration_url}),
            )
            message = "Registration covers every event in this league." if event.league_id else ""
        else:
            action = None
            message = (
                "Team registration closed"
                if is_team(user)
                else "Player registration closed"
                if is_player(user)
                else "Registration closed"
            )
        schedule_available = PermissionService.can_view_schedule(event, user)
        nav = [Link("Info", event_url), Link("Teams", f"{event_url}?tab=teams")]
        if schedule_available:
            nav.append(Link("Schedule", f"{event_url}/schedule"))
        nav.append(Link("Results", f"{event_url}/results"))
        if event.bracket_published or organizer:
            nav.append(Link("Bracket", f"{event_url}/bracket"))
        nav.append(Link("Side events", f"{event_url}?tab=sidecomps"))
        organizer_links = ()
        if organizer:
            organizer_links = (
                Link("Event settings", f"{event_url}/settings"),
                Link("Edit schedule", f"{event_url}/schedule/edit"),
                Link("Manage registrations", f"{scope_url}/manage"),
                Link("Manage footage", f"{event_url}/manage-footage"),
                Link("All organizer tools", f"{event_url}?tab=app"),
            )
        # Banners are static-relative uploaded paths; never permit a script URL or
        # arbitrary remote origin to become part of this public document.
        banner_url = None
        if event.banner_photo:
            path = event.banner_photo.removeprefix("/static/").removeprefix("static/")
            if not path.startswith("/") and ":" not in path and "\\" not in path and ".." not in path.split("/"):
                banner_url = local_url("static/" + quote(path, safe="/"), prefix)
        facts = (
            LabelValue(
                "Team registration",
                "Not available yet"
                if not scope_visible
                else "Open"
                if cfg and cfg.team_registration_open
                else "Closed",
            ),
            LabelValue(
                "Player registration",
                "Not available yet"
                if not scope_visible
                else "Open"
                if cfg and cfg.player_registration_open
                else "Closed",
            ),
            LabelValue("Team fee", _fee(cfg.team_reg_fee if cfg else None)),
            LabelValue("Player fee", _fee(cfg.player_reg_fee if cfg else None)),
        )
        limits = (
            LabelValue("Team capacity", _limit(cfg.n_max_teams if cfg else None)),
            LabelValue("Players per roster", _limit(cfg.max_team_size_roster if cfg else None)),
            LabelValue("Players on field", _limit(cfg.max_team_size_field if cfg else None)),
        )
        return Ok(
            EventPage(
                card,
                render_markdown(event.about),
                facts,
                limits,
                tuple(nav),
                action,
                message,
                status,
                Link("Manage roster", f"{scope_url}/invitations")
                if registered and is_team(user) and scope_visible
                else None,
                organizer_links,
                bool(cfg and cfg.waiver_filepath),
                safe_terms_url(cfg.terms_link if cfg else None),
                bool(event.league_id),
                banner_url,
                schedule_available,
                tuple(
                    ["info", "teams", "sidecomps", "results"]
                    + (["schedule"] if event.schedule_published else [])
                    + (["bracket"] if event.bracket_published else [])
                )
                if event.published
                else (),
            )
        )
