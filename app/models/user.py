"""Player and Team account models.

Defines :class:`Player` (individual user accounts) and :class:`Team`
(team accounts that register for events).

Both subclass :class:`flask_login.UserMixin`, so Flask-Login can authenticate either
shape with the same machinery; both also support either password or
Google OAuth login.
"""

from __future__ import annotations

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from app.models.base import db
from app.models.constants import (
    AUTH_STRING_LEN,
    LONG_NAME_LEN,
    PHONE_LEN,
    SHORT_NAME_LEN,
    USER_ID_LEN,
)


class Player(UserMixin, db.Model):
    """An individual player account.

    Players can log in with a password or via Google OAuth.  They may be
    Tournament Organisers (TOs) and can register for events independently
    or as part of a team.

    Attributes:
        id: Unique username / slug (primary key).
        name: Display name.
        pw_hash: Werkzeug password hash; ``None`` for OAuth-only accounts.
        google_id: Google OAuth subject identifier.
        email: Contact email address. Captured from Google OAuth on
            account creation but never used to authenticate the user —
            login is by ``id`` + password or by ``google_id``. Not
            unique: two players may share an address (e.g. shared club
            inbox).
        phone: Optional contact phone number.
        profile_photo: Server-relative path to the uploaded profile image.
        banner_photo: Server-relative path to the optional profile banner.
        bio: Free-text biography.
        location: Player's city or region.
        pronouns: Optional publicly displayed pronouns.
        pompfen: Canonical names of the player's selected pompfen roles.
    """

    __tablename__ = "players"

    id = db.Column(db.String(USER_ID_LEN), primary_key=True)
    name = db.Column(db.String(SHORT_NAME_LEN), nullable=False)
    pw_hash = db.Column(db.String(AUTH_STRING_LEN), nullable=True)  # Nullable for Google OAuth users
    google_id = db.Column(db.String(AUTH_STRING_LEN), unique=True, nullable=True)  # Google OAuth ID
    email = db.Column(db.String(AUTH_STRING_LEN), nullable=True)
    phone = db.Column(db.String(PHONE_LEN))
    profile_photo = db.Column(db.String(AUTH_STRING_LEN))
    banner_photo = db.Column(db.String(AUTH_STRING_LEN), nullable=True)
    bio = db.Column(db.Text)
    location = db.Column(db.String(SHORT_NAME_LEN))
    pronouns = db.Column(db.String(64), nullable=True)
    pompfen = db.Column(db.JSON, nullable=False, default=list, server_default="[]")

    def set_password(self, password: str) -> None:
        """Hash *password* and store it in :attr:`pw_hash`.

        Args:
            password: The plaintext password to store securely.
        """
        self.pw_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        """Verify *password* against the stored hash.

        Args:
            password: The plaintext password to check.

        Returns:
            ``True`` if the password matches the stored hash, ``False`` if
            :attr:`pw_hash` is ``None`` (OAuth-only account) or the hash does
            not match.
        """
        if not self.pw_hash:
            return False
        return check_password_hash(self.pw_hash, password)


class Team(UserMixin, db.Model):
    """A team account that can register for events.

    Teams can log in with a password or via Google OAuth, just like players.
    A team's roster is determined by :class:`~app.models.registration.PlayerRegistration`
    records linking players to the team for each event.

    Attributes:
        id: Unique team username / slug (primary key).
        name: Display name of the team.
        pw_hash: Werkzeug password hash; ``None`` for OAuth-only accounts.
        google_id: Google OAuth subject identifier.
        phone: Optional contact phone number.
        email: Public contact email address, provided by the team owner.
            Never used to authenticate the team — login is by ``id`` +
            password or by ``google_id``. Not unique: multiple teams in
            the same club may share an inbox.
        icon: Base64-encoded team icon image.
        profile_photo: Server-relative path to the uploaded profile photo.
        banner_photo: Server-relative path to the optional profile banner.
        socials: Free-text social media links.
        website: Team website URL.
        location: Team's city or region.
        about: Free-text team biography / description.
    """

    __tablename__ = "teams"

    id = db.Column(db.String(USER_ID_LEN), primary_key=True)
    name = db.Column(db.String(SHORT_NAME_LEN), nullable=False)
    pw_hash = db.Column(db.String(AUTH_STRING_LEN), nullable=True)  # Nullable for Google OAuth users
    google_id = db.Column(db.String(AUTH_STRING_LEN), unique=True, nullable=True)  # Google OAuth ID
    phone = db.Column(db.String(PHONE_LEN))
    email = db.Column(db.String(AUTH_STRING_LEN), nullable=True)
    icon = db.Column(db.Text)  # base64 image
    profile_photo = db.Column(db.String(AUTH_STRING_LEN))  # Path to uploaded photo
    banner_photo = db.Column(db.String(AUTH_STRING_LEN), nullable=True)
    socials = db.Column(db.Text)
    website = db.Column(db.String(LONG_NAME_LEN))
    location = db.Column(db.String(SHORT_NAME_LEN))
    about = db.Column(db.Text)

    def set_password(self, password: str) -> None:
        """Hash *password* and store it in :attr:`pw_hash`.

        Args:
            password: The plaintext password to store securely.
        """
        self.pw_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        """Verify *password* against the stored hash.

        Args:
            password: The plaintext password to check.

        Returns:
            ``True`` if the password matches the stored hash, ``False`` if
            :attr:`pw_hash` is ``None`` (OAuth-only account) or the hash does
            not match.
        """
        if not self.pw_hash:
            return False
        return check_password_hash(self.pw_hash, password)
