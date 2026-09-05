"use client";

import clsx from "clsx";

import { compact, price as fmtPrice, ratio, REGIME_LABELS } from "@/lib/format";
import type { MarketSnapshot, MicroFeatures } from "@/lib/types";
import { Sparkline } from "./charts";
import { Bar, Chip, EmptyState, KeyValue, Panel } from "./ui";

/* ── depth ladder ─────────────────────────────────────────────────────────── */

/**
 * The book, drawn the way a trader reads one: asks descending into the spread
 * from above, bids below, with size rendered as a proportional fill behind the
 * numbers so relative depth is visible without reading a single figure.
 */
export function DepthLadder({ snapshot, levels = 10 }: { snapshot: MarketSnapshot; levels?: number }) {
  const bids = (snapshot.book?.bids ?? []).slice(0, levels);
  const asks = (snapshot.book?.asks ?? []).slice(0, levels);

  if (!bids.length || !asks.length) {
    return (
      <EmptyState
        title="Order book unavailable"
        body="The local book is not synchronised. It rebuilds automatically from a fresh snapshot."
      />
    );
  }

  const maxSize = Math.max(...bids.map((b) => b[1]), ...asks.map((a) => a[1])) || 1;
  const spread = asks[0][0] - bids[0][0];
  const spreadBps = (spread / ((asks[0][0] + bids[0][0]) / 2)) * 10000;

  const Row = ({ price, size, side }: { price: number; size: number; side: "bid" | "ask" }) => (
    <div className="relative flex items-center justify-between h-5 px-2">
      <div
        className={clsx("absolute inset-y-0 right-0", side === "bid" ? "bg-long/10" : "bg-short/10")}
        style={{ width: `${(size / maxSize) * 100}%` }}
        aria-hidden
      />
      <span className={clsx("num text-micro relative", side === "bid" ? "text-long" : "text-short")}>
        {fmtPrice(price, snapshot.symbol)}
      </span>
      <span className="num text-micro text-ink-300 relative">{size.toFixed(4)}</span>
    </div>
  );

  return (
    <div>
      <div className="flex items-center justify-between px-2 pb-1">
        <span className="text-micro text-ink-400">Price</span>
        <span className="text-micro text-ink-400">Size</span>
      </div>

      <div className="flex flex-col-reverse">
        {asks.map((a, i) => <Row key={`a${i}`} price={a[0]} size={a[1]} side="ask" />)}
      </div>

      <div className="flex items-center justify-between px-2 h-7 my-0.5 border-y border-edge bg-base-900">
        <span className="num text-tick text-ink-100">{fmtPrice(snapshot.price, snapshot.symbol)}</span>
        <span className="num text-micro text-ink-400">
          spread {spread.toFixed(2)} · {spreadBps.toFixed(1)} bps
        </span>
      </div>

      <div>
        {bids.map((b, i) => <Row key={`b${i}`} price={b[0]} size={b[1]} side="bid" />)}
      </div>
    </div>
  );
}

/* ── order-flow readouts ──────────────────────────────────────────────────── */

const ROLE_NOTE: Record<string, string> = {
  timing: "Used for entry timing on 5m and 15m only",
  confirm: "Confirms or vetoes — never sets direction",
  gate: "Risk gate — can suppress a signal, never creates one",
  context: "Shown for context, not scored",
};

