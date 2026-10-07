"""Live market WebSocket for the terminal UI.

Reads the state the ingest worker publishes to Redis rather than holding
exchange connections in the API process, so the API can restart without
dropping upstream sockets.

Health rides on the same channel as prices. The client needs both to decide
whether to render a book or the DATA DELAYED state.
"""
from __future__ import annotations

import asyncio
import contextlib
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.config import settings
from app.db import get_redis
from app.services.subscriptions import allowed_symbols, redact_for_tier

router = APIRouter(tags=["ws"])


@router.websocket("/ws/market")
async def market_socket(websocket: WebSocket) -> None:
    await websocket.accept()
    redis = get_redis()
    symbols = settings.symbols
    interval = 1.0

    try:
        await websocket.send_json({
            "type": "hello",
            "symbols": symbols,
            "mode": settings.market_mode,
            "is_simulated": settings.is_replay,
            "strategy_version": settings.strategy_version,
        })

        async def reader() -> None:
            # A server that never receives cannot see the client leave. This task
            # exists to surface WebSocketDisconnect promptly; client frames are
            # otherwise ignored (the stream is one-way).
            while True:
                await websocket.receive_text()

        reader_task = asyncio.create_task(reader())
        try:
            while not reader_task.done():
                payload: dict = {"type": "tick", "markets": {}}
                for symbol in symbols:
                    raw = await redis.get(f"sp:market:{symbol}")
                    if raw:
                        payload["markets"][symbol] = json.loads(raw)

                health_raw = await redis.get("sp:health")
                payload["health"] = json.loads(health_raw) if health_raw else {
                    "degraded": True,
                    "venues_live": 0,
                    "flags": ["ingest_worker_not_reporting"],
                }

                signals_raw = await redis.get("sp:signals:live")
                if signals_raw:
                    # The socket is unauthenticated, so it gets the free tier's view:
                    # allowed symbols only, with levels locked until the delay passes.
                    free_symbols = allowed_symbols("free")
                    payload["signals"] = [
                        redact_for_tier(s, "free")
                        for s in json.loads(signals_raw)
                        if free_symbols is None or s.get("symbol") in free_symbols
                    ]

                await websocket.send_json(payload)
                await asyncio.wait({reader_task}, timeout=interval)
        finally:
            reader_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, WebSocketDisconnect, RuntimeError):
                await reader_task

    except WebSocketDisconnect:
        return
    except Exception:  # noqa: BLE001 - never take the API down over one socket
        try:
            await websocket.close(code=1011)
        except RuntimeError:
            pass
