"use client";

import clsx from "clsx";
import Link from "next/link";
import { useState } from "react";

import { verifySignal } from "@/lib/api";
import {
  datetime, pct, price as fmtPrice, ratio, shortHash, timeAgo, until, REGIME_LABELS,
} from "@/lib/format";
import type { LifecycleEvent, ReasonCode, RiskFlag, Signal } from "@/lib/types";
import { Chip, DirectionTag, KeyValue, LockedNotice, OutcomeTag, Panel, SimulatedTag } from "./ui";

/* ── strength meter ───────────────────────────────────────────────────────── */

function StrengthMeter({ value, direction }: { value: number; direction: string }) {
  return (
    <div className="flex items-center gap-2">
      <div className="relative w-20 h-1.5 bg-base-700 rounded-panel overflow-hidden">
        <div
          className={clsx("absolute inset-y-0 left-0", direction === "LONG" ? "bg-long" : "bg-short")}
          style={{ width: `${value}%` }}
        />
      </div>
      <span className="num text-tick text-ink-200">{value}</span>
    </div>
  );
}

/* ── level ladder ─────────────────────────────────────────────────────────── */

/**
 * Prices laid out vertically in real market order — targets above entry for a
 * long, below for a short — so the geometry of the trade is legible at a glance
 * rather than read off a list.
 */
export function LevelLadder({ signal, compact }: { signal: Signal; compact?: boolean }) {
  if (signal.locked) return <LockedNotice reason={signal.locked_reason} />;
  if (signal.entry === null) return null;

  const long = signal.direction === "LONG";
  const rows = [
    { key: "TP3", value: signal.tp3, tone: "long" as const, hit: signal.tp_hits >= 3 },
    { key: "TP2", value: signal.tp2, tone: "long" as const, hit: signal.tp_hits >= 2 },
    { key: "TP1", value: signal.tp1, tone: "long" as const, hit: signal.tp_hits >= 1 },
    { key: "Entry", value: signal.entry, tone: "neutral" as const, hit: true },
    { key: "Stop", value: signal.stop_loss, tone: "short" as const, hit: signal.status === "STOPPED" },
  ];
  const ordered = long ? rows : [...rows].reverse();

  return (
    <div className={clsx("divide-y divide-edge/60", compact && "text-tick")}>
      {ordered.map((r) => (
        <div key={r.key} className="flex items-center justify-between py-1.5">
          <span className="flex items-center gap-2">
            <span
              className={clsx(
                "w-1 h-3 rounded-sm",
                r.tone === "long" && (r.hit ? "bg-long" : "bg-long/25"),
                r.tone === "short" && (r.hit ? "bg-short" : "bg-short/25"),
                r.tone === "neutral" && "bg-ink-300"
              )}
            />
            <span className="text-micro text-ink-400">{r.key}</span>
            {r.hit && r.key.startsWith("TP") && <Chip tone="long">reached</Chip>}
          </span>
          <span
            className={clsx(
              "num text-tick",
              r.tone === "long" && "text-long",
              r.tone === "short" && "text-short",
              r.tone === "neutral" && "text-ink-100 font-medium"
            )}
          >
            {fmtPrice(r.value, signal.symbol)}
          </span>
        </div>
      ))}
    </div>
  );
}

/* ── why this signal ──────────────────────────────────────────────────────── */

/**
 * The transparency surface. Drivers and context are rendered in visually
 * distinct groups because they are epistemically different things: drivers moved
 * the number, context did not. Blending them would be exactly the "theatre" the
 * product exists to avoid.
 */
