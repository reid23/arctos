"""League model.

Defines :class:`League` - the optional parent of a
:class:`~app.models.tournament.Tournament`.

League tournaments inherit the league's :class:`~app.models.registrable_config.RegistrableConfig`
instead of owning one directly.
"""

from __future__ import annotations

from app.models.base import db
from app.models.constants import AUTH_STRING_LEN, LONG_NAME_LEN, URL_SLUG_LEN


class League(db.Model):
    """A Jugger league grouping multiple tournaments under a single registration.

    TOs create a new ``League`` for each season.  All tournaments that belong
    to the league share the league's
    :class:`~app.models.registrable_config.RegistrableConfig` (registration
    fees, open/close toggle, waiver, etc.) rather than maintaining individual
    configs.

    Attributes:
        url: URL slug used as the primary key and public identifier.
        name: Human-readable league name.
        banner_photo: Static-relative path to an optional organizer-uploaded banner.
        about: Markdown description shown on the league homepage.
        published: Whether the league is publicly visible.
        registrable_config_id: FK to the shared
            :class:`~app.models.registrable_config.RegistrableConfig`.
        registrable_config: Relationship to the registration config object.
    """

    __tablename__ = "leagues"

    url = db.Column(db.String(URL_SLUG_LEN), primary_key=True)
    name = db.Column(db.String(LONG_NAME_LEN), nullable=False)
    banner_photo = db.Column(db.String(AUTH_STRING_LEN), nullable=True)
    about = db.Column(db.Text)
    published = db.Column(db.Boolean, default=False, nullable=False)

    registrable_config_id = db.Column(
        db.Integer,
        db.ForeignKey("registrable_configs.id", ondelete="CASCADE"),
        nullable=False,
    )
    registrable_config = db.relationship(
        "RegistrableConfig",
        backref="leagues",
        foreign_keys=[registrable_config_id],
    )
