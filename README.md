# SignalProof

Deterministic BTC/ETH/SOL trading signals with an append-only, publicly verifiable track record.

Every signal is hashed and written to a tamper-evident ledger the moment it is generated — before
anyone knows the outcome. Nothing is edited, nothing is deleted, and losing trades sit in the same
table as the winning ones.

---

## Quick start

Requires Docker and Docker Compose. Nothing else, and no API keys.

```bash
git clone <this-repo> signalproof && cd signalproof
cp .env.example .env

# Generate the bundled simulated dataset (~40 MB, marked simulated end to end)
docker compose run --rm api python -m app.seed.generate_replay --days 120 --seed 7

docker compose up
```

| Service | URL |
|---|---|
| Terminal UI | http://localhost:3000 |
| API | http://localhost:8000 |
| API reference | http://localhost:8000/docs |
| Health | http://localhost:8000/health |

Signals begin appearing within a couple of engine cycles (60s each). Everything is labelled
**Simulated data** because `SP_MARKET_MODE=replay` is the default.

### Windows

Install [Docker Desktop](https://www.docker.com/products/docker-desktop/) (the default WSL 2
backend is fine), start it, then from PowerShell in the repository folder:

```powershell
.\start.ps1
```

It creates `.env`, generates the simulated dataset on first run, builds and starts every service,
waits for the API and opens http://localhost:3000. Other switches:

```powershell
.\start.ps1 -Live      # switch to real exchange data (no API keys needed)
.\start.ps1 -Simulated # switch back to the simulated dataset
.\start.ps1 -Logs      # start, then follow the logs
.\start.ps1 -Stop      # stop everything, keep the database
.\start.ps1 -Reset     # stop and delete the database
```

If PowerShell refuses to run the script, allow local scripts for your user once:
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.

Notes:

- The script turns on polling file watchers (`SP_FORCE_POLLING=true`), because Docker Desktop does
  not pass file-change events from Windows folders into containers. Without it, edits to
  `backend/` and `frontend/` would not hot-reload.
- `.gitattributes` keeps files at LF line endings so they work inside the Linux containers. If
  you cloned before it existed, commit or stash your work, then run
  `git rm -r --cached . -q; git reset --hard` once to renormalise (it discards uncommitted changes).
- Port 5432 clashes with a locally installed PostgreSQL. Stop that service, or change the
  left-hand port of `db` in `docker-compose.yml`.
- In Windows PowerShell 5.1, `curl` is an alias for `Invoke-WebRequest`. Use `curl.exe` for the
  examples under "Verifying the record".

### Running without Docker

```bash
# Postgres with TimescaleDB and Redis must be reachable; point SP_DATABASE_URL / SP_REDIS_URL at them.
cd backend
pip install -e ".[dev]"
python -m app.db_bootstrap                       # apply migrations
python -m app.seed.generate_replay               # generate the replay dataset
uvicorn app.main:app --reload --port 8000        # API
python -m app.workers.ingest                     # market data      (separate shell)
python -m app.workers.engine_worker              # signal generation (separate shell)
python -m app.workers.lifecycle_worker           # outcome tracking  (separate shell)
python -m app.workers.anchor_worker              # daily anchoring   (optional)

cd ../frontend && npm install && npm run dev
```

---

## Switching to live market data

Public market data on every supported venue needs **no API key**.

```bash
SP_MARKET_MODE=live
SP_VENUES=binance,coinbase
SP_SYMBOLS=BTCUSDT,ETHUSDT,SOLUSDT
```

Restart the ingest and engine workers. Live signals are written with `is_simulated = false` and are
counted separately from replay signals in every performance statistic.

The keys in `.env.example` are all optional:

| Variable | Needed for | Without it |
|---|---|---|
| `BINANCE_API_KEY` / `SECRET` | Private endpoints, lifted rate limits | Public streams work fine |
| `COINGLASS_API_KEY` | Funding, open interest, liquidation heatmaps | Derivatives block is skipped; free `@forceOrder` liquidations still ingest |
| `TELEGRAM_BOT_TOKEN` | Telegram alerts | Alerts queue and report "not configured" |
| `VAPID_*` | Browser push | Same |
| `SP_BILLING_PROVIDER` + provider keys | Checkout | Billing endpoint returns a not-configured response; everything else runs |