export function OrderFlowPanel({ micro, timeframe }: { micro: MicroFeatures; timeframe?: string }) {
  if (!micro?.available) {
    return (
      <EmptyState
        title="Order flow unavailable"
        body="Book and trade feeds are not reporting for this market. Signals fall back to price, structure and volume."
      />
    );
  }

  const rows: {
    label: string; value: string; bar?: number; role: keyof typeof ROLE_NOTE; tone?: "signed";
  }[] = [
    {
      label: "Book imbalance (persistence weighted)",
      value: ratio(micro.obi_persistent, 3),
      bar: micro.obi_persistent ?? 0,
      role: "timing",
      tone: "signed",
    },
    {
      label: "Book imbalance (top 5)",
      value: ratio(micro.obi_5, 3),
      bar: micro.obi_5 ?? 0,
      role: "timing",
      tone: "signed",
    },
    {
      label: "Order flow imbalance (robust z)",
      value: ratio(micro.ofi_z, 2),
      bar: Math.max(-1, Math.min(1, (micro.ofi_z ?? 0) / 4)),
      role: "timing",
      tone: "signed",
    },
    {
      label: "Aggressive trade imbalance",
      value: ratio(micro.trade_imbalance, 3),
      bar: micro.trade_imbalance ?? 0,
      role: "confirm",
      tone: "signed",
    },
    {
      label: "CVD divergence from price",
      value: ratio(micro.cvd_divergence, 3),
      bar: micro.cvd_divergence ?? 0,
      role: "confirm",
      tone: "signed",
    },
    { label: "Spread (robust z)", value: ratio(micro.spread_z, 2), role: "gate" },
    { label: "Largest print (robust z)", value: ratio(micro.large_print_z, 2), role: "context" },
  ];

  const zeroWeight = timeframe && ["1h", "4h", "1d"].includes(timeframe);

  return (
    <div className="space-y-3">
      {zeroWeight && (
        <p className="text-micro text-ink-400 border border-edge rounded-panel p-2 leading-relaxed">
          These readings carry <span className="num text-ink-200">zero weight</span> on the{" "}
          {timeframe} timeframe. Order-flow edges decay in minutes, so they time entries on 5m and
          15m and are never allowed to vote on a higher-timeframe direction.
        </p>
      )}

      <dl className="space-y-2.5">
        {rows.map((r) => (
          <div key={r.label}>
            <div className="flex items-baseline justify-between gap-3">
              <dt className="text-tick text-ink-200">{r.label}</dt>
              <dd className="num text-tick text-ink-100">{r.value}</dd>
            </div>
            {r.bar !== undefined && <div className="mt-1"><Bar value={r.bar} tone={r.tone} /></div>}
            <div className="text-micro text-ink-400 mt-0.5">{ROLE_NOTE[r.role]}</div>
          </div>
        ))}
      </dl>

      <div className="border-t border-edge pt-2.5 flex flex-wrap gap-2">
        <Chip tone={micro.venues_agreeing >= 2 ? "long" : "warn"}>
          {micro.venues_agreeing} venue{micro.venues_agreeing === 1 ? "" : "s"} agree
        </Chip>
        <Chip tone={micro.book_synced ? "neutral" : "warn"}>
          {micro.book_synced ? "Book synchronised" : "Book resyncing"}
        </Chip>
      </div>
    </div>
  );
}

/* ── CVD ──────────────────────────────────────────────────────────────────── */

export function CvdPanel({ snapshot }: { snapshot: MarketSnapshot }) {
  const cvd = snapshot.cvd_series ?? [];
  const prices = snapshot.price_series ?? [];
  const divergence = snapshot.micro?.cvd_divergence ?? 0;

  return (
    <div className="space-y-3">
      <div>
        <div className="flex items-baseline justify-between">
          <span className="text-micro text-ink-400">Cumulative volume delta</span>
          <span className="num text-tick text-ink-100">
            {cvd.length ? compact(cvd[cvd.length - 1]) : "—"}
          </span>
        </div>
        <Sparkline values={cvd} height={44} />
      </div>

      <div>
        <div className="flex items-baseline justify-between">
          <span className="text-micro text-ink-400">Price over the same window</span>
          <span className="num text-tick text-ink-100">
            {fmtPrice(snapshot.price, snapshot.symbol)}
          </span>
        </div>
        <Sparkline values={prices} height={44} tone="neutral" />
      </div>

      <p className="text-micro text-ink-400 leading-relaxed border-t border-edge pt-2.5">
        {Math.abs(divergence) > 0.35 ? (
          <>
            Price and flow are pulling apart ({ratio(divergence, 2)}).{" "}
            {divergence < 0
              ? "Price is holding up without buy-side flow behind it."
              : "Buyers are absorbing without price following yet."}{" "}
            The engine treats this as a reason to stand down, not as a trade in itself — divergences
            can persist far longer than a position can.
          </>
        ) : (
          <>Flow and price are moving together, which the engine reads as confirmation.</>
        )}
      </p>
    </div>
  );
}

/* ── liquidations ─────────────────────────────────────────────────────────── */

