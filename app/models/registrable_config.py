"""Registration configuration shared by tournaments and leagues.

Defines :class:`RegistrableConfig` - the bundle of registration
settings (fees, caps, waiver, open/close toggles) that governs how a
tournament or league accepts entries. A standalone tournament owns its
config directly; a league tournament inherits its parent league's
config. Resolve the effective config for any tournament with
:func:`app.utils.helpers.get_registrable_config`.
"""

from __future__ import annotations

from app.models.base import db
from app.models.constants import AUTH_STRING_LEN, LONG_NAME_LEN, LONG_URL_LEN, SHA256_HEX_LEN


class RegistrableConfig(db.Model):  # type: ignore[misc]
    """Shared registration configuration for standalone tournaments and leagues.

    Standalone tournaments (``league_id`` is null) own their own config;
    league tournaments inherit the league's config.  Fee constraints are
    enforced at the database level.

    Attributes:
        id: Auto-increment primary key.
        team_reg_fee: Registration fee charged per team (≥ 0). Stored as
            an exact ``DECIMAL(10, 2)`` value — never as a binary float —
            so monetary arithmetic does not accumulate IEEE-754 rounding
            errors across many payments.
        player_reg_fee: Registration fee charged per player (≥ 0). Same
            ``DECIMAL(10, 2)`` storage rationale as ``team_reg_fee``.
        payment_info: Free-text payment instructions shown to registrants.
        team_registration_open: Whether team registration is currently
            accepting new entries.
        player_registration_open: Whether individual player registration is
            currently open.
        terms_link: URL to the tournament's terms and conditions page.
        waiver_filepath: Server-relative path to the uploaded waiver file.
        waiver_sha256: SHA-256 hex digest of the waiver file bytes.
        registration_form_url: Public Google Form responder link, or ``None``
            when a response is not required.
        registration_form_entry_id: Numeric question identifier used to prefill
            a player's registration code.
        registration_form_sheet_id: Private response spreadsheet identifier.
        registration_form_sheet_gid: Numeric response tab identifier; zero
            is a valid identifier.
        registration_form_code_column: Unique first-row heading for the
            column containing Arctos registration codes.
        registration_form_signing_key: Private key used to derive stable player
            codes for this configuration. Never included in API responses.
        n_max_teams: Cap on the number of registered teams, or ``None``.
        max_team_size_roster: Maximum players on a team's full roster.
        max_team_size_field: Maximum players allowed on the field at once.
    """

    __tablename__ = "registrable_configs"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    team_reg_fee = db.Column(db.Numeric(10, 2), default=0, nullable=False)
    player_reg_fee = db.Column(db.Numeric(10, 2), default=0, nullable=False)
    payment_info = db.Column(db.Text)
    # Separate toggles for team and player registration.
    team_registration_open = db.Column(db.Boolean, default=False, nullable=False)
    player_registration_open = db.Column(db.Boolean, default=False, nullable=False)
    terms_link = db.Column(db.String(LONG_URL_LEN))
    # Waiver file uploaded by TOs for this event (standalone tournament or league).
    # Stored as a relative filepath so the frontend can link consistently.
    waiver_filepath = db.Column(db.String(LONG_URL_LEN))
    # SHA-256 of the waiver file bytes (hex string).
    waiver_sha256 = db.Column(db.String(SHA256_HEX_LEN))
    # The form link is public; response-sheet details are organizer-only.
    registration_form_url = db.Column(db.String(LONG_URL_LEN), nullable=True)
    registration_form_entry_id = db.Column(db.String(20), nullable=True)
    registration_form_sheet_id = db.Column(db.String(AUTH_STRING_LEN), nullable=True)
    registration_form_sheet_gid = db.Column(db.Integer, nullable=True)
    registration_form_code_column = db.Column(db.String(LONG_NAME_LEN), nullable=True)
    registration_form_signing_key = db.Column(db.String(SHA256_HEX_LEN), nullable=True)
    n_max_teams = db.Column(db.Integer)
    max_team_size_roster = db.Column(db.Integer)
    max_team_size_field = db.Column(db.Integer)

    __table_args__ = (
        db.CheckConstraint("team_reg_fee >= 0", name="ck_registrable_config_team_reg_fee_nonneg"),
        db.CheckConstraint("player_reg_fee >= 0", name="ck_registrable_config_player_reg_fee_nonneg"),
    )
