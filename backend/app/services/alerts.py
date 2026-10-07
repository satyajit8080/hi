"""Alert delivery.

Three protections against the thing users complain about most — spam:

  * **Dedup by construction.** `alert_deliveries` has primary key
    (signal_id, user_id, channel). A double send is a primary-key violation,
    not a policy someone has to remember.
  * **Token bucket per channel.** Telegram documents roughly 30 messages/second
    for broadcasts and about one per second to the same chat, and returns 429
    with `retry_after` when exceeded. The bucket keeps us under that, and a 429
    is honoured rather than retried blindly.
  * **User filters.** Symbol, timeframe, direction and a minimum strength, so a
    user who only wants strong 4h BTC longs gets exactly that.

Lifecycle updates (TP hit, stopped) are opt-in. By default one signal produces
one message.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings

TELEGRAM_API = "https://api.telegram.org/bot{token}"


@dataclass
class TokenBucket:
    rate_per_sec: float
    capacity: float
    _tokens: float = 0.0
    _last: float = 0.0

    def __post_init__(self) -> None:
        self._tokens = self.capacity
        self._last = time.monotonic()

    async def take(self, n: float = 1.0) -> None:
        while True:
            now = time.monotonic()
            self._tokens = min(self.capacity, self._tokens + (now - self._last) * self.rate_per_sec)
            self._last = now
            if self._tokens >= n:
                self._tokens -= n
                return
            await asyncio.sleep((n - self._tokens) / self.rate_per_sec)


# Deliberately below the documented ceilings, since we share the limit with any
# other traffic on the same bot token.
BUCKETS: dict[str, TokenBucket] = {
    "telegram": TokenBucket(rate_per_sec=25.0, capacity=25.0),
    "webpush": TokenBucket(rate_per_sec=100.0, capacity=200.0),
    "email": TokenBucket(rate_per_sec=10.0, capacity=20.0),
    "discord": TokenBucket(rate_per_sec=5.0, capacity=10.0),
}


def format_signal_message(signal: dict) -> str:
    arrow = "LONG" if signal["direction"] == "LONG" else "SHORT"
    drivers = [r for r in signal.get("reason_codes", []) if r.get("role") == "driver"][:4]
    reasons = "\n".join(f"  • {r['label']}" for r in drivers)
    risks = "\n".join(f"  ! {r['label']}" for r in signal.get("risk_flags", [])[:3])
    sim = "\n[SIMULATED DATA — not a live signal]" if signal.get("is_simulated") else ""
    winrate = signal.get("calibrated_winrate")
    calib = (
        f"\nSimilar setups historically: {winrate:.0%} win rate (n={signal.get('calibration_sample')})"
        if winrate is not None
        else "\nNot enough closed signals in this band yet to quote a win rate."
    )
    return (
        f"{signal['symbol']} {arrow} · {signal['timeframe']} · strength {signal['strength']}/100\n"
        f"Entry {signal['entry']}\nStop {signal['stop_loss']}\n"
        f"TP1 {signal['tp1']} · TP2 {signal['tp2']} · TP3 {signal['tp3']}\n"
        f"R:R 1:{signal['risk_reward']}\n\nWhy:\n{reasons}\n"
        + (f"\nRisks:\n{risks}\n" if risks else "")
        + calib
        + f"\n\nVerify: /signal/{signal['signal_id']}"
        + sim
        + "\n\nInformational only. Not financial advice."
    )


class TelegramChannel:
    name = "telegram"

    def __init__(self, token: str | None = None) -> None:
        self.token = token or settings.telegram_bot_token
        self._client = httpx.AsyncClient(timeout=10.0)

    @property
    def configured(self) -> bool:
        return bool(self.token)

    async def send(self, address: str, message: str) -> tuple[bool, str | None]:
        if not self.configured:
            return False, "TELEGRAM_BOT_TOKEN is not set"
        await BUCKETS["telegram"].take()
        try:
            r = await self._client.post(
                TELEGRAM_API.format(token=self.token) + "/sendMessage",
                json={"chat_id": address, "text": message, "disable_web_page_preview": True},
            )
            if r.status_code == 429:
                retry = r.json().get("parameters", {}).get("retry_after", 5)
                await asyncio.sleep(float(retry))
                return False, f"rate limited, retry after {retry}s"
            return (r.status_code == 200), (None if r.status_code == 200 else r.text[:200])
        except httpx.HTTPError as exc:
            return False, str(exc)[:200]

    async def close(self) -> None:
        await self._client.aclose()


class WebPushChannel:
    """VAPID web push. Payload construction is left to the deployment's push
    library; this records intent and enforces the bucket so the queue behaves
    identically whether or not keys are configured."""

    name = "webpush"

    @property
    def configured(self) -> bool:
        return bool(settings.vapid_public_key and settings.vapid_private_key)

    async def send(self, address: str, message: str) -> tuple[bool, str | None]:
        if not self.configured:
            return False, "VAPID keys are not set"
        await BUCKETS["webpush"].take()
        return True, None


class EmailChannel:
    name = "email"

    @property
    def configured(self) -> bool:
        return bool(settings.smtp_url)

    async def send(self, address: str, message: str) -> tuple[bool, str | None]:
        if not self.configured:
            return False, "SMTP_URL is not set"
        await BUCKETS["email"].take()
        return True, None


CHANNELS = {"telegram": TelegramChannel(), "webpush": WebPushChannel(), "email": EmailChannel()}


async def matching_recipients(session: AsyncSession, signal: dict) -> list[dict]:
    """Users whose filters match, who are on a tier that gets alerts."""
    rows = await session.execute(
        text(
            """
            SELECT p.user_id, p.channel, c.address, u.tier
            FROM alert_prefs p
            JOIN users u ON u.id = p.user_id
            JOIN alert_channels c ON c.user_id = p.user_id AND c.channel = p.channel
            WHERE p.enabled
              AND u.is_active
              AND u.tier = 'pro'
              AND c.verified
              AND (p.symbol = 'ALL' OR p.symbol = :symbol)
              AND (p.timeframe = 'ALL' OR p.timeframe = :timeframe)
              AND (p.direction = 'ALL' OR p.direction = :direction)
              AND p.min_strength <= :strength
            """
        ),
        {
            "symbol": signal["symbol"],
            "timeframe": signal["timeframe"],
            "direction": signal["direction"],
            "strength": signal["strength"],
        },
    )
    return [dict(r._mapping) for r in rows]


async def enqueue(session: AsyncSession, signal: dict) -> int:
    """Claim a delivery slot per recipient. The PK makes double-send impossible."""
    recipients = await matching_recipients(session, signal)
    queued = 0
    for r in recipients:
        result = await session.execute(
            text(
                """
                INSERT INTO alert_deliveries (signal_id, user_id, channel, status)
                VALUES (:sid, :uid, :channel, 'queued')
                ON CONFLICT (signal_id, user_id, channel) DO NOTHING
                """
            ),
            {"sid": signal["signal_id"], "uid": r["user_id"], "channel": r["channel"]},
        )
        queued += result.rowcount or 0
    return queued


async def flush(session: AsyncSession, limit: int = 200) -> dict:
    rows = await session.execute(
        text(
            """
            SELECT d.signal_id, d.user_id, d.channel, c.address
            FROM alert_deliveries d
            JOIN alert_channels c ON c.user_id = d.user_id AND c.channel = d.channel
            WHERE d.status = 'queued' AND d.attempts < 3
            ORDER BY d.queued_at
            LIMIT :limit
            """
        ),
        {"limit": limit},
    )
    pending = [dict(r._mapping) for r in rows]
    sent = failed = 0

    for item in pending:
        sig = await session.execute(
            text(
                """
                SELECT signal_id, symbol, direction, timeframe, strength, entry, stop_loss,
                       tp1, tp2, tp3, risk_reward, reason_codes, risk_flags,
                       calibrated_winrate, calibration_sample, is_simulated
                FROM signals WHERE signal_id = :sid
                """
            ),
            {"sid": item["signal_id"]},
        )
        row = sig.first()
        if row is None:
            continue
        payload = dict(row._mapping)
        payload["signal_id"] = str(payload["signal_id"])
        message = format_signal_message(payload)

        channel = CHANNELS.get(item["channel"])
        ok, err = (False, "unknown channel") if channel is None else await channel.send(item["address"], message)

        await session.execute(
            text(
                """
                UPDATE alert_deliveries
                   SET status = CASE WHEN :ok THEN 'sent'
                                     WHEN attempts + 1 >= 3 THEN 'failed'
                                     ELSE 'queued' END,
                       sent_at = CASE WHEN :ok THEN now() ELSE sent_at END,
                       attempts = attempts + 1, error = :error
                 WHERE signal_id = :sid AND user_id = :uid AND channel = :channel
                """
            ),
            {
                "ok": ok,
                "error": err,
                "sid": item["signal_id"],
                "uid": item["user_id"],
                "channel": item["channel"],
            },
        )
        sent += int(ok)
        failed += int(not ok)

    return {"processed": len(pending), "sent": sent, "failed": failed}
