"""Generate the bundled replay dataset.

This produces **simulated** market data so the whole platform — ingestion, book
sync, feature engine, scoring, lifecycle, UI — can run end to end with no
exchange connection and no API keys.

Every record is written with a simulated marker, which propagates into the
feature snapshot, the signal row, the API response and a badge in the UI.
Simulated signals are excluded from live performance statistics.

The generator is not trying to be a market model. It produces series with the
properties the engine's features actually need to be exercised: trending and
ranging stretches, volatility clustering, realistic intraday range structure, a
two-sided book with depth decay, and a trade tape whose aggressor mix tracks
price direction so CVD is not white noise.

    python -m app.seed.generate_replay --days 90 --seed 7
"""
from __future__ import annotations

import argparse
import json
import math
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / "data"

SYMBOL_PROFILE = {
    "BTCUSDT": {"price": 64000.0, "vol": 0.018, "tick": 0.1, "depth_usd": 2_500_000, "trade_size": 0.05},
    "ETHUSDT": {"price": 3100.0, "vol": 0.024, "tick": 0.01, "depth_usd": 1_200_000, "trade_size": 0.8},
    "SOLUSDT": {"price": 148.0, "vol": 0.038, "tick": 0.001, "depth_usd": 350_000, "trade_size": 12.0},
}

