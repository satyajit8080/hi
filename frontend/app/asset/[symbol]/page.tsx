"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";

import { PriceChart } from "@/components/charts";
import { CvdPanel, DepthLadder, LiquidationTape, OrderFlowPanel, RegimeBadge } from "@/components/panels";
import { SignalCard } from "@/components/signal";
import { DataDelayed, EmptyState, Panel, Skeleton, Stat } from "@/components/ui";
import { getCandles, getDerivatives, getLiveSignals, getSmartMoney } from "@/lib/api";
import { assetName, compact, price as fmtPrice } from "@/lib/format";
import type { Signal } from "@/lib/types";
import { useLive } from "@/lib/useLive";
import { SmartMoneyPanel } from "@/components/panels";

export default function AssetPage() {
  const params = useParams<{ symbol: string }>();
  const symbol = (params.symbol ?? "BTCUSDT").toUpperCase();
  const { markets, health } = useLive();
  const snapshot = markets[symbol];

  const [signals, setSignals] = useState<Signal[] | null>(null);
  const [smart, setSmart] = useState<any>(null);
  const [candles, setCandles] = useState<any[]>([]);
  const [tf, setTf] = useState("1h");
  const [deriv, setDeriv] = useState<any>(null);

  useEffect(() => {
    getLiveSignals(symbol).then((r) => setSignals(r.signals)).catch(() => setSignals([]));
    getSmartMoney(symbol).then(setSmart).catch(() => setSmart(null));
    getDerivatives(symbol).then(setDeriv).catch(() => setDeriv(null));
  }, [symbol]);

  useEffect(() => {
    // Closed bars from the API — the same bars the engine scored, never the one in progress.
    getCandles(symbol, tf, 300).then((r) => setCandles(r.candles)).catch(() => setCandles([]));
  }, [symbol, tf]);
  const levels = (signals ?? [])
    .filter((s) => !s.outcome && s.entry !== null)
    .flatMap((s) => [
      { price: s.entry!, label: `${s.direction} entry`, color: "neutral" as const },
      { price: s.stop_loss!, label: "stop", color: "short" as const },
      { price: s.tp2!, label: "TP2", color: "long" as const },
    ]);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">
            {assetName(symbol)} <span className="text-ink-400 font-normal">/ USDT</span>
          </h1>
          <div className="flex items-baseline gap-3 mt-1">
            <span className="num text-2xl">{fmtPrice(snapshot?.price, symbol)}</span>
            {snapshot?.micro?.available && (
              <span className="num text-tick text-ink-400">
                bid {fmtPrice(snapshot.micro.best_bid, symbol)} · ask {fmtPrice(snapshot.micro.best_ask, symbol)}
              </span>
            )}
          </div>
        </div>
        <Link href="/dashboard" className="btn-ghost">Back to dashboard</Link>
      </div>

      {health?.degraded && (
        <DataDelayed flags={health.flags} stalenessMs={health.max_staleness_ms}
                     venuesLive={health.venues_live} minVenues={health.min_venues_required} />
      )}

      <div className="grid xl:grid-cols-[1fr_340px] gap-4">
        <div className="space-y-4">
          <Panel
            title="Price with live signal levels"
            dense
            action={
              <div className="flex gap-1">
                {["15m", "1h", "4h", "1d"].map((t) => (
                  <button key={t} onClick={() => setTf(t)}
                          className={`text-micro px-1.5 h-5 rounded-panel ${tf === t ? "bg-base-700 text-ink-100" : "text-ink-400 hover:text-ink-200"}`}>
                    {t}
                  </button>
                ))}
              </div>
            }
          >
            <div className="p-2">
              <PriceChart candles={candles} levels={levels} height={400} />
            </div>
          </Panel>

          {deriv?.latest && (
            <Panel title="Derivatives">
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
                <Stat label="Funding rate" size="sm"
                      value={deriv.latest.funding_rate != null ? `${(deriv.latest.funding_rate * 100).toFixed(4)}%` : "—"}
                      tone={Math.abs(deriv.latest.funding_rate ?? 0) > 0.0005 ? "warn" : "neutral"} />
                <Stat label="Open interest" size="sm" value={compact(deriv.latest.open_interest)} />
                <Stat label="Longs liquidated (1h)" size="sm" value={`$${compact(deriv.latest.liq_long_usd)}`} tone="short" />
                <Stat label="Shorts liquidated (1h)" size="sm" value={`$${compact(deriv.latest.liq_short_usd)}`} tone="long" />
              </div>
              <p className="text-micro text-ink-400 mt-3 border-t border-edge pt-2.5 leading-relaxed">{deriv.role}</p>
            </Panel>
          )}

          <div className="grid md:grid-cols-2 gap-4">
            <Panel title="Order flow">
              <OrderFlowPanel micro={snapshot?.micro ?? ({ available: false } as any)} />
            </Panel>
            <Panel title="Cumulative volume delta">
              {snapshot ? <CvdPanel snapshot={snapshot} /> : <Skeleton rows={4} />}
            </Panel>
          </div>

          <Panel title={`Signals on ${assetName(symbol)}`} dense>
            <div className="p-3">
              {signals === null ? (
                <Skeleton rows={2} />
              ) : signals.length === 0 ? (
                <EmptyState
                  title="No open signals on this market"
                  body="Nothing here has cleared the publication gates recently."
                />
              ) : (
                <div className="grid gap-3 md:grid-cols-2">
                  {signals.map((s) => <SignalCard key={s.signal_id} signal={s} />)}
                </div>
              )}
            </div>
          </Panel>
        </div>

        <div className="space-y-4">
          <Panel title="Order book" dense>
            {snapshot ? (
              <div className="py-2"><DepthLadder snapshot={snapshot} levels={12} /></div>
            ) : (
              <div className="p-3"><Skeleton rows={8} /></div>
            )}
          </Panel>

          <Panel title="Recent liquidations">
            {snapshot ? <LiquidationTape snapshot={snapshot} /> : <Skeleton rows={4} />}
          </Panel>

          {smart && <SmartMoneyPanel context={smart.context} disclosure={smart.disclosure} />}
        </div>
      </div>
    </div>
  );
}

