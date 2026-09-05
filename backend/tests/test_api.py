"""API contract tests against the real FastAPI app with the database and Redis faked.

The point is the wire contract: response shapes, tier gating, the immutability
of what the public endpoints promise, and that nothing here fabricates a live
statistic when there is no data.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import main
from app.api import deps
from app.db import session_dep

NOW = datetime.now(timezone.utc)
LIVE_SQL = "FROM signals s JOIN signal_state st ON st.signal_id = s.signal_id\n            WHERE st.outcome IS NULL"


class FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def __iter__(self):
        return iter(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None

    def scalar(self):
        return self._rows[0] if self._rows else None

    def scalar_one(self):
        return self._rows[0]

    def one(self):
        return self._rows[0]


class FakeSession:
    """Routes SQL to canned rows by looking for a distinctive fragment."""

    def __init__(self, table: dict[str, list]):
        self.table = table

    async def execute(self, statement, params=None):
        sql = str(statement)
        for fragment, rows in self.table.items():
            if fragment in sql:
                return FakeResult(rows)
        return FakeResult([])

    async def commit(self):
        return None


def row(**kw):
    ns = SimpleNamespace(**kw)
    ns._mapping = kw
    return ns


def signal_row(**overrides):
    base = dict(
        signal_id=uuid.uuid4(), published_at=NOW - timedelta(minutes=5), symbol="BTCUSDT", direction="LONG",
        timeframe="1h", regime="trend_bull", strategy_version="1.0.0", strength=61, mtf_alignment=0.9,
        calibrated_winrate=None, calibration_sample=3, calibration_ci_low=None, calibration_ci_high=None,
        entry=64000.0, stop_loss=63000.0, tp1=65000.0, tp2=66000.0, tp3=67000.0, risk_reward=2.0,
        risk_category="medium", expires_at=NOW + timedelta(hours=6), invalidation="Close below 63000",
        reason_codes=[], risk_flags=[], data_quality={"venues_live": 2}, is_simulated=True,
        row_hash="a" * 64, prev_hash="0" * 64, seq=1, signal_class="WEAK_LONG", status="ACTIVE", outcome=None,
        tp_hits=0, mfe_pct=0.0, mae_pct=0.0, close_price=None, closed_at=None, close_reason=None,
        pnl_pct=None, r_multiple=None,
    )
    base.update(overrides)
    return row(**base)


class FakeRedis:
    def __init__(self):
        self.store: dict[str, str] = {}

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, ex=None):
        self.store[key] = value

    async def ping(self):
        return True


@pytest.fixture
def client(monkeypatch):
    table: dict[str, list] = {}
    redis = FakeRedis()

    async def fake_session():
        yield FakeSession(table)

    main.app.dependency_overrides[session_dep] = fake_session
    monkeypatch.setattr("app.api.routes.public.get_redis", lambda: redis)
    monkeypatch.setattr("app.api.routes.ws.get_redis", lambda: redis)
    c = TestClient(main.app, raise_server_exceptions=True)
    c.table = table  # type: ignore[attr-defined]
    c.redis = redis  # type: ignore[attr-defined]
    yield c
    main.app.dependency_overrides.clear()


def test_root_lists_public_endpoints_and_disclaimer(client):
    r = client.get("/")
    assert r.status_code == 200
    body = r.json()
    assert "/v1/verify/chain/full" in body["public_endpoints"]
    assert "Past performance does not predict future results" in body["disclaimer"]


def test_pricing_is_public_and_matches_the_tier_policy(client):
    r = client.get("/v1/pricing")
    assert r.status_code == 200
    plans = {p["id"]: p for p in r.json()["plans"]}
    assert plans["pro"]["price"] == 10
    assert "Bitcoin signals only" in plans["free"]["features"][0]
    assert "The public track record" in r.json()["always_free"]


def test_free_tier_sees_signal_but_levels_are_locked(client):
    client.table[LIVE_SQL] = [signal_row()]
    body = client.get("/v1/signals/live").json()
    s = body["signals"][0]
    assert s["direction"] == "LONG" and s["signal_class"] == "WEAK_LONG"
    assert s["entry"] is None and s["locked"] is True
    assert body["tier"]["tier"] == "free"


def test_old_signal_is_unlocked_for_free_tier(client):
    client.table[LIVE_SQL] = [signal_row(published_at=NOW - timedelta(hours=3))]
    s = client.get("/v1/signals/live").json()["signals"][0]
    assert s["entry"] == 64000.0 and not s.get("locked")


def test_pro_user_sees_levels_immediately(client):
    async def pro():
        return deps.CurrentUser(id="u1", email="p@x.io", tier="pro")
    main.app.dependency_overrides[deps.optional_user] = pro
    client.table[LIVE_SQL] = [signal_row()]
    s = client.get("/v1/signals/live").json()["signals"][0]
    assert s["entry"] == 64000.0


def test_status_reports_data_delayed_when_ingest_is_silent(client):
    body = client.get("/v1/status").json()
    assert body["data_delayed"] is True and body["degraded"] is True
    assert "ingest_worker_not_reporting" in body["flags"]
    assert body["engine"] == {"status": "not_reporting"}


def test_status_surfaces_engine_telemetry(client):
    client.redis.store["sp:health"] = json.dumps({"degraded": False, "venues_live": 3, "flags": [],
                                                  "max_staleness_ms": 120, "venues": {}})
    client.redis.store["sp:engine:stats"] = json.dumps({"cycles": 4, "published": 1, "no_trade": 5,
                                                        "rejections": {"NO_TRADE": 5}})
    body = client.get("/v1/status").json()
    assert body["data_delayed"] is False
    assert body["engine"]["no_trade"] == 5


def test_market_intel_503_before_first_engine_cycle(client):
    assert client.get("/v1/market-intel").status_code == 503


def test_market_intel_includes_weights_and_narrative(client):
    client.redis.store["sp:market_intel"] = json.dumps({
        "generated_at": NOW.isoformat(), "is_simulated": True, "overall_conditions_score": 62,
        "assets": [{"symbol": "BTCUSDT", "regime": "trend_bull", "score": 62,
                    "components": {"trend_clarity": 0.8, "liquidity": 0.3}}],
        "correlation": {}, "weights": {"trend_clarity": 0.22}, "explanation": "x",
    })
    body = client.get("/v1/market-intel").json()
    assert body["weights"]["trend_clarity"] == 0.22
    assert "liquidity" in body["narrative"]["summary"]


def test_verify_unknown_signal_is_404(client):
    assert client.get(f"/v1/verify/{uuid.uuid4()}").status_code == 404


def test_full_chain_verification_on_empty_ledger_is_valid(client):
    body = client.get("/v1/verify/chain/full").json()
    assert body["ok"] is True and body["signals_in_chain"] == 0


def test_performance_with_no_closed_signals_has_no_fabricated_numbers(client):
    body = client.get("/v1/performance").json()
    o = body["overall"]
    assert o["total_signals"] == 0
    assert o["win_rate"] is None and o["profit_factor"] is None
    assert o["sufficient_sample"] is False
    assert body["equity_curve"] == [] and body["calibration_curve"] == []
    assert "Nothing is removed" in body["disclaimer"]


def test_candidates_endpoint_explains_watch_states(client):
    body = client.get("/v1/candidates").json()
    assert body["candidates"] == []
    assert "NO TRADE" in body["explanation"]


def test_candles_endpoint_rejects_unknown_symbol_and_bad_timeframe(client):
    assert client.get("/v1/market/DOGEUSDT/candles").status_code == 404
    assert client.get("/v1/market/BTCUSDT/candles?timeframe=7m").status_code == 422


def test_signup_rejects_short_passwords(client):
    assert client.post("/v1/auth/signup", json={"email": "a@b.co", "password": "short"}).status_code == 422


def test_protected_routes_require_a_token(client):
    assert client.get("/v1/me/alerts").status_code == 401
    assert client.post("/v1/billing/checkout").status_code == 401


def test_webhook_rejects_unsigned_payloads(client):
    assert client.post("/v1/billing/webhook/stripe", content=b'{"id":"evt_1","type":"x"}').status_code == 401
    assert client.post("/v1/billing/webhook/unknownpay", content=b"{}").status_code == 400


def test_websocket_hello_frame_declares_mode(client):
    with client.websocket_connect("/ws/market") as ws:
        hello = ws.receive_json()
        assert hello["type"] == "hello" and hello["mode"] in ("replay", "live")
        assert isinstance(hello["is_simulated"], bool)