> **Licensing warning.** Cheap tiers of several data providers are personal-use-only and cannot back
> a paid product. Coinglass commercial use starts at the Standard tier; CryptoQuant's affordable tier
> is CC BY-NC (non-commercial); Glassnode redistribution requires an Institutional contract. Confirm
> terms in writing before pointing this at a paid feed in production.

---

## Repository layout

```
backend/
  app/
    config.py                 settings, timeframe hierarchy
    core/hashchain.py         canonical payloads, hash chain, Merkle trees
    core/security.py          password hashing, JWT
    features/
      indicators.py           EMA/RSI/MACD/ATR/ADX/BB, robust z, percentile rank
      microstructure.py       OBI, OFI, micro-price, CVD, persistence weighting
      structure.py            swings, support/resistance, structural stops
      regime.py               regime classification, multi-timeframe alignment
      snapshot.py             the replayable FeatureSnapshot record
    engine/
      strategies.py           six strategies + regime gates
      scoring.py              weights, reason codes, driver/context separation
      risk.py                 stops, targets, R:R, publication gates
      calibration.py          Platt/isotonic, Wilson intervals, reliability
      publisher.py            validation, hash chaining, persistence
      lifecycle.py            state machine, MFE/MAE, outcome resolution
    market/
      orderbook.py            local book with sequence reconciliation
      adapters/               binance, coinbase, replay
      hub.py                  shared market state, feed health
    services/                 performance, smart money, alerts, subscriptions
    api/routes/               public, auth, account, websocket
    workers/                  ingest, engine, lifecycle, anchor
    seed/generate_replay.py   simulated dataset generator
  backtest/                   engine, metrics, walk-forward, CLI
  db/migrations/              schema, accounts, grants and views
  tests/                      85 tests
frontend/
  app/                        15 pages
  components/                 ui, nav, signal, panels, charts
  lib/                        api client, types, formatting, live socket
```

---

## How the engine works

**Timeframe hierarchy.** The daily and 4-hour set direction. The 1-hour confirms. The 15m and 5m
time the entry. A setup that fights the higher timeframes is demoted, not published as a clean
entry.

**Six strategies, gated by regime.** Trend following, momentum, mean reversion, breakout, volatility
expansion and market structure each score independently. The current regime decides which may speak
— mean reversion is muted in a trend, trend following is muted in a range. Weights are fixed,
published at `/v1/methodology`, and identical for every subscriber.

**Order flow times entries and nothing else.** Book imbalance, OFI, micro-price and CVD are strong
over seconds to minutes and worthless over days, so their weight is hard-zeroed above the 1-hour
chart in `MICRO_WEIGHT_BY_TF`. A test asserts a 4-hour signal is bit-identical with and without
order-flow data present.

**Smart money is context, not a driver.** On-chain and whale reason codes are emitted with
`contribution: 0.0` and `role: "context_only"`. Promotion requires a passing row in
`smart_money_validation` — a cohort selected on one period must predict returns in a later,
untouched period. The API refuses to start if `SP_SMART_MONEY_IS_SIGNAL_DRIVER=true` without one.

**Gates before publication.** Degraded feeds, fewer than two live venues, stale data, extreme
spreads, sub-threshold strength or R:R below 1:1.5 all block publication. The UI shows
**DATA DELAYED** rather than a signal built on a stale book.

---

### What the engine can say

Every (symbol, timeframe) evaluation ends in one of five classes:

    STRONG LONG  ·  WEAK LONG  ·  NO TRADE  ·  WEAK SHORT  ·  STRONG SHORT

NO TRADE is the default and is reached whenever the evidence is contradictory
(more than 40% of active component weight opposes the net direction), the setup
fights the 1d/4h trend, two or more high-severity risk flags are up, or
strength is below 45. STRONG needs 70. A NO TRADE decision is never silent: it
lands on the watchlist (`/v1/candidates`) with its reasons, and every
transition is written to the append-only `candidate_events` table.

Only closed candles are scored. The bar in progress is dropped before the
engine sees it, so the live path and the backtest path look at identical bars.

### Data reliability

