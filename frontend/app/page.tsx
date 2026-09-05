"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { EquityCurve } from "@/components/charts";
import { Chip, Panel, Skeleton, Stat } from "@/components/ui";
import { getPerformance, verifyChain } from "@/lib/api";
import { pct, rate, ratio, shortHash } from "@/lib/format";
import type { PerformanceReport } from "@/lib/types";

/**
 * The hero is the ledger itself.
 *
 * Every signal product on the internet opens with a promise. This one opens
 * with the live chain-verification result and the real win rate including the
 * losses — because the claim being made is "you can check", and the most
 * direct way to make that claim is to run the check on page load.
 */
export default function LandingPage() {
  const [perf, setPerf] = useState<PerformanceReport | null>(null);
  const [chain, setChain] = useState<{ ok: boolean; checked: number; head?: string } | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([getPerformance("all"), verifyChain()])
      .then(([p, c]) => { setPerf(p); setChain(c); })
      .catch((e) => setError(e.message));
  }, []);

  const o = perf?.overall;

  return (
    <div className="space-y-10">
      <section className="pt-6 pb-2">
        <div className="grid lg:grid-cols-[1.1fr_1fr] gap-8 items-start">
          <div>
            <h1 className="text-3xl sm:text-4xl font-semibold tracking-tight leading-[1.15] max-w-xl">
              Crypto signals you can audit line by line.
            </h1>
            <p className="mt-4 text-ink-200 leading-relaxed max-w-prose">
              Every BTC, ETH and SOL signal is hashed and written to an append-only ledger the moment
              it is generated — before anyone knows how it turns out. Nothing is edited, nothing is
              quietly deleted, and the losing trades sit in the same table as the winning ones.
            </p>

            <div className="mt-6 flex flex-wrap gap-2.5">
              <Link href="/dashboard" className="btn-primary">Open the live dashboard</Link>
              <Link href="/performance" className="btn-ghost">See the full track record</Link>
            </div>

            <p className="mt-4 text-micro text-ink-400">
              Free plan covers Bitcoin. Pro is $10/month for all three markets in real time.
            </p>
          </div>

          <Panel
            title="Ledger integrity, checked just now"
            action={chain && <Chip tone={chain.ok ? "long" : "short"}>{chain.ok ? "verified" : "broken"}</Chip>}
          >
            {error ? (
              <p className="text-tick text-ink-400">
                The API is not reachable. Start the backend to see live verification.
              </p>
            ) : !chain || !perf ? (
              <Skeleton rows={4} />
            ) : (
              <div className="space-y-4">
                <div className="grid grid-cols-2 gap-4">
                  <Stat label="Signals in the chain" value={chain.checked.toLocaleString()} size="lg" />
                  <Stat
                    label="Hash links checked"
                    value={chain.ok ? "all valid" : "break found"}
                    tone={chain.ok ? "long" : "short"}
                    size="lg"
                  />
                </div>
                <div>
                  <div className="text-micro text-ink-400">Current chain head</div>
                  <div className="num text-tick text-ink-200 mt-0.5 break-all">
                    {shortHash(chain.head, 24)}
                  </div>
                </div>
                <p className="text-micro text-ink-400 leading-relaxed border-t border-edge pt-3">
                  Each signal&apos;s hash includes the hash before it. Altering any published field
                  breaks every link that follows, which is why the record cannot be quietly improved
                  after the fact.
                </p>
              </div>
            )}
          </Panel>
        </div>
      </section>

      {o && (
        <section>
          <div className="grid sm:grid-cols-2 lg:grid-cols-5 gap-px bg-edge border border-edge rounded-panel overflow-hidden">
            {[
              {
                label: "Win rate",
                value: o.win_rate === null ? "—" : rate(o.win_rate),
                sub: o.win_rate_ci[0] !== null
                  ? `95% interval ${rate(o.win_rate_ci[0])}–${rate(o.win_rate_ci[1])}`
                  : "insufficient sample",
              },
              { label: "Closed signals", value: o.total_signals.toLocaleString(), sub: `${o.losing_signals} losses on the record` },
              { label: "Profit factor", value: ratio(o.profit_factor), sub: "gross profit ÷ gross loss" },
              { label: "Max drawdown", value: pct(o.max_drawdown_pct), sub: `worst streak ${o.max_consecutive_losses} losses` },
              { label: "Expectancy", value: pct(o.expectancy_pct), sub: "per closed signal" },
            ].map((s) => (
              <div key={s.label} className="bg-base-800 p-4">
                <Stat label={s.label} value={s.value} sub={s.sub} />
              </div>
            ))}
          </div>
          {perf.includes_simulated && (
            <p className="mt-2 text-micro text-warn">
              These figures come from the bundled simulated dataset. They are not a live track record.
            </p>
          )}
        </section>
      )}

      <section className="grid lg:grid-cols-3 gap-4">
        <Panel title="How a signal is built">
          <ol className="space-y-3 text-tick text-ink-200">
            <li>
              <span className="text-ink-100 font-medium">Direction comes from the daily and 4-hour.</span>{" "}
              A setup that fights the higher timeframe is demoted, not published as a clean entry.
            </li>
            <li>
              <span className="text-ink-100 font-medium">Six strategies, gated by regime.</span>{" "}
              Mean reversion is muted in a trend, trend-following is muted in a range. The weights
              are fixed, published, and identical for everyone.
            </li>
            <li>
              <span className="text-ink-100 font-medium">Order flow times the entry, nothing more.</span>{" "}
              Book imbalance and CVD decay in minutes, so they carry zero weight above the 1-hour
              chart. That limit is enforced in code, not in a policy document.
            </li>
          </ol>
        </Panel>

        <Panel title="What we refuse to do">
          <ul className="space-y-3 text-tick text-ink-200">
            <li>
              <span className="text-ink-100 font-medium">No quiet deletions.</span> The database
              revokes UPDATE and DELETE on the signal table from the application itself.
            </li>
            <li>
              <span className="text-ink-100 font-medium">No flattering ambiguity.</span> When a candle
              contains both the stop and a target, we record the stop. Order cannot be proven from
              price bars, so we take the loss.
            </li>
            <li>
              <span className="text-ink-100 font-medium">No numbers we cannot support.</span> Below 30
              closed signals in a band, we show no win rate at all rather than a comforting one.
            </li>
            <li>
              <span className="text-ink-100 font-medium">No signals on bad data.</span> If feeds lag or
              venues drop out, generation pauses and the interface says so.
            </li>
          </ul>
        </Panel>

        <Panel title="Where the record lives">
          <div className="space-y-3 text-tick text-ink-200">
            <p>
              The full signal log is downloadable as CSV without an account, and any single signal can
              be re-hashed from its stored contents to confirm it has not changed.
            </p>
            <p>
              Each day&apos;s hashes are folded into a Merkle root that can be timestamped into
              Bitcoin, which pins the record to a moment we do not control.
            </p>
            <div className="flex flex-wrap gap-2 pt-1">
              <Link href="/api-docs" className="btn-ghost">Verification and API</Link>
              <Link href="/history" className="btn-ghost">Browse every signal</Link>
            </div>
          </div>
        </Panel>
      </section>

      {perf && perf.equity_curve.length > 1 && (
        <section>
          <Panel title="Cumulative return and drawdown, every closed signal">
            <EquityCurve points={perf.equity_curve} height={260} />
            <p className="mt-3 text-micro text-ink-400 max-w-prose leading-relaxed">
              The upper line is cumulative percentage return per signal; the bars below it are
              drawdown from the running peak. Losses are not excluded, smoothed, or restated.
            </p>
          </Panel>
        </section>
      )}

      <section className="border border-edge rounded-panel p-6 flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4">
        <div>
          <h2 className="text-lg font-medium">Start on Bitcoin for nothing.</h2>
          <p className="text-tick text-ink-300 mt-1">
            Free covers BTC with a delay. Pro adds ETH and SOL in real time, alerts, and full history
            for $10 a month.
          </p>
        </div>
        <div className="flex gap-2.5 shrink-0">
          <Link href="/signup" className="btn-primary">Create an account</Link>
          <Link href="/pricing" className="btn-ghost">Compare plans</Link>
        </div>
      </section>
    </div>
  );
}
