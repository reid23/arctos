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

from app.error_values import Err, Ok, Result
from app.exceptions import ArctosError, NotFoundError, ValidationError
from app.models.license import LICENSE_KIND_EVENT, LICENSE_KIND_LEAGUE, License
from app.utils.datetime_helpers import now_utc_naive
from models import League, Tournament, db

EVENT_PRICE_CENTS = 500
LEAGUE_PRICE_CENTS = 1000
MAX_TEAM_ALLOWANCE = 10_000
_KEY_ID_RE = re.compile(r"^[0-9a-f]{16}$")
_MAX_KEY_LENGTH = 200


class LicenseStatus(NamedTuple):
    """Whether a page has a license, how many teams it covers, and its price."""

    licensed: bool
    team_allowance: int | None
    price_cents: int


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
    def issue(kind: str, team_count: int) -> Result[str, ArctosError]:
        """Create an unused license and return its one-time key.

        The returned key is not stored. Callers must show it to the buyer
        immediately; it cannot be recovered later.

        Args:
            kind: ``event`` or ``league``.
            team_count: How many teams the payment covers.

        Returns:
            The plaintext key, ``{key_id}.{secret}``, or a validation error.
        """
        if kind not in (LICENSE_KIND_EVENT, LICENSE_KIND_LEAGUE):
            return Err(ValidationError("License kind must be event or league."))
        if isinstance(team_count, bool) or not isinstance(team_count, int):
            return Err(ValidationError(f"Team count must be between 1 and {MAX_TEAM_ALLOWANCE}."))
        if team_count < 1 or team_count > MAX_TEAM_ALLOWANCE:
            return Err(ValidationError(f"Team count must be between 1 and {MAX_TEAM_ALLOWANCE}."))
        price = LicenseService.price_cents(kind)
        for _attempt in range(5):
            key_id = secrets.token_hex(8)
            secret = secrets.token_urlsafe(32)
            row = License(
                key_id=key_id,
                key_hash=generate_password_hash(secret),
                kind=kind,
                amount_paid_cents=price * team_count,
                team_allowance=team_count,
                created_at=now_utc_naive(),
            )
            try:
                db.session.add(row)
                db.session.commit()
            except IntegrityError:
                db.session.rollback()
                continue
            return Ok(f"{key_id}.{secret}")
        return Err(ValidationError("Could not generate a license key."))

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
            return LicenseStatus(licensed, allowance, LEAGUE_PRICE_CENTS)
        licensed, allowance = LicenseService._status(tournament_url=tournament.url)
        return LicenseStatus(licensed, allowance, EVENT_PRICE_CENTS)

    @staticmethod
    def status_for_league(league_url: str) -> LicenseStatus:
        """License state for a league page."""
        licensed, allowance = LicenseService._status(league_url=league_url)
        return LicenseStatus(licensed, allowance, LEAGUE_PRICE_CENTS)

    @staticmethod
    def redeemed_url_sets(
        tournament_urls: set[str],
        league_urls: set[str],
    ) -> tuple[set[str], set[str]]:
        """Return the given urls that currently have a license attached.

        One query covers a whole page of events or leagues. Empty inputs
        do not touch the database.
        """
        tournament_urls = {url for url in tournament_urls if url}
        league_urls = {url for url in league_urls if url}
        if not tournament_urls and not league_urls:
            return set(), set()
        query = db.session.query(License.tournament_url, License.league_url)
        if tournament_urls and league_urls:
            query = query.filter(or_(License.tournament_url.in_(tournament_urls), License.league_url.in_(league_urls)))
        elif tournament_urls:
            query = query.filter(License.tournament_url.in_(tournament_urls))
        else:
            query = query.filter(License.league_url.in_(league_urls))
        redeemed_events: set[str] = set()
        redeemed_leagues: set[str] = set()
        for tournament_url, league_url in query.all():
            if tournament_url:
                redeemed_events.add(tournament_url)
            if league_url:
                redeemed_leagues.add(league_url)
        return redeemed_events, redeemed_leagues

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
