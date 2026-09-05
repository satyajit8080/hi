"""FastAPI application."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api.routes import account, auth, public, ws
from app.config import settings
from app.db import SessionLocal, close_connections, get_redis

logging.basicConfig(level=settings.log_level.upper())
log = logging.getLogger("signalproof")

DISCLAIMER = (
    "SignalProof publishes automated, impersonal market analytics for informational and "
    "educational purposes. Nothing here is personalised investment advice, a solicitation, or a "
    "recommendation tailored to your circumstances. Signals are identical for every subscriber and "
    "are generated algorithmically. Trading cryptocurrency carries substantial risk of loss. Past "
    "performance does not predict future results. We do not custody funds, execute trades, or "
    "manage accounts."
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.smart_money_is_signal_driver:
        async with SessionLocal() as session:
            row = await session.execute(
                text("SELECT count(*) FROM smart_money_validation WHERE passed")
            )
            if int(row.scalar_one()) == 0:
                # Refuse to start rather than quietly score an unvalidated feature.
                raise RuntimeError(
                    "SP_SMART_MONEY_IS_SIGNAL_DRIVER is on but no passing row exists in "
                    "smart_money_validation. On-chain features stay context-only until a cohort "
                    "selected on one period predicts returns on a later one."
                )
    log.info("SignalProof API up — mode=%s version=%s", settings.market_mode, settings.strategy_version)
    yield
    await close_connections()


app = FastAPI(
    title="SignalProof API",
    version=settings.strategy_version,
    description=(
        "Deterministic BTC/ETH/SOL trading signals with an append-only, hash-chained, publicly "
        "verifiable track record.\n\n" + DISCLAIMER
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    # Same-origin behind Caddy, so this mostly matters for the API-access plan.
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"]
    if settings.env == "local"
    else [o.strip() for o in settings.cors_origins.split(",") if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(public.router)
app.include_router(auth.router)
app.include_router(account.router)
app.include_router(ws.router)


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    log.exception("Unhandled error on %s", request.url.path)
    return JSONResponse(
        status_code=500,
        content={
            "error": "internal_error",
            "message": "Something broke on our side. The request was not completed.",
            "path": request.url.path,
        },
    )


@app.get("/health")
async def health():
    checks: dict[str, str] = {}
    try:
        async with SessionLocal() as session:
            await session.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["database"] = f"error: {exc}"[:120]
    try:
        await get_redis().ping()
        checks["redis"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["redis"] = f"error: {exc}"[:120]

    healthy = all(v == "ok" for v in checks.values())
    return JSONResponse(
        status_code=200 if healthy else 503,
        content={"status": "ok" if healthy else "degraded", "checks": checks,
                 "mode": settings.market_mode, "version": settings.strategy_version},
    )


@app.get("/")
async def root():
    return {
        "name": "SignalProof",
        "promise": "Real-time crypto signals with a completely transparent, verifiable track record.",
        "docs": "/docs",
        "public_endpoints": [
            "/v1/signals/live",
            "/v1/signals/history",
            "/v1/performance",
            "/v1/performance/calibration",
            "/v1/verify/{signal_id}",
            "/v1/verify/chain/full",
            "/v1/export/signals.csv",
            "/v1/methodology",
            "/v1/status",
        ],
        "disclaimer": DISCLAIMER,
    }
