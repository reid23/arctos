"""Issue and redeem voluntary license keys.

Payment happens outside Arctos. A maintainer runs ``generate_license.py``
after receiving payment, and an organizer enters the printed key. The
secret half of the key is hashed like a password before it is stored.
The redeem request is the only time the key is sent, and no response
returns it.
"""

from __future__ import annotations

import functools
import re
import secrets
from typing import NamedTuple

from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from werkzeug.security import check_password_hash, generate_password_hash

from app.domain.enums import TeamRegistrationStatus
from app.error_values import Err, Ok, Result
from app.exceptions import ArctosError, NotFoundError, ValidationError
from app.models.license import LICENSE_KIND_EVENT, LICENSE_KIND_LEAGUE, License
from app.utils.datetime_helpers import now_utc_naive
from models import League, TeamRegistration, Tournament, db

EVENT_PRICE_CENTS = 500
LEAGUE_PRICE_CENTS = 1000
MAX_TEAM_ALLOWANCE = 10_000
_KEY_ID_RE = re.compile(r"^[0-9a-f]{16}$")
_MAX_KEY_LENGTH = 200


class IssuedLicense(NamedTuple):
    """A newly stored license and the only copy of its key."""

    license_id: int
    key: str


class LicenseStatus(NamedTuple):
    """A page's license, and whether that license covers its confirmed teams."""

    licensed: bool
    team_allowance: int | None
    price_cents: int
    registered_teams: int

    @property
    def valid(self) -> bool:
        """True when an attached license covers every confirmed team."""
        return self.licensed and self.team_allowance is not None and self.team_allowance >= self.registered_teams


def format_dollars(cents: int) -> str:
    """Format a cent total as dollars, for the license script."""
    return f"${cents // 100}.{cents % 100:02d}"


@functools.cache
def _unused_password_hash() -> str:
    return generate_password_hash("license-key-timing-placeholder")


def _split_key(key: str) -> tuple[str, str] | None:
    text = key.strip()
    if not text or len(text) > _MAX_KEY_LENGTH or text.count(".") != 1:
        return None
    key_id, secret = text.split(".", 1)
    key_id = key_id.lower()
    if not secret or not _KEY_ID_RE.fullmatch(key_id):
        return None
    return key_id, secret


def _reject_unknown_key() -> None:
    check_password_hash(_unused_password_hash(), "not-the-placeholder")


def _article(word: str) -> str:
    return "an" if word[:1] in "aeiou" else "a"


def _team_count_error(team_count: object) -> ValidationError | None:
    if (
        isinstance(team_count, bool)
        or not isinstance(team_count, int)
        or team_count < 1
        or team_count > MAX_TEAM_ALLOWANCE
    ):
        return ValidationError(f"Team count must be between 1 and {MAX_TEAM_ALLOWANCE}.")
    return None


def _payment_error(amount_paid_cents: object) -> ValidationError | None:
    if isinstance(amount_paid_cents, bool) or not isinstance(amount_paid_cents, int) or amount_paid_cents < 0:
        return ValidationError("Payment must be zero or more cents.")
    return None


def parse_paid_cents(amount: str) -> Result[int, ArctosError]:
    """Parse a dollar amount such as ``40`` or ``40.50`` into cents."""
    text = amount.strip()
    if text.startswith("$"):
        text = text[1:].strip()
    match = re.fullmatch(r"(\d+)(?:\.(\d{1,2}))?", text)
    if match is None:
        return Err(ValidationError("Payment must be a dollar amount, such as 40 or 40.50."))
    fraction = match.group(2) or ""
    return Ok(int(match.group(1)) * 100 + int(fraction.ljust(2, "0")))