Per venue: heartbeat, sequence validation (strict prev-seq for OKX), automatic
book resync, trade dedup by trade id, clock-skew measurement against event
timestamps, and a circuit breaker (5 failures in 60s opens the venue for 120s).
Across venues: mid-price divergence above 0.5% marks the feed degraded, and the
engine fails over to the first healthy corroborating venue when the primary is
not. Order-book votes are discounted by a spoof-risk score built from cancel
rate and displayed-vs-executed notional; a high score also raises the
`SUSPICIOUS_DEPTH` flag on the signal.

## Verifying the record

Nothing below requires an account.

```bash
# Recompute one signal's hash and see exactly what was hashed
curl -s localhost:8000/v1/verify/SIGNAL_ID | python3 -c "
import sys, json, hashlib
d = json.load(sys.stdin)
h = hashlib.sha256((d['canonical_payload'] + d['prev_hash']).encode()).hexdigest()
print('MATCH' if h == d['stored_hash'] else 'MISMATCH', h)"

# Walk the entire chain and report the first break
curl -s localhost:8000/v1/verify/chain/full | python3 -m json.tool

# The complete signal log, wins and losses
curl -s localhost:8000/v1/export/signals.csv -o signals.csv
```

**Why it holds.** Each signal stores `row_hash = sha256(canonical_payload || prev_hash)`. Altering
any published field breaks that row's hash and every hash after it. On top of that:

- `signals` and `signal_events` have `UPDATE`, `DELETE` and `TRUNCATE` revoked from the application
  role, and a trigger that raises on either.
- Mutable state lives in `signal_state`, which is a rebuildable projection of the append-only event
  log. Losing it loses nothing auditable.
- Daily Merkle roots can be timestamped into Bitcoin via OpenTimestamps, pinning the record to a
  moment nobody here controls.

Try it: connect as the app role and attempt `UPDATE signals SET strength = 99;`. It is refused.

---

## Honesty rules enforced in code

| Rule | Where |
|---|---|
| Stop-first when a candle contains both stop and target | `engine/lifecycle.py`, `backtest/engine.py` |
| No win rate below 30 closed signals in a band | `engine/calibration.py::empirical_bucket` |
| Context features can never carry weight | `engine/publisher.py::validate` raises `CONTEXT_LEAKED_INTO_SCORE` |
| Order flow cannot influence 4h/1d | `engine/scoring.py::MICRO_WEIGHT_BY_TF` |
| Backtests run without order flow, since it cannot be rebuilt from OHLCV | `backtest/engine.py::_snapshot` |
| Costs always charged: fees, slippage, funding | `backtest/engine.py::_close` |
| Losses, expiries and cancellations all in the denominator | `services/performance.py::compute_stats` |

---

## Tests

    cd backend && python -m pytest tests/ -q          # 169 tests, ~60s

Suites: `test_classification` (five-state classifier, contradiction, a golden
fixture that pins strength 46 for a fixed input, determinism, parabolic moves
not chased), `test_data_quality` (dedup, skew, circuit breakers, divergence,
failover, spoof metrics, unclosed-bar drop), `test_adapters` (Binance,
Coinbase, OKX, Bybit normalisation), `test_backtest_advanced` (Monte Carlo,
PBO, sensitivity, ablation, look-ahead in the feature store), `test_intel_
smartmoney_security` (conditions score, cohort FDR and OOS gate, bcrypt and
JWT), `test_api` (the real app against a faked database and Redis, tier
gating, DATA DELAYED, empty-ledger verification, no fabricated performance
numbers), plus the original hash-chain, indicator, microstructure, risk,
lifecycle, calibration and backtest suites.

If the golden fixture in `test_classification` changes, the engine changed:
bump `SP_STRATEGY_VERSION` and record it in `strategy_versions`.

## Backtesting

    cd backend
    python -m backtest.run --symbol BTCUSDT --timeframe 1h --walkforward --montecarlo
    python -m backtest.run --symbol BTCUSDT --timeframe 1h --sensitivity --pbo
    python -m backtest.run --symbol BTCUSDT --timeframe 1h --ablation