export function LiquidationTape({ snapshot }: { snapshot: MarketSnapshot }) {
  const liqs = [...(snapshot.liquidations ?? [])].reverse().slice(0, 14);
  if (!liqs.length) {
    return <EmptyState title="No recent liquidations" body="Forced closes appear here as they cross the tape." />;
  }
  const longs = liqs.filter((l) => l.side === "long").reduce((s, l) => s + l.notional, 0);
  const shorts = liqs.filter((l) => l.side === "short").reduce((s, l) => s + l.notional, 0);

  return (
    <div className="space-y-2.5">
      <div className="grid grid-cols-2 gap-2">
        <div className="border border-edge rounded-panel p-2">
          <div className="text-micro text-ink-400">Longs liquidated</div>
          <div className="num text-tick text-short mt-0.5">${compact(longs)}</div>
        </div>
        <div className="border border-edge rounded-panel p-2">
          <div className="text-micro text-ink-400">Shorts liquidated</div>
          <div className="num text-tick text-long mt-0.5">${compact(shorts)}</div>
        </div>
      </div>

      <ul className="divide-y divide-edge/60">
        {liqs.map((l, i) => (
          <li key={i} className="flex items-center justify-between py-1">
            <span className={clsx("text-micro", l.side === "long" ? "text-short" : "text-long")}>
              {l.side === "long" ? "Long closed" : "Short closed"}
            </span>
            <span className="num text-micro text-ink-300">
              {fmtPrice(l.price, snapshot.symbol)} · ${compact(l.notional)}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/* ── regime ───────────────────────────────────────────────────────────────── */

export function RegimeBadge({ regime, adx }: { regime: string; adx?: number }) {
  const tone =
    regime === "trend_bull" ? "long" : regime === "trend_bear" ? "short"
      : regime === "high_volatility" ? "warn" : "neutral";
  return (
    <span className="inline-flex items-center gap-2">
      <Chip tone={tone as any}>{REGIME_LABELS[regime] ?? regime}</Chip>
      {adx !== undefined && Number.isFinite(adx) && (
        <span className="num text-micro text-ink-400">ADX {adx.toFixed(0)}</span>
      )}
    </span>
  );
}

/* ── smart money ──────────────────────────────────────────────────────────── */

export function SmartMoneyPanel({
  context, disclosure,
}: {
  context: Record<string, any>;
  disclosure: { role: string; explanation: string; promotion_requirements: string[] };
}) {
  const isDriver = context?.is_signal_driver;

  return (
    <Panel
      title="On-chain and whale context"
      action={<Chip tone={isDriver ? "long" : "muted"}>{isDriver ? "scored" : "not scored"}</Chip>}
    >
      {context?.available ? (
        <div className="space-y-2">
          <KeyValue
            k="Tracked-wallet accumulation (robust z)"
            v={ratio(context.smart_money_accum_z, 2)}
          />
          <KeyValue k="Wallets in cohort" v={context.smart_money_cohort_size ?? "—"} />
          <KeyValue
            k="Exchange net flow"
            v={context.exchange_netflow_usd !== null ? `$${compact(context.exchange_netflow_usd)}` : "—"}
          />
          {context.hyperliquid_whale_bias !== null && context.hyperliquid_whale_bias !== undefined && (
            <KeyValue k="Hyperliquid large-position lean" v={ratio(context.hyperliquid_whale_bias, 2)} />
          )}
        </div>
      ) : (
        <EmptyState
          title="No on-chain context yet"
          body="Wallet-flow data has not been ingested for this market."
        />
      )}

      <div className="mt-3 border-t border-edge pt-3">
        <p className="text-micro text-ink-300 leading-relaxed">{disclosure.explanation}</p>
        <details className="mt-2">
          <summary className="text-micro text-ink-400 cursor-pointer hover:text-ink-200">
            What it would take for this to affect a signal
          </summary>
          <ul className="mt-1.5 space-y-1">
            {disclosure.promotion_requirements.map((r) => (
              <li key={r} className="text-micro text-ink-400 flex gap-2">
                <span aria-hidden>·</span>
                <span>{r}</span>
              </li>
            ))}
          </ul>
        </details>
      </div>
    </Panel>
  );
}
