"""JSON shapes for payout accounts and an event's or league's online payment settings."""

from __future__ import annotations

from app.services import payout_account_service
from app.utils.stripe_gateway import gateway
from models import PayoutAccount, RegistrableConfig


def payout_account_payload(account: PayoutAccount | None) -> dict | None:
    if account is None:
        return None
    return {
        "id": account.id,
        "owner": {"id": account.owner_player, "name": account.owner.name},
        "charges_enabled": account.charges_enabled,
        "payouts_enabled": account.payouts_enabled,
        "details_submitted": account.details_submitted,
    }


def online_payments_payload(config: RegistrableConfig, own_account: PayoutAccount | None) -> dict:
    return {
        "available": gateway() is not None,
        "enabled": config.online_payments_enabled,
        "accepting": payout_account_service.accepts_online_payments(config),
        "account": payout_account_payload(config.payout_account),
        "own_account": payout_account_payload(own_account),
    }
