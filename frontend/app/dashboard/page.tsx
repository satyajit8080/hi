"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { CvdPanel, DepthLadder, OrderFlowPanel } from "@/components/panels";
import { SignalCard } from "@/components/signal";
import { Chip, DataDelayed, EmptyState, ErrorState, Panel, Skeleton, Stat } from "@/components/ui";
import { getCandidates, getLiveSignals } from "@/lib/api";
import { assetName, price as fmtPrice } from "@/lib/format";
import type { Signal, TierSummary } from "@/lib/types";
import { useLive } from "@/lib/useLive";

export default function DashboardPage() {
  const { markets, health, connected, everConnected } = useLive();
  const [signals, setSignals] = useState<Signal[] | null>(null);
  const [tier, setTier] = useState<TierSummary | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [focus, setFocus] = useState<string>("BTCUSDT");
  const [watch, setWatch] = useState<any[] | null>(null);

  const load = () => {
    getCandidates().then((r) => setWatch(r.candidates)).catch(() => setWatch([]));
    getLiveSignals()
      .then((r) => {
        setSignals(r.signals);
        setTier(r.tier);
        setError(null);
      })
      .catch((e) => setError(e));
  };

  useEffect(() => {
    load();
    const id = setInterval(load, 20000);
    return () => clearInterval(id);
  }, []);

  const symbols = Object.keys(markets).length
    ? Object.keys(markets)
    : ["BTCUSDT", "ETHUSDT", "SOLUSDT"];
  const snapshot = markets[focus];

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Live dashboard</h1>
          <p className="text-tick text-ink-400 mt-0.5">
            Open signals, order flow and market state across the three markets.
          </p>
        </div>
        {tier && tier.tier === "free" && (
          <Link href="/pricing" className="btn-ghost">
            Free plan · Bitcoin only, {tier.delay_minutes}-minute delay
          </Link>
        )}
      </div>

      {health?.degraded && (
        <DataDelayed
          flags={health.flags}
          stalenessMs={health.max_staleness_ms}
          venuesLive={health.venues_live}
          minVenues={health.min_venues_required}
        />
      )}

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {symbols.map((s) => {
          const m = markets[s];
          const open = (signals ?? []).filter((sig) => sig.symbol === s && !sig.outcome);
          const selected = focus === s;
          return (
            <button
              key={s}
              onClick={() => setFocus(s)}
              aria-pressed={selected}
              className={`panel text-left transition-colors ${
                selected ? "border-long/50" : "hover:border-base-500"
              }`}
            >
              <div className="panel-head">
                <span className="panel-title">{assetName(s)}</span>
                <span className="text-micro text-ink-400">
                  {open.length} open signal{open.length === 1 ? "" : "s"}
                </span>
              </div>
              <div className="panel-body flex items-end justify-between gap-4">
                <Stat
                  label="Last price"
                  value={m ? fmtPrice(m.price, s) : "—"}
                  size="lg"
                  sub={
                    m?.micro?.available
                      ? `spread ${((m.micro.spread_rel ?? 0) * 10000).toFixed(1)} bps`
                      : "book unavailable"
                  }
                />
                {open[0] && (
                  <div className="text-right">
                    <div className="text-micro text-ink-400">Latest</div>
                    <div
                      className={`num text-tick mt-0.5 ${
                        open[0].direction === "LONG" ? "text-long" : "text-short"
                      }`}
                    >
                      {open[0].direction} {open[0].timeframe}
                    </div>
                  </div>
                )}
              </div>
            </button>
          );
        })}
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-[1fr_340px] gap-4">
        <div className="space-y-4">
          <Panel
            title="Open signals"
            action={
              <Link href="/history" className="text-micro text-ink-400 hover:text-ink-200">
                Full history
              </Link>
            }
            dense
          >
            <div className="p-3">
              {error ? (
                <ErrorState error={error} retry={load} />
              ) : signals === null ? (
                <Skeleton rows={3} />
              ) : signals.length === 0 ? (
                <EmptyState
                  title="No open signals right now"
                  body="The engine publishes only when a setup clears its gates. Quiet periods are normal and are not a fault."
                  action={
                    <Link href="/history" className="btn-ghost">
                      Look at closed signals instead
                    </Link>
                  }
                />
              ) : (
                <div className="grid gap-3 md:grid-cols-2">
                  {signals.map((s) => (
                    <SignalCard key={s.signal_id} signal={s} />
                  ))}
                </div>
              )}
            </div>
          </Panel>

          <Panel title="Watching — what the engine saw and did not trade" dense>
            <div className="p-3">
              {watch === null ? (
                <Skeleton rows={2} />
              ) : watch.length === 0 ? (
                <p className="text-tick text-ink-400">Nothing on the watchlist. The engine has no partial setups in view.</p>
              ) : (
                <ul className="divide-y divide-edge/60">
                  {watch.slice(0, 8).map((c) => (
                    <li key={c.id} className="py-2 flex flex-wrap items-start justify-between gap-2">
                      <div className="flex items-center gap-2">
                        <span className="text-tick text-ink-100">{c.symbol.replace("USDT", "")}</span>
                        <Chip tone="muted">{c.timeframe}</Chip>
                        <span className={`text-micro ${c.direction === "LONG" ? "text-long" : c.direction === "SHORT" ? "text-short" : "text-ink-400"}`}>
                          {c.direction}
                        </span>
                        <Chip tone={c.state === "SETUP_FORMING" ? "warn" : "muted"}>
                          {c.state.replace("_", " ").toLowerCase()}
                        </Chip>
                        <span className="num text-micro text-ink-400">strength {c.strength}</span>
                      </div>
                      <p className="text-micro text-ink-400 max-w-md">
                        {(c.blockers ?? []).slice(0, 2).join(" ")}
                      </p>
                    </li>
                  ))}
                </ul>
              )}
              <p className="text-micro text-ink-400 mt-3 border-t border-edge pt-2.5 leading-relaxed">
                NO TRADE is a real output. Contradictory components, a counter-trend setup, or a blocked
                gate all land here with their reason rather than being forced into a call.
              </p>
            </div>
          </Panel>

          {snapshot && (
            <Panel
              title={`${assetName(focus)} order flow`}
              action={
                <Link
                  href={`/asset/${focus}`}
                  className="text-micro text-ink-400 hover:text-ink-200"
                >
                  Asset detail
                </Link>
              }
            >
              <div className="grid md:grid-cols-2 gap-5">
                <OrderFlowPanel micro={snapshot.micro} />
                <CvdPanel snapshot={snapshot} />
              </div>
            </Panel>
          )}
        </div>

        <div className="space-y-4">
          <Panel title={`${assetName(focus)} book`} dense>
            {snapshot ? (
              <div className="py-2">
                <DepthLadder snapshot={snapshot} levels={9} />
              </div>
            ) : (
              <div className="p-3">
                <Skeleton rows={6} />
              </div>
            )}
          </Panel>

          <Panel title="Engine state">
            <div className="space-y-2.5">
              <div className="flex items-center justify-between">
                <span className="text-micro text-ink-400">Feed</span>
                <span className={`text-tick ${connected ? "text-long" : "text-warn"}`}>
                  {connected ? "Connected" : everConnected ? "Reconnecting" : "Connecting"}
                </span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-micro text-ink-400">Venues live</span>
                <span className="num text-tick">
                  {health?.venues_live ?? 0}/{health?.min_venues_required ?? 2} required
                </span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-micro text-ink-400">Feed lag</span>
                <span className="num text-tick">
                  {health?.max_staleness_ms != null
                    ? `${(health.max_staleness_ms / 1000).toFixed(1)}s`
                    : "—"}
                </span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-micro text-ink-400">Strategy version</span>
                <span className="num text-tick">{health?.strategy_version ?? "—"}</span>
              </div>
              <p className="text-micro text-ink-400 border-t border-edge pt-2.5 leading-relaxed">
                Signals require at least two venues reporting fresh data. Below that the engine stops
                publishing rather than trusting a single book, which is the easiest thing in crypto
                to paint.
              </p>
            </div>
          </Panel>
        </div>
      </div>
    </div>
  );
}
