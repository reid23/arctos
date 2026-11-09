"""Column-length constants for SQLAlchemy model definitions.

Every ``db.Column(db.String(N))`` in :mod:`app.models` should pull
``N`` from a constant here rather than hard-coding a literal. Grouping
the lengths in one place keeps related FK columns the same width as
the PKs they reference and makes "widen this column" a one-line edit
plus a migration.
"""

from typing import Final

# ---------------------------------------------------------------------------
# Identifiers (primary keys + their FK mirror columns)
# ---------------------------------------------------------------------------

URL_SLUG_LEN: Final[int] = 100
"""Slug-shaped primary keys (``Tournament.url``, ``League.url``) and all
``event`` / ``league_id`` foreign-key columns that reference them."""

USER_ID_LEN: Final[int] = 50
"""``Player.id`` / ``Team.id`` primary keys and every foreign-key column
that references a player or team (e.g. ``TO.user_id``, ``Match.team1``)."""

UUID_LEN: Final[int] = 36
"""UUID4 string representation, always 36 characters including hyphens.
Used for ``Match.uuid``, ``Point.uuid``, ``Camera.uuid``, and their FK
mirror columns."""

# ---------------------------------------------------------------------------
# Human-readable display text
# ---------------------------------------------------------------------------

LONG_NAME_LEN: Final[int] = 200
"""Longer display names: ``Tournament.name``, ``League.name``,
``Match.name``, ``Camera.name``, ``Match.team1_initial`` /
``team2_initial`` (ASS expressions), ``Tournament.location``,
``Team.website``."""

SHORT_NAME_LEN: Final[int] = 100
"""Shorter display names: ``Player.name``, ``Team.name``,
``TeamRegistration.pseudonym``, ``PlayerRegistration.jersey_name``,
``Field.name``, ``SideComp.name``, ``Player.location``,
``Team.location``, payment reference strings."""

SHORT_LABEL_LEN: Final[int] = 50
"""Short labels and enumerated strings: ``PenaltyType.name``,
``Payment.method``, ``Camera.source_type``,
``Camera.status``, ``SideComp.type``."""

TAG_NAME_LEN: Final[int] = 50
"""Tag name strings used in ASS (Arctos Schedule Script) expressions.
Kept shorter than display names to stay practical as identifiers."""

SHORTNAME_LEN: Final[int] = 8
"""Optional short team display alias used in space-constrained UI
(schedule cells, bracket lines, match cards). Stored on
``TeamRegistration.shortname``."""

# ---------------------------------------------------------------------------
# Auth / external credentials
# ---------------------------------------------------------------------------

AUTH_STRING_LEN: Final[int] = 255
"""Werkzeug password hashes, Google OAuth subject IDs, email addresses,
and profile photo server paths — all originate from external systems
and may be long."""

PHONE_LEN: Final[int] = 20
"""Phone number strings (E.164 is at most 15 digits; allows headroom
for formatting characters such as spaces and dashes)."""

# ---------------------------------------------------------------------------
# Short codes
# ---------------------------------------------------------------------------

SHORT_CODE_LEN: Final[int] = 10
"""Very short codes: ``PlayerRegistration.jersey_number``,
``TO.user_type`` (``"player"`` / ``"team"``),
``Camera.uploaded_by_user_type``,
``Point.winner`` (``"TEAM1"`` / ``"TEAM2"``)."""

# ---------------------------------------------------------------------------
# URLs and file paths
# ---------------------------------------------------------------------------

LONG_URL_LEN: Final[int] = 500
"""Long external URLs and server-side file paths: footage clip URLs,
Amazon S3 object keys, waiver file paths, YouTube video links, terms
and conditions page URLs."""

# ---------------------------------------------------------------------------
# Cryptographic / fixed-width encodings
# ---------------------------------------------------------------------------

SHA256_HEX_LEN: Final[int] = 64
"""SHA-256 digest encoded as lowercase hex: 32 bytes × 2 hex chars = 64.
Used for ``RegistrableConfig.waiver_sha256`` and
``PlayerRegistration.waiver_legal_name_signature_sha256``."""

HEX_COLOR_LEN: Final[int] = 6
"""CSS hex colour without the leading ``#`` character (e.g. ``"FF0000"``).
Used for ``PenaltyType.color``."""
