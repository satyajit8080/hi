"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { RegimeBadge } from "@/components/panels";
import { Bar, Chip, EmptyState, ErrorState, Panel, Skeleton, Stat } from "@/components/ui";
import { getMarketIntel } from "@/lib/api";
import { assetName, compact, price as fmtPrice, ratio, timeAgo } from "@/lib/format";

const COMPONENT_LABELS: Record<string, string> = {
  trend_clarity: "Trend clarity",
  regime_stability: "Regime agreement",
  momentum: "Momentum",
  volatility: "Volatility (inverted)",
  liquidity: "Liquidity (inverted spread)",
  order_flow: "Order flow",
  derivatives: "Derivatives",
  data_quality: "Data quality",
};

export default function MarketPage() {
  const [intel, setIntel] = useState<any>(null);
  const [error, setError] = useState<Error | null>(null);

  const load = () => getMarketIntel().then((r) => { setIntel(r); setError(null); }).catch(setError);
  useEffect(() => {
    load();
    const id = setInterval(load, 30000);
    return () => clearInterval(id);
  }, []);

  if (error) {
    return (
      <div className="max-w-xl">
        <ErrorState error={error} retry={load} />
      </div>
    );
  }
  if (!intel) return <Skeleton rows={8} />;

  const assets: any[] = intel.assets ?? [];
  const symbols = assets.map((a) => a.symbol);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Market intelligence</h1>
          <p className="text-tick text-ink-400 mt-0.5 max-w-prose">
            What kind of market this is, per asset, and how tradeable the conditions are.
          </p>
        </div>
        <span className="text-micro text-ink-400">Computed {timeAgo(intel.generated_at)}</span>
      </div>

      <div className="grid lg:grid-cols-[280px_1fr] gap-4">
        <Panel title="Market conditions score">
          <Stat label="Across BTC, ETH and SOL" value={`${intel.overall_conditions_score}/100`} size="lg" />
          <p className="text-micro text-ink-400 mt-3 leading-relaxed">{intel.explanation}</p>
          <div className="mt-3 border-t border-edge pt-3">
            <h3 className="text-micro text-ink-400 mb-1.5">Weights</h3>
            <ul className="space-y-1">
              {Object.entries(intel.weights as Record<string, number>).map(([k, w]) => (
                <li key={k} className="flex justify-between text-micro">
                  <span className="text-ink-300">{COMPONENT_LABELS[k] ?? k}</span>
                  <span className="num text-ink-200">{(w * 100).toFixed(0)}%</span>
                </li>
              ))}
            </ul>
          </div>
        </Panel>

        <Panel title="Correlation of hourly returns, last 48 bars" dense>
          <table className="w-full">
            <thead>
              <tr>
                <th className="th"></th>
                {symbols.map((s) => <th key={s} className="th text-right">{assetName(s)}</th>)}
              </tr>
            </thead>
            <tbody>
              {symbols.map((a) => (
                <tr key={a}>
                  <td className="td text-ink-300">{assetName(a)}</td>
                  {symbols.map((b) => {
                    const v = intel.correlation?.[a]?.[b];
                    return (
                      <td key={b} className={`td num text-right ${a === b ? "text-ink-400" : v != null && v > 0.8 ? "text-warn" : ""}`}>
                        {v == null ? "—" : v.toFixed(2)}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
          <p className="px-3 pb-3 text-micro text-ink-400 leading-relaxed">
            Above 0.8 the three markets are moving as one, which means three signals in the same
            direction carry roughly one signal&apos;s worth of diversification.
          </p>
        </Panel>
      </div>

      {assets.length === 0 ? (
        <EmptyState title="No assets scored yet" body="The engine publishes market intelligence after its first cycle." />
      ) : (
        <div className="grid lg:grid-cols-3 gap-4">
          {assets.map((a) => (
            <Panel
              key={a.symbol}
              title={`${assetName(a.symbol)} · ${fmtPrice(a.price, a.symbol)}`}
              action={<Link href={`/asset/${a.symbol}`} className="text-micro text-ink-400 hover:text-ink-200">Detail</Link>}
            >
              <div className="flex items-center justify-between">
                <RegimeBadge regime={a.regime} />
                <span className="num text-lg">{a.score}<span className="text-micro text-ink-400">/100</span></span>
              </div>

              <dl className="mt-3 space-y-2">
                {Object.entries(a.components as Record<string, number>).map(([k, v]) => (
                  <div key={k}>
                    <div className="flex justify-between text-micro">
                      <dt className="text-ink-300">{COMPONENT_LABELS[k] ?? k}</dt>
                      <dd className="num text-ink-200">{(v * 100).toFixed(0)}</dd>
                    </div>
                    <div className="mt-0.5 h-1 bg-base-700 rounded-panel overflow-hidden">
                      <div className="h-full bg-ink-300" style={{ width: `${v * 100}%` }} />
                    </div>
                  </div>
                ))}
              </dl>

              <div className="mt-3 border-t border-edge pt-2.5 grid grid-cols-2 gap-x-4 gap-y-1 text-micro">
                <Row k="Trend" v={ratio(a.trend_score, 2)} />
                <Row k="RSI" v={ratio(a.rsi, 0)} />
                <Row k="Vol pct" v={a.vol_percentile != null ? `${(a.vol_percentile * 100).toFixed(0)}%` : "—"} />
                <Row k="Spread z" v={ratio(a.spread_z, 1)} />
                <Row k="Book lean" v={ratio(a.obi_persistent, 2)} />
                <Row k="CVD div" v={ratio(a.cvd_divergence, 2)} />
                <Row k="Funding" v={a.funding_rate != null ? `${(a.funding_rate * 100).toFixed(4)}%` : "—"} />
                <Row k="OI Δ 1h" v={a.oi_change_pct != null ? `${a.oi_change_pct.toFixed(2)}%` : "—"} />
                <Row k="Liq long" v={`$${compact(a.liq_long_usd)}`} />
                <Row k="Liq short" v={`$${compact(a.liq_short_usd)}`} />
              </div>

              <div className="mt-2.5 flex items-center gap-2">
                <span className="text-micro text-ink-400">Smart money z</span>
                <span className="num text-micro text-ink-300">{ratio(a.smart_money_z, 2)}</span>
                <Chip tone="muted">context only</Chip>
              </div>

              <p className="mt-2 text-micro text-ink-400">
                {a.regimes_by_tf && Object.entries(a.regimes_by_tf).map(([tf, r]) => `${tf}: ${String(r).replace(/_/g, " ")}`).join(" · ")}
              </p>
            </Panel>
          ))}
        </div>
      )}

      {intel.narrative?.summary && (
        <Panel title="In plain language">
          <p className="text-tick text-ink-200 leading-relaxed max-w-prose">{intel.narrative.summary}</p>
          <p className="text-micro text-ink-400 mt-2">
            Generated from the numbers above by a fixed template. It adds no judgement of its own.
          </p>
        </Panel>
      )}
    </div>
  );
}

function Row({ k, v }: { k: string; v: string }) {
  return (
    <div className="flex justify-between">
      <span className="text-ink-400">{k}</span>
      <span className="num text-ink-200">{v}</span>
    </div>
  );
}