export function WhySignal({
  reasons, risks, showAll,
}: {
  reasons: ReasonCode[];
  risks: RiskFlag[];
  showAll?: boolean;
}) {
  const [expanded, setExpanded] = useState(Boolean(showAll));
  const drivers = reasons.filter((r) => r.role === "driver");
  const context = reasons.filter((r) => r.role === "context_only");
  const visible = expanded ? drivers : drivers.slice(0, 4);
  const maxAbs = Math.max(...drivers.map((d) => Math.abs(d.contribution)), 0.0001);

  return (
    <div className="space-y-4">
      <div>
        <h3 className="text-micro text-ink-400 mb-2">What drove this signal</h3>
        {drivers.length === 0 ? (
          <p className="text-tick text-ink-400">No driver factors were recorded.</p>
        ) : (
          <ul className="space-y-1.5">
            {visible.map((r) => (
              <li key={r.code} className="flex items-center gap-2.5">
                <span
                  className={clsx(
                    "shrink-0 w-3 text-center text-tick",
                    r.contribution > 0 ? "text-long" : "text-short"
                  )}
                  aria-hidden
                >
                  {r.contribution > 0 ? "+" : "−"}
                </span>
                <span className="text-tick text-ink-100 flex-1 min-w-0 truncate" title={r.label}>
                  {r.label}
                </span>
                <span className="hidden sm:block w-16 h-1 bg-base-700 rounded-panel overflow-hidden shrink-0">
                  <span
                    className={clsx("block h-full", r.contribution > 0 ? "bg-long" : "bg-short")}
                    style={{ width: `${(Math.abs(r.contribution) / maxAbs) * 100}%` }}
                  />
                </span>
                <span className="num text-micro text-ink-400 w-10 text-right shrink-0">
                  {r.contribution >= 0 ? "+" : ""}
                  {r.contribution.toFixed(3)}
                </span>
              </li>
            ))}
          </ul>
        )}
        {drivers.length > 4 && (
          <button
            className="text-micro text-ink-400 hover:text-ink-200 mt-2"
            onClick={() => setExpanded((e) => !e)}
          >
            {expanded ? "Show fewer" : `Show all ${drivers.length} factors`}
          </button>
        )}
      </div>

      {risks.length > 0 && (
        <div>
          <h3 className="text-micro text-ink-400 mb-2">Risks we can see</h3>
          <ul className="space-y-1.5">
            {risks.map((r) => (
              <li key={r.code} className="flex items-start gap-2.5">
                <span
                  className={clsx(
                    "shrink-0 mt-1 w-1.5 h-1.5 rounded-full",
                    r.severity === "high" ? "bg-short" : r.severity === "medium" ? "bg-warn" : "bg-ink-400"
                  )}
                  aria-hidden
                />
                <span className="text-tick text-ink-200">{r.label}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {context.length > 0 && (
        <div className="border-t border-edge pt-3">
          <h3 className="text-micro text-ink-400 mb-2">Context, which did not affect the score</h3>
          <ul className="space-y-2">
            {context.map((r) => (
              <li key={r.code}>
                <div className="flex items-center gap-2">
                  <span className="text-tick text-ink-300">{r.label}</span>
                  <Chip tone="muted">weight 0.000</Chip>
                </div>
                {r.note && <p className="text-micro text-ink-400 mt-0.5 max-w-prose">{r.note}</p>}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

/* ── signal card ──────────────────────────────────────────────────────────── */

export function SignalCard({ signal, href }: { signal: Signal; href?: string }) {
  const rrTone = signal.risk_reward >= 2.5 ? "long" : "neutral";

  return (
    <article className="panel">
      <header className="panel-head">
        <div className="flex items-center gap-2 min-w-0">
          <Link href={href ?? `/signal/${signal.signal_id}`} className="text-sm font-semibold hover:text-long">
            {signal.symbol.replace("USDT", "")}
          </Link>
          <DirectionTag direction={signal.direction} size="sm" />
          <Chip tone="muted">{signal.timeframe}</Chip>
          {signal.signal_class && (
            <Chip tone={signal.signal_class.startsWith("STRONG") ? "neutral" : "muted"}>
              {signal.signal_class.split("_")[0].toLowerCase()}
            </Chip>
          )}
          {signal.is_simulated && <SimulatedTag />}
        </div>
        <OutcomeTag outcome={signal.outcome} status={signal.status} />
      </header>

      <div className="panel-body space-y-3">
        <div className="flex items-center justify-between gap-4">
          <div>
            <div className="text-micro text-ink-400">Strength</div>
            <div className="mt-1">
              <StrengthMeter value={signal.strength} direction={signal.direction} />
            </div>
          </div>
          <div className="text-right">
            <div className="text-micro text-ink-400">Risk / reward</div>
            <div className={clsx("num text-lg leading-none mt-1", rrTone === "long" ? "text-long" : "text-ink-100")}>
              1:{ratio(signal.risk_reward)}
            </div>
          </div>
        </div>

        <LevelLadder signal={signal} compact />

        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-micro text-ink-400 pt-1">
          <span>{REGIME_LABELS[signal.regime] ?? signal.regime}</span>
          <span>Published {timeAgo(signal.published_at)}</span>
          {!signal.outcome && <span>Valid for {until(signal.expires_at)}</span>}
          {signal.outcome && signal.pnl_pct !== null && (
            <span className={signal.pnl_pct >= 0 ? "text-long" : "text-short"}>
              {pct(signal.pnl_pct, 2, true)}
            </span>
          )}
        </div>

        {signal.calibrated_winrate !== null ? (
          <p className="text-micro text-ink-300 border-t border-edge pt-2.5">
            Signals of similar strength have closed as wins{" "}
            <span className="num text-ink-100">{(signal.calibrated_winrate * 100).toFixed(0)}%</span>{" "}
            of the time across {signal.calibration_sample} closed signals.
          </p>
        ) : (
          <p className="text-micro text-ink-400 border-t border-edge pt-2.5">
            Not enough closed signals at this strength yet to quote a win rate.
          </p>
        )}

        <Link href={href ?? `/signal/${signal.signal_id}`} className="btn-ghost w-full">
          Open signal
        </Link>
      </div>
    </article>
  );
}

/* ── lifecycle ────────────────────────────────────────────────────────────── */

const EVENT_LABELS: Record<string, string> = {
  WATCHING: "Watching",
  SETUP_FORMING: "Setup forming",
  SIGNAL_GENERATED: "Signal generated",
  ACTIVE: "Tracking started",
  TP1_HIT: "Take profit 1 reached",
  TP2_HIT: "Take profit 2 reached",
  TP3_HIT: "Take profit 3 reached",
  STOPPED: "Stopped out",
  EXPIRED: "Expired",
  CANCELLED: "Cancelled",
};

export function LifecycleTimeline({ events, symbol }: { events: LifecycleEvent[]; symbol: string }) {
  if (!events.length) {
    return <p className="text-tick text-ink-400">No lifecycle events recorded yet.</p>;
  }
  return (
    <ol className="relative pl-4">
      <span className="absolute left-[3px] top-1.5 bottom-1.5 w-px bg-edge" aria-hidden />
      {events.map((e, i) => {
        const terminal = ["STOPPED", "EXPIRED", "CANCELLED"].includes(e.event);
        const win = e.event.startsWith("TP");
        return (
          <li key={`${e.event}-${i}`} className="relative pb-3 last:pb-0">
            <span
              className={clsx(
                "absolute -left-4 top-1.5 w-1.5 h-1.5 rounded-full",
                win ? "bg-long" : terminal ? "bg-short" : "bg-ink-300"
              )}
              aria-hidden
            />
            <div className="flex items-baseline justify-between gap-3">
              <span className="text-tick text-ink-100">{EVENT_LABELS[e.event] ?? e.event}</span>
              {e.price !== null && (
                <span className="num text-tick text-ink-300">{fmtPrice(e.price, symbol)}</span>
              )}
            </div>
            <div className="text-micro text-ink-400">{datetime(e.ts)}</div>
            {e.reason && <p className="text-micro text-ink-300 mt-0.5 max-w-prose">{e.reason}</p>}
          </li>
        );
      })}
    </ol>
  );
}

/* ── verification ─────────────────────────────────────────────────────────── */

export function HashVerify({ signal }: { signal: Signal }) {
  const [result, setResult] = useState<{ valid: boolean; recomputed: string; payload: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const run = async () => {
    setBusy(true);
    setError(null);
    try {
      const r = await verifySignal(signal.signal_id);
      setResult({ valid: r.valid, recomputed: r.recomputed_hash, payload: r.canonical_payload });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Panel title="Verification">
      <div className="space-y-2.5">
        <KeyValue k="Ledger position" v={`#${signal.seq}`} />
        <KeyValue k="Signal hash" v={shortHash(signal.row_hash, 12)} />
        <KeyValue k="Previous hash" v={shortHash(signal.prev_hash, 12)} />
        <KeyValue k="Strategy version" v={signal.strategy_version} />
        <KeyValue k="Published" v={datetime(signal.published_at)} mono={false} />

        <p className="text-micro text-ink-400 leading-relaxed pt-1">
          This signal&apos;s hash is computed from its contents plus the hash of the signal before it.
          Changing any field here — a price, a reason, an outcome — changes this hash and every hash
          after it, so edits cannot be hidden.
        </p>

        <button className="btn-ghost w-full" onClick={run} disabled={busy}>
          {busy ? "Checking…" : "Recompute the hash"}
        </button>

        {error && <p className="text-micro text-short">{error}</p>}

        {result && (
          <div
            className={clsx(
              "rounded-panel border p-2.5",
              result.valid ? "border-long/40 bg-long-deep" : "border-short/40 bg-short-deep"
            )}
          >
            <div className={clsx("text-tick font-medium", result.valid ? "text-long" : "text-short")}>
              {result.valid ? "Hash matches the stored record" : "Hash does not match"}
            </div>
            <div className="num text-micro text-ink-300 mt-1 break-all">{result.recomputed}</div>
            <details className="mt-2">
              <summary className="text-micro text-ink-400 cursor-pointer hover:text-ink-200">
                Show what was hashed
              </summary>
              <pre className="mt-1.5 text-micro text-ink-300 bg-base-900 p-2 rounded-panel overflow-x-auto max-h-48">
                {result.payload}
              </pre>
            </details>
          </div>
        )}
      </div>
    </Panel>
  );
}
