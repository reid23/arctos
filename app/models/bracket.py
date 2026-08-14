"""SQLAlchemy models for interactive bracket canvas elements."""

from __future__ import annotations

import uuid

from app.domain.enums import BracketPortMode
from app.models.base import db
from app.models.constants import LONG_NAME_LEN, LONG_URL_LEN, SHORT_LABEL_LEN, URL_SLUG_LEN, UUID_LEN

# Default match-block size on the bracket canvas (CSS pixels).
DEFAULT_BRACKET_WIDTH: float = 280.0
DEFAULT_BRACKET_HEIGHT: float = 100.0
DEFAULT_TEXT_SIZE: float = 18.0
DEFAULT_IMAGE_WIDTH: float = 240.0
DEFAULT_IMAGE_HEIGHT: float = 160.0
DEFAULT_LABELED_TEAM_WIDTH: float = 200.0
DEFAULT_LABELED_TEAM_HEIGHT: float = 40.0


class BracketPlacement(db.Model):
    """Layout and port-mode data for one match on a tournament bracket canvas.

    Each row ties a :class:`~app.models.match.Match` to a position on the
    open-canvas bracket editor.  When :attr:`x_pos` / :attr:`y_pos` are
    ``None`` the match exists in the schedule but is not currently placed
    on the canvas.

    :attr:`team1` / :attr:`team2` control whether each input is shown as a
    net-style label (the match's ``teamN_initial``) or as a wire (NET)
    connected to another match's winner/loser output.

    :attr:`inputs_flipped` swaps the vertical order of the two inputs on the
    canvas (team2 on top, team1 on bottom) without changing match data.
    Winner/loser *outputs* stay top/bottom respectively.
    """

    __tablename__ = "bracket_placements"

    event = db.Column(
        db.String(URL_SLUG_LEN),
        db.ForeignKey("tournaments.url", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    )
    match = db.Column(
        db.String(UUID_LEN),
        db.ForeignKey("matches.uuid", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    )
    x_pos = db.Column(db.Float, nullable=True)
    y_pos = db.Column(db.Float, nullable=True)
    width = db.Column(db.Float, nullable=False, default=DEFAULT_BRACKET_WIDTH)
    height = db.Column(db.Float, nullable=False, default=DEFAULT_BRACKET_HEIGHT)
    team1 = db.Column(
        db.Enum(BracketPortMode),
        nullable=False,
        default=BracketPortMode.LABEL,
    )
    team2 = db.Column(
        db.Enum(BracketPortMode),
        nullable=False,
        default=BracketPortMode.LABEL,
    )
    inputs_flipped = db.Column(db.Boolean, nullable=False, default=False)

    match_obj = db.relationship(
        "Match",
        foreign_keys=[match],
        backref=db.backref("bracket_placement", uselist=False),
    )
    tournament = db.relationship(
        "Tournament",
        foreign_keys=[event],
        backref=db.backref("bracket_placements", lazy="dynamic", cascade="all, delete-orphan"),
    )

    @property
    def is_placed(self) -> bool:
        """True when both coordinates are set (match is on the canvas)."""
        return self.x_pos is not None and self.y_pos is not None


class BracketText(db.Model):
    """A free-text annotation on the bracket canvas."""

    __tablename__ = "bracket_texts"

    id = db.Column(db.String(UUID_LEN), primary_key=True, default=lambda: str(uuid.uuid4()))
    event = db.Column(
        db.String(URL_SLUG_LEN),
        db.ForeignKey("tournaments.url", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    text = db.Column(db.Text, nullable=False, default="")
    x_pos = db.Column(db.Float, nullable=False, default=40.0)
    y_pos = db.Column(db.Float, nullable=False, default=40.0)
    size = db.Column(db.Float, nullable=False, default=DEFAULT_TEXT_SIZE)


class BracketLabeledTeam(db.Model):
    """A standalone team slot on the bracket canvas (label or wire input).

    ``team`` holds the same kind of token used in match ``_initial`` fields:
    an explicit team id, ``tag::Name``, or ``MatchName::winner`` / ``::loser``.
    ``kind`` chooses LABEL (net label) vs NET (wire from a match output).
    ``label`` is the short caption shown until the team resolves; once known
    the canvas displays ``{label}: [pfp] teamname``.
    """

    __tablename__ = "bracket_labeled_teams"

    id = db.Column(db.String(UUID_LEN), primary_key=True, default=lambda: str(uuid.uuid4()))
    event = db.Column(
        db.String(URL_SLUG_LEN),
        db.ForeignKey("tournaments.url", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    label = db.Column(db.String(SHORT_LABEL_LEN), nullable=False, default="")
    team = db.Column(db.String(LONG_NAME_LEN), nullable=False, default="")
    kind = db.Column(
        db.Enum(BracketPortMode),
        nullable=False,
        default=BracketPortMode.LABEL,
    )
    x_pos = db.Column(db.Float, nullable=False, default=40.0)
    y_pos = db.Column(db.Float, nullable=False, default=40.0)


class BracketImage(db.Model):
    """An uploaded image placed on the bracket canvas."""

    __tablename__ = "bracket_images"

    id = db.Column(db.String(UUID_LEN), primary_key=True, default=lambda: str(uuid.uuid4()))
    event = db.Column(
        db.String(URL_SLUG_LEN),
        db.ForeignKey("tournaments.url", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    image = db.Column(db.String(LONG_URL_LEN), nullable=False, default="")
    x_pos = db.Column(db.Float, nullable=False, default=40.0)
    y_pos = db.Column(db.Float, nullable=False, default=40.0)
    width = db.Column(db.Float, nullable=False, default=DEFAULT_IMAGE_WIDTH)
    height = db.Column(db.Float, nullable=False, default=DEFAULT_IMAGE_HEIGHT)