TF_MINUTES = {"5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240, "1d": 1440}


def generate_price_path(profile: dict, bars: int, rng: random.Random) -> list[float]:
    """Regime-switching random walk with volatility clustering.

    Plain GBM produces a market with no trends and no quiet periods, which makes
    the regime classifier untestable. Alternating drift and vol states gives the
    engine something with structure to find.
    """
    price = profile["price"]
    base_vol = profile["vol"] / math.sqrt(288)     # daily vol -> 5m vol
    prices: list[float] = []

    drift = 0.0
    vol_mult = 1.0
    regime_left = 0

    for _ in range(bars):
        if regime_left <= 0:
            regime = rng.choices(
                ["trend_up", "trend_down", "range", "volatile"], weights=[0.28, 0.24, 0.36, 0.12]
            )[0]
            regime_left = rng.randint(200, 900)
            drift = {"trend_up": 0.00018, "trend_down": -0.00016, "range": 0.0, "volatile": 0.0}[regime]
            vol_mult = {"trend_up": 1.0, "trend_down": 1.15, "range": 0.7, "volatile": 2.4}[regime]
        regime_left -= 1

        shock = rng.gauss(0.0, 1.0) * base_vol * vol_mult
        # Mild mean reversion inside ranges keeps prices from drifting off.
        pull = -0.02 * (math.log(price / profile["price"])) if vol_mult < 1.0 else 0.0
        price *= math.exp(drift + pull + shock)
        prices.append(round(price, 8))
    return prices


def to_ohlcv(prices: list[float], start: datetime, tf: str, rng: random.Random) -> list[dict]:
    """Aggregate the 5m path into a higher timeframe with plausible wicks."""
    step = TF_MINUTES[tf] // 5
    rows: list[dict] = []
    for i in range(0, len(prices) - step, step):
        chunk = prices[i : i + step]
        if not chunk:
            continue
        o, c = chunk[0], chunk[-1]
        body_high, body_low = max(chunk), min(chunk)
        wick = abs(c - o) * rng.uniform(0.2, 1.1) + o * 0.0004
        high = round(body_high + wick * rng.uniform(0, 1), 8)
        low = round(body_low - wick * rng.uniform(0, 1), 8)
        volume = round(abs(c - o) / max(o, 1e-9) * 90_000 + rng.uniform(400, 3200), 4)
        rows.append({
            "ts": int((start + timedelta(minutes=5 * i)).timestamp() * 1000),
            "open": round(o, 8), "high": high, "low": round(min(low, o, c), 8),
            "close": round(c, 8), "volume": volume,
            "trades": int(volume / 3), "is_simulated": True,
        })
    return rows


def build_book(price: float, profile: dict, rng: random.Random, levels: int = 50) -> dict:
    """Two-sided book with exponential depth decay and a light random lean."""
    tick = profile["tick"]
    spread_ticks = rng.randint(1, 3)
    bid = round(price - spread_ticks * tick / 2, 8)
    ask = round(price + spread_ticks * tick / 2, 8)
    lean = rng.uniform(0.85, 1.15)

    bids, asks = [], []
    for i in range(levels):
        gap = tick * (1 + i * rng.uniform(1.0, 2.5))
        size_usd = profile["depth_usd"] / levels * math.exp(-i / 14) * rng.uniform(0.6, 1.5)
        bids.append([round(bid - gap * i, 8), round(size_usd * lean / price, 6)])
        asks.append([round(ask + gap * i, 8), round(size_usd / lean / price, 6)])
    return {"last_update_id": rng.randint(10**9, 10**10), "bids": bids, "asks": asks}


def write_events(path: Path, symbols: list[str], paths: dict, start: datetime, rng: random.Random) -> int:
    """Interleave trades, depth diffs and occasional liquidations, in time order."""
    events: list[dict] = []
    for symbol in symbols:
        profile = SYMBOL_PROFILE[symbol]
        prices = paths[symbol]
        update_id = rng.randint(10**9, 10**10)

        # Emit a manageable slice: the replay adapter loops the file.
        for i, price in enumerate(list(enumerate(prices))[-2000:] and prices[-2000:]):
            ts = int((start + timedelta(minutes=5 * (len(prices) - 2000 + i))).timestamp() * 1000)
            prev = prices[max(0, len(prices) - 2000 + i - 1)]
            direction = 1 if price >= prev else -1

            # Aggressor mix follows direction, so CVD carries signal not noise.
            for _ in range(rng.randint(3, 9)):
                buy_bias = 0.58 if direction > 0 else 0.42
                is_buyer_maker = rng.random() > buy_bias
                events.append({
                    "kind": "trade", "symbol": symbol,
                    "ts_ms": ts + rng.randint(0, 290_000),
                    "payload": {
                        "price": round(price * rng.uniform(0.99985, 1.00015), 8),
                        "qty": round(profile["trade_size"] * rng.expovariate(1.0), 6),
                        "is_buyer_maker": is_buyer_maker,
                        "trade_id": rng.randint(10**8, 10**9),
                    },
                })

            for _ in range(rng.randint(2, 5)):
                update_id += rng.randint(1, 4)
                tick = profile["tick"]
                side_bias = 1.25 if direction > 0 else 0.8
                events.append({
                    "kind": "book_diff", "symbol": symbol,
                    "ts_ms": ts + rng.randint(0, 290_000),
                    "payload": {
                        "first_update_id": update_id,
                        "final_update_id": update_id,
                        "prev_update_id": update_id - 1,
                        "bids": [[round(price - tick * rng.randint(1, 30), 8),
                                  round(profile["trade_size"] * rng.uniform(0, 9) * side_bias, 6)]],
                        "asks": [[round(price + tick * rng.randint(1, 30), 8),
                                  round(profile["trade_size"] * rng.uniform(0, 9) / side_bias, 6)]],
                    },
                })

            if rng.random() < 0.012:
                events.append({
                    "kind": "liquidation", "symbol": symbol,
                    "ts_ms": ts + rng.randint(0, 290_000),
                    "payload": {
                        "side": "long" if direction < 0 else "short",
                        "price": round(price, 8),
                        "qty": round(profile["trade_size"] * rng.uniform(20, 400), 4),
                    },
                })

    events.sort(key=lambda e: e["ts_ms"])
    with path.open("w") as fh:
        for e in events:
            fh.write(json.dumps(e) + "\n")
    return len(events)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate the SignalProof replay dataset")
    parser.add_argument("--days", type=int, default=120)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--out", type=Path, default=DATA_DIR)
    args = parser.parse_args()

    rng = random.Random(args.seed)
    args.out.mkdir(parents=True, exist_ok=True)
    bars_5m = args.days * 288
    start = datetime.now(timezone.utc) - timedelta(days=args.days)

    symbols = list(SYMBOL_PROFILE)
    paths = {s: generate_price_path(SYMBOL_PROFILE[s], bars_5m, rng) for s in symbols}

    for symbol in symbols:
        for tf in TF_MINUTES:
            rows = to_ohlcv(paths[symbol], start, tf, rng)
            (args.out / f"candles_{symbol}_{tf}.json").write_text(json.dumps(rows))

        last = paths[symbol][-1]
        (args.out / f"book_{symbol}.json").write_text(
            json.dumps(build_book(last, SYMBOL_PROFILE[symbol], rng))
        )
        (args.out / f"derivatives_{symbol}.json").write_text(json.dumps({
            "funding_rate": round(rng.gauss(0.0001, 0.00012), 8),
            "open_interest": round(rng.uniform(1e8, 9e8), 2),
            "mark_price": last,
            "is_simulated": True,
        }))

    count = write_events(args.out / "events.jsonl", symbols, paths, start, rng)

    (args.out / "MANIFEST.json").write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "seed": args.seed,
        "days": args.days,
        "symbols": symbols,
        "events": count,
        "is_simulated": True,
        "warning": (
            "SIMULATED DATA. Generated by app/seed/generate_replay.py for development, testing "
            "and demonstration. It is not real market data and any signals or statistics derived "
            "from it are not a track record."
        ),
    }, indent=2))

    print(f"Wrote {count} events and {len(symbols) * len(TF_MINUTES)} candle files to {args.out}")
    print("All records are marked simulated.")


if __name__ == "__main__":
    main()
