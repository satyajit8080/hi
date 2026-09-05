"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";

import { HashVerify, LevelLadder, LifecycleTimeline, WhySignal } from "@/components/signal";
import { RegimeBadge } from "@/components/panels";
import {
  Chip, DirectionTag, ErrorState, KeyValue, OutcomeTag, Panel, SimulatedTag, Skeleton, Stat,
} from "@/components/ui";
import { getSignal } from "@/lib/api";
import { datetime, pct, price as fmtPrice, ratio, until } from "@/lib/format";
import type { Signal } from "@/lib/types";

export default function SignalDetailPage() {
  const params = useParams<{ id: string }>();
  const [signal, setSignal] = useState<Signal | null>(null);
  const [error, setError] = useState<Error | null>(null);

  useEffect(() => {
    if (!params.id) return;
    getSignal(params.id).then(setSignal).catch(setError);
  }, [params.id]);

  if (error) {
    return (
      <div className="max-w-lg">
        <ErrorState error={error} />
        <Link href="/history" className="btn-ghost mt-3">Back to signal history</Link>
      </div>
    );
  }
  if (!signal) return <Skeleton rows={8} className="max-w-3xl" />;

  const features = signal.features ?? {};
  const ta = features.ta ?? {};
  const micro = features.micro ?? {};
  const quality = signal.data_quality ?? {};

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2 flex-wrap">
            <h1 className="text-xl font-semibold tracking-tight">
              {signal.symbol.replace("USDT", "")}
            </h1>
            <DirectionTag direction={signal.direction} />
            <Chip tone="muted">{signal.timeframe}</Chip>
            <RegimeBadge regime={signal.regime} adx={ta.adx} />
            {signal.is_simulated && <SimulatedTag />}
          </div>
          <p className="text-micro text-ink-400 mt-1.5 num">
            {signal.signal_id}
          </p>
        </div>
        <div className="text-right">
          <OutcomeTag outcome={signal.outcome} status={signal.status} />
          <div className="text-micro text-ink-400 mt-1">
            {signal.outcome
              ? `Closed ${datetime(signal.closed_at)}`
              : `Expires in ${until(signal.expires_at)}`}
          </div>
        </div>
      </div>

      <div className="grid lg:grid-cols-[1fr_360px] gap-4">
        <div className="space-y-4">
          <Panel title="The trade as published">
            <div className="grid sm:grid-cols-[1fr_1fr] gap-6">
              <LevelLadder signal={signal} />
              <div className="space-y-4">
                <div className="grid grid-cols-2 gap-4">
                  <Stat label="Strength" value={`${signal.strength}/100`} />
                  <Stat
                    label="Risk / reward"
                    value={`1:${ratio(signal.risk_reward)}`}
                    tone={signal.risk_reward >= 2.5 ? "long" : "neutral"}
                  />
                  <Stat label="Risk category" value={signal.risk_category} size="sm" />
                  <Stat
                    label="Timeframe agreement"
                    value={`${Math.round(signal.mtf_alignment * 100)}%`}
                    size="sm"
                  />
                </div>

                {signal.calibrated_winrate !== null ? (
                  <div className="border-t border-edge pt-3">
                    <div className="text-micro text-ink-400">
                      Historical win rate at this strength
                    </div>
                    <div className="num text-lg mt-0.5">
                      {(signal.calibrated_winrate * 100).toFixed(0)}%
                    </div>
                    <div className="text-micro text-ink-400 mt-0.5">
                      From {signal.calibration_sample} closed signals · 95% interval{" "}
                      {signal.calibration_ci_low !== null
                        ? `${(signal.calibration_ci_low * 100).toFixed(0)}–${(signal.calibration_ci_high! * 100).toFixed(0)}%`
                        : "—"}
                    </div>
                  </div>
                ) : (
                  <p className="text-micro text-ink-400 border-t border-edge pt-3">
                    Fewer than the minimum closed signals exist in this strength band, so no win rate
                    is quoted. We would rather show nothing than a number built on five trades.
                  </p>
                )}
              </div>
            </div>

            <p className="text-tick text-ink-200 mt-4 border-t border-edge pt-3 leading-relaxed">
              <span className="text-ink-400">Invalidation. </span>
              {signal.invalidation}
            </p>
          </Panel>

          <Panel title="Why this signal">
            <WhySignal reasons={signal.reason_codes} risks={signal.risk_flags} />
          </Panel>

          <Panel title="What happened next">
            <div className="grid sm:grid-cols-[1fr_200px] gap-6">
              <LifecycleTimeline events={signal.lifecycle ?? []} symbol={signal.symbol} />
              <div className="space-y-3">
                <Stat
                  label="Best excursion in our favour"
                  value={pct(signal.mfe_pct, 2, true)}
                  tone="long"
                  size="sm"
                />
                <Stat
                  label="Worst excursion against"
                  value={pct(signal.mae_pct, 2, true)}
                  tone="short"
                  size="sm"
                />
                {signal.outcome && (
                  <>
                    <Stat
                      label="Result"
                      value={pct(signal.pnl_pct, 2, true)}
                      tone={(signal.pnl_pct ?? 0) >= 0 ? "long" : "short"}
                      size="sm"
                    />
                    <Stat label="R multiple" value={ratio(signal.r_multiple)} size="sm" />
                  </>
                )}
                {signal.close_reason && (
                  <p className="text-micro text-ink-400 leading-relaxed border-t border-edge pt-2.5">
                    {signal.close_reason}
                  </p>
                )}
              </div>
            </div>
          </Panel>

          <Panel title="Feature snapshot at the moment of publication">
            <p className="text-micro text-ink-400 mb-3 max-w-prose leading-relaxed">
              These are the exact inputs the engine saw. Together with the strategy version they are
              enough to recompute this signal from scratch — the reason the record is auditable
              rather than merely public.
            </p>
            <div className="grid sm:grid-cols-3 gap-x-6 gap-y-0.5">
              <div>
                <h3 className="text-micro text-ink-400 mb-1">Price and indicators</h3>
                <KeyValue k="Close" v={fmtPrice(ta.close, signal.symbol)} />
                <KeyValue k="RSI 14" v={ratio(ta.rsi14, 1)} />
                <KeyValue k="ADX" v={ratio(ta.adx, 1)} />
                <KeyValue k="ATR %" v={ta.atr_pct != null ? `${(ta.atr_pct * 100).toFixed(2)}%` : "—"} />
                <KeyValue k="EMA 200" v={fmtPrice(ta.ema200, signal.symbol)} />
                <KeyValue k="Volume vs average" v={ratio(ta.volume_ratio, 2)} />
              </div>
              <div>
                <h3 className="text-micro text-ink-400 mb-1">Order flow</h3>
                {micro.available ? (
                  <>
                    <KeyValue k="Book imbalance" v={ratio(micro.obi_persistent, 3)} />
                    <KeyValue k="OFI (z)" v={ratio(micro.ofi_z, 2)} />
                    <KeyValue k="Trade imbalance" v={ratio(micro.trade_imbalance, 3)} />
                    <KeyValue k="CVD divergence" v={ratio(micro.cvd_divergence, 3)} />
                    <KeyValue k="Spread (z)" v={ratio(micro.spread_z, 2)} />
                    <KeyValue k="Venues agreeing" v={micro.venues_agreeing ?? "—"} />
                  </>
                ) : (
                  <p className="text-micro text-ink-400">
                    Order flow was unavailable for this bar, so it contributed nothing.
                  </p>
                )}
              </div>
              <div>
                <h3 className="text-micro text-ink-400 mb-1">Data quality</h3>
                <KeyValue k="Venues live" v={quality.venues_live ?? "—"} />
                <KeyValue k="Book synchronised" v={quality.book_synced ? "yes" : "no"} />
                <KeyValue k="Feed lag" v={`${quality.max_staleness_ms ?? 0} ms`} />
                <KeyValue k="Degraded" v={quality.degraded ? "yes" : "no"} />
                {features.component_scores && (
                  <>
                    <h3 className="text-micro text-ink-400 mt-3 mb-1">Component scores</h3>
                    {Object.entries(features.component_scores as Record<string, number>).map(
                      ([k, v]) => (
                        <KeyValue key={k} k={k.replace(/_/g, " ")} v={ratio(v, 3)} />
                      )
                    )}
                  </>
                )}
              </div>
            </div>

            {Array.isArray(features.engine_notes) && features.engine_notes.length > 0 && (
              <ul className="mt-4 border-t border-edge pt-3 space-y-1">
                {features.engine_notes.map((n: string, i: number) => (
                  <li key={i} className="text-micro text-ink-400">
                    {n}
                  </li>
                ))}
              </ul>
            )}
          </Panel>
        </div>

        <div className="space-y-4">
          <HashVerify signal={signal} />

          <Panel title="Provenance">
            <div className="space-y-2">
              <KeyValue k="Published" v={datetime(signal.published_at)} mono={false} />
              <KeyValue k="Expires" v={datetime(signal.expires_at)} mono={false} />
              <KeyValue k="Regime at publication" v={signal.regime.replace(/_/g, " ")} mono={false} />
              <KeyValue k="Status" v={signal.status.replace(/_/g, " ")} mono={false} />
              <KeyValue k="Targets reached" v={`${signal.tp_hits} of 3`} />
            </div>
            <p className="text-micro text-ink-400 mt-3 border-t border-edge pt-2.5 leading-relaxed">
              This page is a permanent record. Nothing on it can be edited or removed once published,
              including if the trade went badly.
            </p>
          </Panel>

          <Panel title="Elsewhere">
            <div className="flex flex-col gap-2">
              <Link href={`/asset/${signal.symbol}`} className="btn-ghost">
                Live {signal.symbol.replace("USDT", "")} market
              </Link>
              <Link href="/history" className="btn-ghost">All signals</Link>
              <Link href="/performance" className="btn-ghost">Track record</Link>
            </div>
          </Panel>
        </div>
      </div>
    </div>
  );
}
