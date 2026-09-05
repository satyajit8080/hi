"use client";

import { useEffect, useState } from "react";

import { Sparkline } from "@/components/charts";
import { SmartMoneyPanel } from "@/components/panels";
import { Chip, EmptyState, Panel, Skeleton } from "@/components/ui";
import { getSmartMoney } from "@/lib/api";
import { assetName, compact, datetime, ratio } from "@/lib/format";

const SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"];

export default function SmartMoneyPage() {
  const [data, setData] = useState<Record<string, any> | null>(null);

  useEffect(() => {
    Promise.all(SYMBOLS.map((s) => getSmartMoney(s).catch(() => null))).then((results) => {
      const map: Record<string, any> = {};
      results.forEach((r, i) => { if (r) map[SYMBOLS[i]] = r; });
      setData(map);
    });
  }, []);

  const disclosure = data ? Object.values(data)[0]?.disclosure : null;

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">Smart money and on-chain flow</h1>
        <p className="text-tick text-ink-400 mt-0.5 max-w-prose">
          Wallet cohorts, exchange flows and large positions — shown in full, and scored at zero.
        </p>
      </div>

      <div className="border border-edge rounded-panel p-4">
        <div className="flex items-center gap-2 mb-2">
          <Chip tone="muted">context only</Chip>
          <span className="text-tick text-ink-100 font-medium">
            This data does not move any signal score
          </span>
        </div>
        <p className="text-tick text-ink-200 max-w-prose leading-relaxed">
          {disclosure?.explanation ??
            "On-chain and whale data is shown for context. Wallet-level flow has a well-documented edge on small caps and a weak, largely narrative one on majors, where exchange custody, ETFs and market makers dominate the flow."}
        </p>
        <p className="text-micro text-ink-400 mt-2.5 max-w-prose leading-relaxed">
          Plenty of products put whale alerts front and centre and let you assume they drive the
          calls. For BTC, ETH and SOL that would be theatre. We show the data because it is genuinely
          interesting context, and we tell you plainly that it contributed nothing.
        </p>
      </div>

      {!data ? (
        <Skeleton rows={6} />
      ) : (
        <div className="grid lg:grid-cols-3 gap-4">
          {SYMBOLS.map((symbol) => {
            const d = data[symbol];
            if (!d) {
              return (
                <Panel key={symbol} title={assetName(symbol)}>
                  <EmptyState title="No data" body="On-chain context is not available for this market." />
                </Panel>
              );
            }
            const series = (d.series ?? []).slice().reverse();
            const flows = series.map((s: any) => Number(s.net_flow_usd ?? 0));
            return (
              <div key={symbol} className="space-y-4">
                <Panel title={`${assetName(symbol)} cohort flow`}>
                  {flows.length > 1 ? (
                    <>
                      <Sparkline values={flows} height={56} tone="neutral" />
                      <div className="mt-2 flex items-baseline justify-between">
                        <span className="text-micro text-ink-400">Latest net flow</span>
                        <span className="num text-tick">
                          ${compact(flows[flows.length - 1])}
                        </span>
                      </div>
                      <div className="flex items-baseline justify-between">
                        <span className="text-micro text-ink-400">Observations</span>
                        <span className="num text-tick">{flows.length}</span>
                      </div>
                    </>
                  ) : (
                    <EmptyState
                      title="No flow history"
                      body="Cohort flow appears once wallet data has been ingested for this market."
                    />
                  )}
                </Panel>
                <SmartMoneyPanel context={d.context} disclosure={d.disclosure} />
              </div>
            );
          })}
        </div>
      )}

      <Panel title="What would have to be true for this to count">
        <div className="grid md:grid-cols-2 gap-6">
          <div>
            <p className="text-tick text-ink-200 leading-relaxed">
              Promotion from context to scoring input is an evidence question, not a configuration
              flag. A wallet cohort is selected on one period and then has to predict returns in a
              later period it was never fitted on. Until that passes, the contribution stays at
              exactly zero — and the engine refuses to start if the flag is switched on without a
              passing record behind it.
            </p>
          </div>
          <ul className="space-y-2">
            {(disclosure?.promotion_requirements ?? []).map((r: string) => (
              <li key={r} className="flex gap-2.5 text-tick text-ink-200">
                <span className="text-ink-400 shrink-0" aria-hidden>·</span>
                <span>{r}</span>
              </li>
            ))}
          </ul>
        </div>
      </Panel>
    </div>
  );
}
