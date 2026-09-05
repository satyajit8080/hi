"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { CalibrationChart, EquityCurve } from "@/components/charts";
import { Chip, ErrorState, OutcomeTag, Panel, Skeleton, Stat } from "@/components/ui";
import { API, getHistory, getPerformance } from "@/lib/api";
import { datetime, pct, rate, ratio, timeAgo } from "@/lib/format";
import type { PerfStats, PerformanceReport, Signal } from "@/lib/types";

const WINDOWS = [
  { id: "30d", label: "30 days" },
  { id: "90d", label: "90 days" },
  { id: "6m", label: "6 months" },
  { id: "1y", label: "1 year" },
  { id: "all", label: "All time" },
];

export default function PerformancePage() {
  const [window_, setWindow] = useState("all");
  const [report, setReport] = useState<PerformanceReport | null>(null);
  const [losses, setLosses] = useState<Signal[] | null>(null);
  const [error, setError] = useState<Error | null>(null);

  useEffect(() => {
    setReport(null);
    getPerformance(window_).then(setReport).catch(setError);
  }, [window_]);

  useEffect(() => {
    getHistory({ outcome: "LOSS", limit: 25 })
      .then((r) => setLosses(r.signals))
      .catch(() => setLosses([]));
  }, []);

  if (error) return <ErrorState error={error} />;

  const o = report?.overall;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Track record</h1>
          <p className="text-tick text-ink-400 mt-0.5 max-w-prose">
            Every closed signal, wins and losses alike. No account needed, no filtering, no restated
            history.
          </p>
        </div>
        <div className="flex flex-wrap gap-1">
          {WINDOWS.map((w) => (
            <button
              key={w.id}
              onClick={() => setWindow(w.id)}
              className={`btn h-7 text-tick ${
                window_ === w.id ? "bg-base-700 text-ink-100" : "text-ink-400 hover:text-ink-200"
              }`}
            >
              {w.label}
            </button>
          ))}
        </div>
      </div>

      {report?.includes_simulated && (
        <div className="border border-warn/40 bg-warn-dim/30 rounded-panel p-3">
          <span className="text-tick font-medium text-warn">Simulated dataset</span>
          <p className="text-tick text-ink-200 mt-1 max-w-prose">
            The platform is running on generated market data. These figures demonstrate how the
            record is presented; they are not a live track record and must not be read as one.
          </p>
        </div>
      )}

      {!o ? (
        <Skeleton rows={6} />
      ) : (
        <>
          <div className="grid sm:grid-cols-2 lg:grid-cols-4 xl:grid-cols-6 gap-px bg-edge border border-edge rounded-panel overflow-hidden">
            <Cell
              label="Win rate"
              value={o.win_rate === null ? "insufficient sample" : rate(o.win_rate)}
              sub={
                o.win_rate_ci[0] !== null
                  ? `95% interval ${rate(o.win_rate_ci[0])}–${rate(o.win_rate_ci[1])}`
                  : `needs ${o.minimum_sample} closed signals`
              }
            />
            <Cell label="Closed signals" value={o.total_signals.toLocaleString()}
                  sub={`${o.winning_signals} wins · ${o.losing_signals} losses · ${o.expired_signals} expired`} />
            <Cell label="Profit factor" value={ratio(o.profit_factor)} sub="gross profit ÷ gross loss" />
            <Cell label="Expectancy" value={pct(o.expectancy_pct)} sub="average per closed signal" />
            <Cell label="Max drawdown" value={pct(o.max_drawdown_pct)} sub="peak to trough, cumulative" />
            <Cell label="Worst losing streak" value={`${o.max_consecutive_losses}`}
                  sub={`current: ${o.current_streak} ${o.current_streak_kind}`} />
          </div>

          <div className="grid xl:grid-cols-[1.4fr_1fr] gap-4">
            <Panel title="Cumulative return and drawdown">
              <EquityCurve points={report.equity_curve} height={280} />
              <p className="text-micro text-ink-400 mt-3 leading-relaxed max-w-prose">
                Each point is one closed signal, in the order it closed. The bars beneath show
                drawdown from the running peak. This is a percentage-per-signal curve, not a
                compounded equity model, because we do not know or assume your position sizing.
              </p>
            </Panel>

            <Panel title="Are the confidence numbers honest?">
              <CalibrationChart curve={report.calibration_curve} height={280} />
              <p className="text-micro text-ink-400 mt-2 leading-relaxed">
                Predicted win rate against what actually happened, over{" "}
                <span className="num">{report.calibration_sample}</span> calibrated signals. Points on
                the dashed diagonal mean the stated confidence was accurate. Bars are 95% intervals;
                dot size reflects sample count.
              </p>
            </Panel>
          </div>

          <div className="grid lg:grid-cols-4 gap-4">
            <BreakdownTable title="By market" rows={report.by_symbol} labelKey="Market" />
            <BreakdownTable title="By timeframe" rows={report.by_timeframe} labelKey="Timeframe" />
            <BreakdownTable title="By market regime" rows={report.by_regime} labelKey="Regime" />
            <BreakdownTable title="By signal strength" rows={report.by_strength_bucket ?? {}} labelKey="Strength" />
          </div>

          {report.calibration_quality?.sufficient && (
            <Panel title="Calibration quality">
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
                <Stat label="Brier score" size="sm" value={report.calibration_quality.brier?.toFixed(4) ?? "—"}
                      sub={`baseline ${report.calibration_quality.brier_baseline?.toFixed(4)}`} />
                <Stat label="Log loss" size="sm" value={report.calibration_quality.log_loss?.toFixed(4) ?? "—"} />
                <Stat label="Expected calibration error" size="sm" value={report.calibration_quality.ece?.toFixed(4) ?? "—"} />
                <Stat label="Skill vs base rate" size="sm"
                      value={report.calibration_quality.skill != null ? `${(report.calibration_quality.skill * 100).toFixed(1)}%` : "—"}
                      tone={(report.calibration_quality.skill ?? 0) > 0 ? "long" : "short"} />
              </div>
              <p className="text-micro text-ink-400 mt-3 border-t border-edge pt-2.5">{report.calibration_quality.note}</p>
            </Panel>
          )}

          {report.by_month.length > 0 && (
            <Panel title="Month by month" dense>
              <div className="overflow-x-auto">
                <table className="w-full">
                  <thead>
                    <tr>
                      <th className="th">Month</th>
                      <th className="th text-right">Closed</th>
                      <th className="th text-right">Wins</th>
                      <th className="th text-right">Losses</th>
                      <th className="th text-right">Win rate</th>
                      <th className="th text-right">Profit factor</th>
                      <th className="th text-right">Expectancy</th>
                    </tr>
                  </thead>
                  <tbody>
                    {report.by_month.map((m) => (
                      <tr key={m.month}>
                        <td className="td num">{m.month}</td>
                        <td className="td num text-right">{m.total_signals}</td>
                        <td className="td num text-right text-long">{m.winning_signals}</td>
                        <td className="td num text-right text-short">{m.losing_signals}</td>
                        <td className="td num text-right">{m.win_rate === null ? "—" : rate(m.win_rate)}</td>
                        <td className="td num text-right">{ratio(m.profit_factor)}</td>
                        <td className="td num text-right">{pct(m.expectancy_pct)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Panel>
          )}
        </>
      )}

      <Panel
        title="Losing signals"
        action={
          <Link href="/history?outcome=LOSS" className="text-micro text-ink-400 hover:text-ink-200">
            See all losses
          </Link>
        }
        dense
      >
        <div className="px-3 pt-3">
          <p className="text-micro text-ink-400 max-w-prose leading-relaxed">
            Published here deliberately and in full. A signal service that cannot show you its losses
            is not showing you its record.
          </p>
        </div>
        {losses === null ? (
          <div className="p-3"><Skeleton rows={4} /></div>
        ) : losses.length === 0 ? (
          <p className="p-3 text-tick text-ink-400">No losing signals have closed yet.</p>
        ) : (
          <div className="overflow-x-auto mt-2">
            <table className="w-full">
              <thead>
                <tr>
                  <th className="th">Signal</th>
                  <th className="th">Market</th>
                  <th className="th">Direction</th>
                  <th className="th text-right">Strength</th>
                  <th className="th text-right">Result</th>
                  <th className="th text-right">Worst excursion</th>
                  <th className="th">Closed</th>
                </tr>
              </thead>
              <tbody>
                {losses.map((s) => (
                  <tr key={s.signal_id} className="hover:bg-base-700/50">
                    <td className="td">
                      <Link href={`/signal/${s.signal_id}`} className="num text-ink-300 hover:text-long">
                        {s.signal_id.slice(0, 8)}
                      </Link>
                    </td>
                    <td className="td num">{s.symbol.replace("USDT", "")}</td>
                    <td className="td">
                      <span className={s.direction === "LONG" ? "text-long" : "text-short"}>
                        {s.direction}
                      </span>
                    </td>
                    <td className="td num text-right">{s.strength}</td>
                    <td className="td num text-right text-short">{pct(s.pnl_pct, 2, true)}</td>
                    <td className="td num text-right text-ink-300">{pct(s.mae_pct, 2)}</td>
                    <td className="td text-ink-400">{timeAgo(s.closed_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>

      <div className="border border-edge rounded-panel p-4 flex flex-col sm:flex-row gap-4 items-start sm:items-center justify-between">
        <p className="text-tick text-ink-300 max-w-prose leading-relaxed">
          {report?.disclaimer ??
            "Every closed signal is included. Past performance does not predict future results."}
        </p>
        <a href={`${API}/v1/export/signals.csv`} className="btn-ghost shrink-0">
          Download the full log (CSV)
        </a>
      </div>
    </div>
  );
}

function Cell({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="bg-base-800 p-4">
      <Stat label={label} value={value} sub={sub} />
    </div>
  );
}

function BreakdownTable({
  title, rows, labelKey,
}: {
  title: string;
  rows: Record<string, PerfStats>;
  labelKey: string;
}) {
  const entries = Object.entries(rows);
  return (
    <Panel title={title} dense>
      {entries.length === 0 ? (
        <p className="p-3 text-tick text-ink-400">Nothing has closed in this grouping yet.</p>
      ) : (
        <table className="w-full">
          <thead>
            <tr>
              <th className="th">{labelKey}</th>
              <th className="th text-right">n</th>
              <th className="th text-right">Win rate</th>
              <th className="th text-right">Expectancy</th>
            </tr>
          </thead>
          <tbody>
            {entries.map(([key, s]) => (
              <tr key={key}>
                <td className="td">{key.replace("USDT", "").replace(/_/g, " ")}</td>
                <td className="td num text-right">{s.total_signals}</td>
                <td className="td num text-right">
                  {s.win_rate === null ? (
                    <span className="text-ink-400">low n</span>
                  ) : (
                    rate(s.win_rate)
                  )}
                </td>
                <td className="td num text-right">{pct(s.expectancy_pct)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Panel>
  );
}
