"""Account, alerts and billing."""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, current_user, pro_user
from app.config import settings
from app.db import session_dep
from app.services.subscriptions import BillingProvider, tier_summary

router = APIRouter(prefix="/v1", tags=["account"])
billing = BillingProvider()

WEBHOOK_SECRETS = {
    "stripe": "STRIPE_WEBHOOK_SECRET",
    "razorpay": "RAZORPAY_WEBHOOK_SECRET",
    "paddle": "PADDLE_WEBHOOK_SECRET",
}


class AlertPref(BaseModel):
    channel: str = Field(pattern="^(telegram|webpush|email|discord)$")
    symbol: str = Field(pattern="^(BTCUSDT|ETHUSDT|SOLUSDT|ALL)$")
    timeframe: str
    direction: str = Field(pattern="^(LONG|SHORT|ALL)$")
    min_strength: int = Field(0, ge=0, le=100)
    enabled: bool = True


class ChannelBinding(BaseModel):
    channel: str = Field(pattern="^(telegram|webpush|email|discord)$")
    address: str = Field(min_length=1, max_length=500)


@router.get("/me/alerts")
async def get_alerts(user: CurrentUser = Depends(current_user), session: AsyncSession = Depends(session_dep)):
    prefs = await session.execute(
        text(
            """
            SELECT id, channel, symbol, timeframe, direction, min_strength, enabled
            FROM alert_prefs WHERE user_id = :uid ORDER BY id
            """
        ),
        {"uid": user.id},
    )
    channels = await session.execute(
        text("SELECT channel, address, verified FROM alert_channels WHERE user_id = :uid"),
        {"uid": user.id},
    )
    return {
        "preferences": [dict(r._mapping) for r in prefs],
        "channels": [dict(r._mapping) for r in channels],
        "alerts_available": tier_summary(user.tier)["alerts"],
        "note": (
            "One alert per signal per channel. Take-profit and stop updates are off by default so "
            "a single setup cannot turn into five notifications."
        ),
    }


@router.put("/me/alerts")
async def set_alerts(
    prefs: list[AlertPref],
    user: CurrentUser = Depends(pro_user),
    session: AsyncSession = Depends(session_dep),
):
    await session.execute(text("DELETE FROM alert_prefs WHERE user_id = :uid"), {"uid": user.id})
    for p in prefs:
        await session.execute(
            text(
                """
                INSERT INTO alert_prefs (user_id, channel, symbol, timeframe, direction,
                                         min_strength, enabled)
                VALUES (:uid, :channel, :symbol, :timeframe, :direction, :min_strength, :enabled)
                ON CONFLICT (user_id, channel, symbol, timeframe, direction)
                DO UPDATE SET min_strength = EXCLUDED.min_strength, enabled = EXCLUDED.enabled
                """
            ),
            {"uid": user.id, **p.model_dump()},
        )
    await session.commit()
    return {"saved": len(prefs)}


@router.put("/me/channels")
async def bind_channel(
    body: ChannelBinding,
    user: CurrentUser = Depends(pro_user),
    session: AsyncSession = Depends(session_dep),
):
    await session.execute(
        text(
            """
            INSERT INTO alert_channels (user_id, channel, address, verified)
            VALUES (:uid, :channel, :address, true)
            ON CONFLICT (user_id, channel) DO UPDATE SET address = EXCLUDED.address
            """
        ),
        {"uid": user.id, **body.model_dump()},
    )
    await session.commit()
    return {"channel": body.channel, "address": body.address, "verified": True}


@router.delete("/me", status_code=204)
async def deactivate(user: CurrentUser = Depends(current_user), session: AsyncSession = Depends(session_dep)):
    """Deactivating an account never touches the signal ledger."""
    await session.execute(text("UPDATE users SET is_active = false WHERE id = :uid"), {"uid": user.id})
    await session.execute(
        text("INSERT INTO audit_log (actor, action, entity, entity_id) VALUES (:a, 'deactivate', 'user', :id)"),
        {"a": user.email, "id": user.id},
    )
    await session.commit()


@router.post("/billing/checkout")
async def checkout(user: CurrentUser = Depends(current_user)):
    payload = billing.checkout_payload(user.id, user.email)
    if not payload["configured"]:
        return {
            **payload,
            "price_usd": settings.pro_price_usd,
            "setup_hint": "See README → Enabling payments.",
        }
    return payload


@router.post("/billing/webhook/{provider}")
async def webhook(
    provider: str,
    request: Request,
    signature: str | None = Header(default=None, alias="X-Signature"),
    session: AsyncSession = Depends(session_dep),
):
    """Signature-verified, idempotent by (provider, event_id)."""
    if provider not in WEBHOOK_SECRETS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unknown payment provider.")

    import os

    secret = os.environ.get(WEBHOOK_SECRETS[provider], "")
    body = await request.body()
    if not billing.verify_webhook(provider, body, signature or "", secret):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Webhook signature check failed.")

    event = json.loads(body or b"{}")
    event_id = str(event.get("id") or event.get("event_id") or "")
    event_type = str(event.get("type") or event.get("event") or "")
    if not event_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Webhook is missing an event id.")

    inserted = await session.execute(
        text(
            """
            INSERT INTO billing_events (provider, event_id, event_type, payload)
            VALUES (:p, :eid, :etype, CAST(:payload AS jsonb))
            ON CONFLICT (provider, event_id) DO NOTHING
            """
        ),
        {"p": provider, "eid": event_id, "etype": event_type, "payload": json.dumps(event)},
    )
    if (inserted.rowcount or 0) == 0:
        return {"status": "duplicate_ignored", "event_id": event_id}

    new_status = BillingProvider.status_from_event(event_type)
    reference = (
        event.get("client_reference_id")
        or event.get("data", {}).get("client_reference_id")
        or event.get("data", {}).get("object", {}).get("client_reference_id")
    )
    if new_status and reference:
        await session.execute(
            text(
                """
                UPDATE subscriptions
                   SET status = :status, provider = :provider, updated_at = now()
                 WHERE user_id = :uid
                """
            ),
            {"status": new_status, "provider": provider, "uid": reference},
        )
        await session.execute(
            text("UPDATE users SET tier = :tier WHERE id = :uid"),
            {"tier": "pro" if new_status == "active" else "free", "uid": reference},
        )
    await session.commit()
    return {"status": "processed", "event_id": event_id, "subscription_status": new_status}


@router.get("/pricing")
async def pricing():
    return {
        "currency": "USD",
        "plans": [
            {
                "id": "free",
                "name": "Free",
                "price": 0,
                "features": [
                    "Bitcoin signals only",
                    f"Levels unlock {settings.free_tier_delay_minutes} minutes after publication",
                    f"{settings.free_tier_history_days} days of signal history",
                    "1h and 4h timeframes",
                    "Full public track record and verification",
                ],
            },
            {
                "id": "pro",
                "name": "Pro",
                "price": settings.pro_price_usd,
                "interval": "month",
                "features": [
                    "BTC, ETH and SOL in real time",
                    "Every timeframe, 5m through 1d",
                    "Telegram and browser alerts with your own filters",
                    "Complete signal history and replay",
                    "Performance analytics, calibration and drawdown",
                    "API key for the read API",
                ],
            },
        ],
        "always_free": [
            "The public track record",
            "Every closed signal, including losses",
            "Hash and chain verification",
            "CSV export of the full signal log",
        ],
        "note": "The proof is free. The product is the speed and the filtering.",
    }
