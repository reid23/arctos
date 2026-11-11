"""Voluntary license keys for standalone events."""

from flask import request

from app.services.license_service import LicenseService
from app.utils.decorators import require_tournament_organizer
from app.utils.responses import json_error
from app.utils.result_helpers import json_from_result

from . import bp


def _entered_key():
    body = request.get_json(silent=True) or {}
    key = body.get("key") if isinstance(body, dict) else None
    if not isinstance(key, str) or not key.strip():
        return None
    return key


def _grant(license_row) -> dict:
    return {
        "team_allowance": license_row.team_allowance,
        "amount_paid_cents": license_row.amount_paid_cents,
    }


@bp.route("/<tournament_url>/license", methods=["POST"])
@require_tournament_organizer()
def redeem_tournament_license(tournament_url):
    """Attach a license key to this standalone event."""
    key = _entered_key()
    if key is None:
        return json_error("Enter a license key.")
    return json_from_result(LicenseService.redeem_event(tournament_url, key), ok_to_payload=_grant)


@bp.route("/<tournament_url>/license", methods=["DELETE"])
@require_tournament_organizer()
def clear_tournament_license(tournament_url):
    """Remove the license entered on this event so the key can be used elsewhere."""
    return json_from_result(LicenseService.clear_event(tournament_url), ok_to_payload=lambda _cleared: {})
