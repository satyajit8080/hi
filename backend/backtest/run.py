"""CLI: python -m backtest.run --symbol BTCUSDT --timeframe 1h"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.features.snapshot import Candles
from backtest.engine import Backtester, BacktestConfig
from backtest.metrics import compute
from backtest.walkforward import run_walkforward

DATA_DIR = Path(__file__).resolve().parent.parent / "app" / "seed" / "data"


def load(symbol: str, timeframe: str) -> Candles:
    path = DATA_DIR / f"candles_{symbol}_{timeframe}.json"
    if not path.exists():
        raise SystemExit(
            f"No dataset at {path}. Generate one first:\n    python -m app.seed.generate_replay"
        )
    rows = json.loads(path.read_text())
    return Candles(
        ts=[r["ts"] for r in rows], open=[r["open"] for r in rows], high=[r["high"] for r in rows],
        low=[r["low"] for r in rows], close=[r["close"] for r in rows],
        volume=[r["volume"] for r in rows],
    )


def main() -> None:
    p = argparse.ArgumentParser(description="Run a SignalProof backtest")
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--timeframe", default="1h")
    p.add_argument("--fee-bps", type=float, default=5.0)
    p.add_argument("--slippage-bps", type=float, default=3.0)
    p.add_argument("--min-strength", type=int, default=45)
    p.add_argument("--walkforward", action="store_true")
    p.add_argument("--montecarlo", action="store_true")
    p.add_argument("--sensitivity", action="store_true")
    p.add_argument("--ablation", action="store_true")
    p.add_argument("--pbo", action="store_true", help="CSCV over the sensitivity grid")
    p.add_argument("--bars", type=int, default=0, help="use only the trailing N bars")
    p.add_argument("--out", type=Path)
    args = p.parse_args()

    candles = load(args.symbol, args.timeframe)
    if args.bars:
        from app.features.snapshot import Candles as _C
        n = args.bars
        candles = _C(ts=candles.ts[-n:], open=candles.open[-n:], high=candles.high[-n:],
                     low=candles.low[-n:], close=candles.close[-n:], volume=candles.volume[-n:])
    cfg = BacktestConfig(
        symbol=args.symbol, timeframe=args.timeframe,
        fee_bps=args.fee_bps, slippage_bps=args.slippage_bps, min_strength=args.min_strength,
    )

    if args.ablation:
        from backtest.ablation import run as run_ablation
        report = run_ablation(candles, cfg)
    elif args.sensitivity or args.pbo:
        from backtest.sensitivity import run as run_sensitivity
        report = run_sensitivity(candles, cfg)
        if args.pbo:
            import numpy as np
            from backtest.pbo import run as run_pbo
            # Per-config monthly return series from the sensitivity grid, for CSCV.
            from backtest.engine import Backtester as _BT
            series = []
            for row in report["rows"][:12]:
                c2 = BacktestConfig(**{**cfg.__dict__, "min_strength": row["min_strength"]})
                trades = _BT(c2).run(candles)
                months = {}
                for tr in trades:
                    months.setdefault(tr["closed_at"][:7], 0.0)
                    months[tr["closed_at"][:7]] += tr["pnl_pct"]
                series.append(months)
            keys = sorted({k for s in series for k in s})
            if len(keys) >= 32 and len(series) >= 2:
                matrix = np.array([[s.get(k, 0.0) for s in series] for k in keys])
                report["pbo"] = run_pbo(matrix)
            else:
                report["pbo"] = {"sufficient": False,
                                 "note": f"Only {len(keys)} monthly periods; CSCV needs at least 32."}
    elif args.walkforward:
        report = run_walkforward(candles, cfg)
        trades = report.pop("trades")
        if args.montecarlo:
            from backtest.montecarlo import run as run_mc
            report["montecarlo"] = run_mc(trades)
    else:
        bt = Backtester(cfg)
        trades = bt.run(candles)
        report = {
            "metrics": compute(trades).to_dict(),
            "skipped": bt.skipped,
            "trades": trades[-25:],
            "note": "In-sample run. Use --walkforward before believing any of these numbers.",
        }
        if args.montecarlo:
            from backtest.montecarlo import run as run_mc
            report["montecarlo"] = run_mc(trades)

    text = json.dumps(report, indent=2)
    if args.out:
        args.out.write_text(text)
        print(f"Wrote {args.out}")
    else:
        print(text)
    print("\nDataset is simulated; these figures are not a track record.")


if __name__ == "__main__":
    main()