Costs charged: fee, slippage and half-spread, each on both sides; funding per
8h while a trade is open. Stop-first on ambiguous bars. Walk-forward reports
out-of-sample calibration (Platt fitted on the first 60% of trades, Brier and
log loss on the rest, against a base-rate baseline), and breakdowns by regime,
signal class and strength bucket. `--montecarlo` bootstraps the trade sequence
and shuffles its path; `--pbo` runs combinatorially symmetric cross-validation
over the sensitivity grid; `--ablation` compares variants A–E:

    A technical · B +derivatives · C +order flow · D +both · E +smart money

Order-flow and smart-money variants need recorded observations
(`backtest.engine.FeatureStore`). Without them C/D/E are identical to A/B and
the report says so. A layer is promoted to a scoring driver only if it lifts
net-of-cost expectancy by 0.10pp and out-of-sample Brier by 0.005 over at least
60 trades.

## Subscription model

| | Free | Pro — $10/month |
|---|---|---|
| Markets | BTC | BTC, ETH, SOL |
| Delay | 60 minutes | none |
| History | 30 days | complete |
| Timeframes | 1h, 4h | 5m–1d |
| Alerts | — | Telegram, push, email |
| Track record, verification, CSV | ✓ | ✓ |

The proof is free on every plan. A record only visible to paying customers proves nothing to
someone deciding whether to become one.

Payment credentials stay external: set `SP_BILLING_PROVIDER` to `stripe`, `razorpay` or `paddle`
plus the matching secrets. Webhooks are HMAC-verified and idempotent on `(provider, event_id)`.

---

## Deploying to a VPS

    export DOMAIN=signals.example.com REPO=https://github.com/you/signalproof.git
    curl -fsSL https://raw.githubusercontent.com/you/signalproof/main/deploy/deploy.sh | bash

Or from a checkout: `bash deploy/deploy.sh`. It installs Docker, writes `.env` with generated
secrets, opens 22/80/443 only, builds `docker-compose.prod.yml` and waits for `/v1/status`.
Caddy terminates TLS (Let's Encrypt) and routes `/v1/*`, `/ws/*` to the API and everything
else to the Next.js build. Postgres and Redis are not reachable from outside. One engine
replica only. `deploy/backup.sh` dumps the ledger nightly and verifies the chain.

DNS: an A record to the VPS. If it sits behind Cloudflare, proxy on, SSL mode **Full (strict)**,
WebSockets on. Before going live set `SP_MARKET_MODE=live`, `SP_BILLING_PROVIDER`, SMTP, and
`SP_ANCHOR_PROVIDER=opentimestamps`, then `bash deploy/deploy.sh` again.

## Production notes

- **Storage.** Only derived microstructure metrics are persisted, never raw L2 — full depth history
  runs to gigabytes per day per symbol and is almost never read back. Enable TimescaleDB compression
  on `ohlcv` and `microstructure` for a large reduction on older chunks.
- **Migrations.** Applied by `app.db_bootstrap` and recorded in `schema_migrations` with a checksum.
  Never edit an applied migration; add a new one.
- **Scaling.** The four workers are independent processes. Only the engine writes signals, and it
  serialises chain-head reads through a Postgres advisory lock, so a second engine instance cannot
  fork the ledger.
- **Secrets.** `SP_SECRET_KEY` must be replaced before any deployment
  (`openssl rand -hex 32`). CORS origins are environment-gated in `app/main.py`.

---

### Upgrading from 1.0

Apply `db/migrations/0004_candidates.sql`. Passwords now hash through `bcrypt`
directly (`passlib` was dropped: it is unmaintained and breaks against
`bcrypt >= 4.1`); existing `$2b$` hashes verify unchanged. `pydantic[email]` is
now a declared dependency. Add `okx` and `bybit` to `SP_VENUES` if you want
four-venue failover.

## Legal position

SignalProof publishes automated, impersonal market analytics for informational and educational
purposes. Output is identical for every subscriber and algorithmically generated — it is not
personalised investment advice, a solicitation, or a recommendation tailored to anyone's
circumstances. The platform does not custody funds, execute trades or manage accounts.

Trading cryptocurrency carries substantial risk of loss. Past performance does not predict future
results.

Publishing impersonal analytics on a subscription basis is a materially different regulatory posture
from personalised advice, but the perimeter varies by jurisdiction. Take local advice before
operating commercially.
