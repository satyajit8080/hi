"""Tier gating and the billing adapter boundary.

Free vs Pro is enforced in one place so the rules can be stated plainly on the
pricing page and be true:

    Free  BTC only, signals delayed 60 minutes, 30 days of history, no alerts.
    Pro   BTC + ETH + SOL in real time, every timeframe, alerts, full history,
          analytics, and an API key. $10/month.

The public track record is *not* gated. Anyone can verify the record without an
account, because a paywalled proof is not a proof.
"""
from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal

from app.config import settings

Tier = Literal["free", "pro"]


@dataclass(slots=True)
class TierPolicy:
    symbols: tuple[str, ...] | None      # None = all
    delay: timedelta
    history: timedelta | None
    alerts: bool
    timeframes: tuple[str, ...] | None
    api_access: bool
    analytics: bool


POLICIES: dict[str, TierPolicy] = {
    "free": TierPolicy(
        symbols=settings.free_tier_symbols,
        delay=timedelta(minutes=settings.free_tier_delay_minutes),
        history=timedelta(days=settings.free_tier_history_days),
        alerts=False,
        timeframes=("1h", "4h"),
        api_access=False,
        analytics=False,
    ),
    "pro": TierPolicy(
        symbols=None,
        delay=timedelta(0),
        history=None,
        alerts=True,
        timeframes=None,
        api_access=True,
        analytics=True,
    ),
}


def policy_for(tier: str | None) -> TierPolicy:
    return POLICIES.get(tier or "free", POLICIES["free"])


def visible_cutoff(tier: str | None) -> datetime:
    """Newest publish time a tier may see."""
    return datetime.now(timezone.utc) - policy_for(tier).delay


def history_floor(tier: str | None) -> datetime | None:
    p = policy_for(tier)
    return None if p.history is None else datetime.now(timezone.utc) - p.history


def allowed_symbols(tier: str | None) -> tuple[str, ...] | None:
    return policy_for(tier).symbols


def redact_for_tier(signal: dict, tier: str | None) -> dict:
    """Hide the actionable levels on a signal a free user cannot see yet.

    The signal's existence, direction and reasoning stay visible — that is the
    upgrade prompt. What is withheld is the tradeable detail, and the response
    says so rather than silently returning nulls.
    """
    p = policy_for(tier)
    if p.delay == timedelta(0):
        return signal
    published = signal.get("published_at")
    if isinstance(published, str):
        published = datetime.fromisoformat(published)
    if published and published <= datetime.now(timezone.utc) - p.delay:
        return signal

    out = dict(signal)
    for field in ("entry", "stop_loss", "tp1", "tp2", "tp3"):
        out[field] = None
    out["locked"] = True
    out["locked_reason"] = (
        f"Levels unlock {int(p.delay.total_seconds() // 60)} minutes after publication on the free "
        "plan. Pro sees them the moment the signal is generated."
    )
    return out


def tier_summary(tier: str | None) -> dict:
    p = policy_for(tier)
    return {
        "tier": tier or "free",
        "symbols": list(p.symbols) if p.symbols else ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
        "delay_minutes": int(p.delay.total_seconds() // 60),
        "history_days": None if p.history is None else p.history.days,
        "alerts": p.alerts,
        "timeframes": list(p.timeframes) if p.timeframes else ["5m", "15m", "1h", "4h", "1d"],
        "api_access": p.api_access,
        "analytics": p.analytics,
        "price_usd": 0 if (tier or "free") == "free" else settings.pro_price_usd,
    }


# ─────────────────────────── billing adapter ────────────────────────────────


class BillingProvider:
    """Provider-agnostic surface. Credentials live in the environment only.

    With `SP_BILLING_PROVIDER=none` the app runs fully; checkout returns a
    not-configured response instead of failing, so the product is developable
    without a payment account.
    """

    def __init__(self, name: str | None = None) -> None:
        self.name = name or settings.billing_provider

    @property
    def configured(self) -> bool:
        return self.name != "none"

    def checkout_payload(self, user_id: str, email: str) -> dict:
        if not self.configured:
            return {
                "configured": False,
                "message": (
                    "No payment provider is configured. Set SP_BILLING_PROVIDER and the matching "
                    "credentials to enable checkout."
                ),
            }
        return {
            "configured": True,
            "provider": self.name,
            "price_usd": settings.pro_price_usd,
            "client_reference_id": user_id,
            "customer_email": email,
        }

    def verify_webhook(self, provider: str, body: bytes, signature: str, secret: str) -> bool:
        """HMAC comparison shared by Stripe, Razorpay and Paddle webhook styles."""
        if not secret or not signature:
            return False
        digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        candidates = [digest]
        if "," in signature:  # Stripe-style "t=...,v1=..."
            for part in signature.split(","):
                if "=" in part:
                    candidates.append(part.split("=", 1)[1])
        return any(hmac.compare_digest(digest, c) for c in candidates)

    @staticmethod
    def status_from_event(event_type: str) -> str | None:
        mapping = {
            "checkout.session.completed": "active",
            "customer.subscription.created": "active",
            "customer.subscription.updated": "active",
            "customer.subscription.deleted": "canceled",
            "invoice.payment_failed": "past_due",
            "subscription.activated": "active",
            "subscription.charged": "active",
            "subscription.cancelled": "canceled",
            "subscription.halted": "past_due",
        }
        return mapping.get(event_type)
