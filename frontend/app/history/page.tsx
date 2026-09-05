"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { Chip, ErrorState, OutcomeTag, Panel, Skeleton } from "@/components/ui";
import { API, getHistory } from "@/lib/api";
import { datetime, pct, price as fmtPrice, ratio, timeAgo } from "@/lib/format";
import type { Signal, TierSummary } from "@/lib/types";

const SYMBOLS = ["", "BTCUSDT", "ETHUSDT", "SOLUSDT"];
const TIMEFRAMES = ["", "15m", "1h", "4h"];
const OUTCOMES = ["", "WIN", "LOSS", "EXPIRED", "CANCELLED"];
const PAGE = 50;

export default function HistoryPage() {
  const [filters, setFilters] = useState({ symbol: "", timeframe: "", outcome: "", direction: "" });
  const [offset, setOffset] = useState(0);
  const [signals, setSignals] = useState<Signal[] | null>(null);
  const [total, setTotal] = useState(0);
  const [tier, setTier] = useState<TierSummary | null>(null);
  const [limited, setLimited] = useState(false);
  const [error, setError] = useState<Error | null>(null);

  useEffect(() => {
    setSignals(null);
    getHistory({ ...filters, limit: PAGE, offset })
      .then((r) => {
        setSignals(r.signals);
        setTotal(r.total_published);
        setTier(r.tier);
        setLimited(r.history_limited);
        setError(null);
      })
      .catch(setError);
  }, [filters, offset]);

  const set = (key: keyof typeof filters, value: string) => {
    setOffset(0);
    setFilters((f) => ({ ...f, [key]: value }));
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Signal history</h1>
          <p className="text-tick text-ink-400 mt-0.5">
            <span className="num">{total.toLocaleString()}</span> signals published to date. Nothing
            has been removed.
          </p>
        </div>
        <a href={`${API}/v1/export/signals.csv`} className="btn-ghost">Download CSV</a>
      </div>

      <Panel title="Filter" dense>
        <div className="p-3 flex flex-wrap gap-4">
          <Select label="Market" value={filters.symbol} options={SYMBOLS}
                  render={(v) => (v ? v.replace("USDT", "") : "All")}
                  onChange={(v) => set("symbol", v)} />
          <Select label="Timeframe" value={filters.timeframe} options={TIMEFRAMES}
                  render={(v) => v || "All"} onChange={(v) => set("timeframe", v)} />
          <Select label="Outcome" value={filters.outcome} options={OUTCOMES}
                  render={(v) => (v ? v[0] + v.slice(1).toLowerCase() : "All")}
                  onChange={(v) => set("outcome", v)} />
          <Select label="Direction" value={filters.direction} options={["", "LONG", "SHORT"]}
                  render={(v) => v || "All"} onChange={(v) => set("direction", v)} />
        </div>
      </Panel>

      {limited && tier && (
        <div className="border border-edge rounded-panel p-3 flex flex-wrap items-center justify-between gap-3">
          <p className="text-tick text-ink-300">
            The free plan shows the last {tier.history_days} days. Pro opens the entire archive.
          </p>
          <Link href="/pricing" className="btn-ghost shrink-0">See Pro</Link>
        </div>
      )}

      <Panel dense>
        {error ? (
          <div className="p-3"><ErrorState error={error} /></div>
        ) : signals === null ? (
          <div className="p-3"><Skeleton rows={8} /></div>
        ) : signals.length === 0 ? (
          <p className="p-6 text-center text-tick text-ink-400">
            No signals match these filters.
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr>
                  <th className="th">Published</th>
                  <th className="th">Market</th>
                  <th className="th">Dir</th>
                  <th className="th">TF</th>
                  <th className="th text-right">Strength</th>
                  <th className="th text-right">Entry</th>
                  <th className="th text-right">Stop</th>
                  <th className="th text-right">R:R</th>
                  <th className="th">Status</th>
                  <th className="th text-right">Result</th>
                  <th className="th text-right">MFE / MAE</th>
                  <th className="th"></th>
                </tr>
              </thead>
              <tbody>
                {signals.map((s) => (
                  <tr key={s.signal_id} className="hover:bg-base-700/50">
                    <td className="td text-ink-400" title={datetime(s.published_at)}>
                      {timeAgo(s.published_at)}
                    </td>
                    <td className="td num">{s.symbol.replace("USDT", "")}</td>
                    <td className="td">
                      <span className={s.direction === "LONG" ? "text-long" : "text-short"}>
                        {s.direction}
                      </span>
                    </td>
                    <td className="td num text-ink-300">{s.timeframe}</td>
                    <td className="td num text-right">{s.strength}</td>
                    <td className="td num text-right">
                      {s.locked ? <span className="text-ink-400">locked</span> : fmtPrice(s.entry, s.symbol)}
                    </td>
                    <td className="td num text-right text-ink-300">
                      {s.locked ? "—" : fmtPrice(s.stop_loss, s.symbol)}
                    </td>
                    <td className="td num text-right">1:{ratio(s.risk_reward)}</td>
                    <td className="td"><OutcomeTag outcome={s.outcome} status={s.status} /></td>
                    <td className={`td num text-right ${
                      (s.pnl_pct ?? 0) > 0 ? "text-long" : (s.pnl_pct ?? 0) < 0 ? "text-short" : "text-ink-400"
                    }`}>
                      {s.pnl_pct === null ? "—" : pct(s.pnl_pct, 2, true)}
                    </td>
                    <td className="td num text-right text-ink-400">
                      {pct(s.mfe_pct, 1)} / {pct(s.mae_pct, 1)}
                    </td>
                    <td className="td text-right">
                      <Link href={`/signal/${s.signal_id}`} className="text-micro text-ink-400 hover:text-long">
                        open
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>

      <div className="flex items-center justify-between">
        <button className="btn-ghost" disabled={offset === 0} onClick={() => setOffset((o) => Math.max(0, o - PAGE))}>
          Previous
        </button>
        <span className="text-micro text-ink-400 num">
          {offset + 1}–{offset + (signals?.length ?? 0)}
        </span>
        <button
          className="btn-ghost"
          disabled={(signals?.length ?? 0) < PAGE}
          onClick={() => setOffset((o) => o + PAGE)}
        >
          Next
        </button>
      </div>
    </div>
  );
}

function Select({
  label, value, options, onChange, render,
}: {
  label: string;
  value: string;
  options: string[];
  onChange: (v: string) => void;
  render: (v: string) => string;
}) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-micro text-ink-400">{label}</span>
      <select
        className="field h-8 w-36 text-tick"
        value={value}
        onChange={(e) => onChange(e.target.value)}
      >
        {options.map((o) => (
          <option key={o} value={o}>{render(o)}</option>
        ))}
      </select>
    </label>
  );
}