class LicenseService:
    """Create license keys and attach each one to a single event or league."""

    @staticmethod
    def price_cents(kind: str) -> int:
        """Per-team price for a license kind.

        Args:
            kind: ``event`` or ``league``.

        Returns:
            The price in cents for one team.

        Raises:
            ValidationError: If ``kind`` is not a known license kind.
        """
        if kind == LICENSE_KIND_EVENT:
            return EVENT_PRICE_CENTS
        if kind == LICENSE_KIND_LEAGUE:
            return LEAGUE_PRICE_CENTS
        raise ValidationError("License kind must be event or league.")

    @staticmethod
    def issue(kind: str, team_count: int, amount_paid_cents: int) -> Result[IssuedLicense, ArctosError]:
        """Create an unused license and return its id and one-time key.

        The returned key is not stored. Callers must show it to the buyer
        immediately; it cannot be recovered later. ``license_id`` is the
        handle used to list or update the row afterwards. The payment is
        stored as given; it is not derived from the team count.

        Args:
            kind: ``event`` or ``league``.
            team_count: How many teams the license covers.
            amount_paid_cents: Money received for this license, in cents.

        Returns:
            The new license id and plaintext key, ``{key_id}.{secret}``,
            or a validation error.
        """
        if kind not in (LICENSE_KIND_EVENT, LICENSE_KIND_LEAGUE):
            return Err(ValidationError("License kind must be event or league."))
        error = _team_count_error(team_count)
        if error is not None:
            return Err(error)
        error = _payment_error(amount_paid_cents)
        if error is not None:
            return Err(error)
        for _attempt in range(5):
            key_id = secrets.token_hex(8)
            secret = secrets.token_urlsafe(32)
            row = License(
                key_id=key_id,
                key_hash=generate_password_hash(secret),
                kind=kind,
                amount_paid_cents=amount_paid_cents,
                team_allowance=team_count,
                created_at=now_utc_naive(),
            )
            try:
                db.session.add(row)
                db.session.commit()
            except IntegrityError:
                db.session.rollback()
                continue
            return Ok(IssuedLicense(row.id, f"{key_id}.{secret}"))
        return Err(ValidationError("Could not generate a license key."))

    @staticmethod
    def list_licenses() -> list[License]:
        """Every license, oldest id first.

        The key cannot be recovered from these rows.
        """
        return License.query.order_by(License.id.asc()).all()

    @staticmethod
    def update_license(
        license_id: int,
        *,
        team_count: int | None = None,
        amount_paid_cents: int | None = None,
    ) -> Result[License, ArctosError]:
        """Change the team allowance, the recorded payment, or both.

        Omitted fields stay as they are. This does not move the license
        between events.
        """
        if team_count is None and amount_paid_cents is None:
            return Err(ValidationError("Specify a team allowance, a payment, or both."))
        if team_count is not None:
            error = _team_count_error(team_count)
            if error is not None:
                return Err(error)
        if amount_paid_cents is not None:
            error = _payment_error(amount_paid_cents)
            if error is not None:
                return Err(error)
        row = db.session.get(License, license_id)
        if row is None:
            return Err(NotFoundError(f"No license with id {license_id}."))
        if team_count is not None:
            row.team_allowance = team_count
        if amount_paid_cents is not None:
            row.amount_paid_cents = amount_paid_cents
        db.session.commit()
        return Ok(row)

    @staticmethod
    def redeem_event(tournament_url: str, key: str) -> Result[License, ArctosError]:
        """Attach a key to a standalone event.

        The returned row is the stored license. Its hash is not the key.
        """
        tournament = Tournament.query.filter_by(url=tournament_url).first()
        if tournament is None:
            return Err(NotFoundError("Event not found"))
        if tournament.league_id:
            return Err(
                ValidationError(
                    "This event is part of a league. Buy a league license and enter it in the league settings."
                )
            )
        return LicenseService._redeem(LICENSE_KIND_EVENT, key, tournament_url=tournament_url, league_url=None)

    @staticmethod
    def redeem_league(league_url: str, key: str) -> Result[License, ArctosError]:
        """Attach a key to a league."""
        if League.query.filter_by(url=league_url).first() is None:
            return Err(NotFoundError("League not found"))
        return LicenseService._redeem(LICENSE_KIND_LEAGUE, key, tournament_url=None, league_url=league_url)

    @staticmethod
    def clear_event(tournament_url: str) -> Result[None, ArctosError]:
        """Detach the license currently entered on an event, if any."""
        LicenseService.release_event(tournament_url)
        db.session.commit()
        return Ok(None)

    @staticmethod
    def clear_league(league_url: str) -> Result[None, ArctosError]:
        """Detach the license currently entered on a league, if any."""
        LicenseService.release_league(league_url)
        db.session.commit()
        return Ok(None)

    @staticmethod
    def release_event(tournament_url: str) -> None:
        """Detach an event's license without committing.

        Used when the event itself is being deleted, so the key can be
        entered somewhere else. The caller commits.
        """
        LicenseService._detach(License.query.filter_by(tournament_url=tournament_url).all())

    @staticmethod
    def release_league(league_url: str) -> None:
        """Detach a league's license without committing."""
        LicenseService._detach(License.query.filter_by(league_url=league_url).all())

    @staticmethod
    def status_for_tournament(tournament) -> LicenseStatus:
        """License state for an event page.

        A league event reports the league's license. The price is the
        price of the license that belongs on that page.
        """
        if tournament.league_id:
            licensed, allowance = LicenseService._status(league_url=tournament.league_id)
            registered = LicenseService.confirmed_team_count(league_url=tournament.league_id)
            return LicenseStatus(licensed, allowance, LEAGUE_PRICE_CENTS, registered)
        licensed, allowance = LicenseService._status(tournament_url=tournament.url)
        registered = LicenseService.confirmed_team_count(event_url=tournament.url)
        return LicenseStatus(licensed, allowance, EVENT_PRICE_CENTS, registered)

    @staticmethod
    def status_for_league(league_url: str) -> LicenseStatus:
        """License state for a league page."""
        licensed, allowance = LicenseService._status(league_url=league_url)
        registered = LicenseService.confirmed_team_count(league_url=league_url)
        return LicenseStatus(licensed, allowance, LEAGUE_PRICE_CENTS, registered)

    @staticmethod
    def confirmed_team_count(*, event_url: str | None = None, league_url: str | None = None) -> int:
        """Confirmed team registrations for one standalone event or one league.

        Cancelled registrations are not counted. A league event uses the
        league's registrations, not rows stored against the event itself.
        """
        query = TeamRegistration.query.filter(TeamRegistration.status == TeamRegistrationStatus.CONFIRMED)
        if league_url is not None:
            query = query.filter(TeamRegistration.league_id == league_url)
        else:
            query = query.filter(TeamRegistration.event == event_url)
        return int(query.count())

    @staticmethod
    def allowances_by_holder(
        tournament_urls: set[str],
        league_urls: set[str],
    ) -> tuple[dict[str, int], dict[str, int]]:
        """Team allowances for the given events and leagues that have a license.

        One query covers a whole page. Empty inputs do not touch the
        database. A url is absent when no license is attached.
        """
        tournament_urls = {url for url in tournament_urls if url}
        league_urls = {url for url in league_urls if url}
        if not tournament_urls and not league_urls:
            return {}, {}
        query = db.session.query(License.tournament_url, License.league_url, License.team_allowance)
        if tournament_urls and league_urls:
            query = query.filter(or_(License.tournament_url.in_(tournament_urls), License.league_url.in_(league_urls)))
        elif tournament_urls:
            query = query.filter(License.tournament_url.in_(tournament_urls))
        else:
            query = query.filter(License.league_url.in_(league_urls))
        event_allowances: dict[str, int] = {}
        league_allowances: dict[str, int] = {}
        for tournament_url, league_url, allowance in query.all():
            if tournament_url:
                event_allowances[tournament_url] = allowance
            if league_url:
                league_allowances[league_url] = allowance
        return event_allowances, league_allowances

    @staticmethod
    def _status(*, tournament_url: str | None = None, league_url: str | None = None) -> tuple[bool, int | None]:
        if tournament_url is not None:
            row = License.query.filter_by(tournament_url=tournament_url).first()
        else:
            row = License.query.filter_by(league_url=league_url).first()
        if row is None:
            return False, None
        return True, row.team_allowance

    @staticmethod
    def _detach(rows: list[License]) -> None:
        for row in rows:
            row.tournament_url = None
            row.league_url = None
            row.redeemed_at = None

    @staticmethod
    def _row_for_key(key: str) -> License | None:
        parsed = _split_key(key)
        if parsed is None:
            _reject_unknown_key()
            return None
        key_id, secret = parsed
        row = License.query.filter_by(key_id=key_id).first()
        if row is None or not row.key_hash:
            _reject_unknown_key()
            return None
        if not check_password_hash(row.key_hash, secret):
            return None
        return row

    @staticmethod
    def _redeem(
        kind: str,
        key: str,
        *,
        tournament_url: str | None,
        league_url: str | None,
    ) -> Result[License, ArctosError]:
        row = LicenseService._row_for_key(key)
        if row is None:
            return Err(ValidationError("That license key is not valid."))
        noun = "league" if kind == LICENSE_KIND_LEAGUE else "event"
        if row.kind != kind:
            other = "league" if row.kind == LICENSE_KIND_LEAGUE else "event"
            message = f"This license is for {_article(other)} {other}, not {_article(noun)} {noun}."
            return Err(ValidationError(message))
        if tournament_url is not None and row.tournament_url == tournament_url:
            return Ok(row)
        if league_url is not None and row.league_url == league_url:
            return Ok(row)
        if row.tournament_url or row.league_url:
            return Err(ValidationError(f"This license is already used on another {noun}."))
        holder = {"tournament_url": tournament_url} if tournament_url else {"league_url": league_url}
        existing = License.query.filter_by(**holder).first()
        if existing is not None and existing.id != row.id:
            return Err(
                ValidationError(f"This {noun} already has a license. Remove it before entering a different key.")
            )
        license_id = row.id
        claimed = License.query.filter(
            License.id == license_id,
            License.tournament_url.is_(None),
            License.league_url.is_(None),
        ).update(
            {
                "tournament_url": tournament_url,
                "league_url": league_url,
                "redeemed_at": now_utc_naive(),
            },
            synchronize_session=False,
        )
        if claimed != 1:
            db.session.rollback()
            fresh = db.session.get(License, license_id)
            if fresh is not None and (
                (tournament_url is not None and fresh.tournament_url == tournament_url)
                or (league_url is not None and fresh.league_url == league_url)
            ):
                return Ok(fresh)
            return Err(ValidationError(f"This license is already used on another {noun}."))
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            return Err(
                ValidationError(f"This {noun} already has a license. Remove it before entering a different key.")
            )
        fresh = db.session.get(License, license_id)
        if fresh is None:
            return Err(ValidationError("That license key is not valid."))
        return Ok(fresh)
