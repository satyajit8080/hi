"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { CvdPanel, DepthLadder, LiquidationTape, OrderFlowPanel } from "@/components/panels";
import { Chip, DataDelayed, Panel, Skeleton, Stat } from "@/components/ui";
import { getMethodology } from "@/lib/api";
import { assetName, price as fmtPrice, ratio } from "@/lib/format";
import { useLive } from "@/lib/useLive";

export default function OrderFlowPage() {
  const { markets, health } = useLive();
  const [method, setMethod] = useState<any>(null);
  const symbols = Object.keys(markets);

  useEffect(() => {
    getMethodology().then(setMethod).catch(() => setMethod(null));
  }, []);

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">Order flow</h1>
        <p className="text-tick text-ink-400 mt-0.5 max-w-prose">
          The book, the tape, and what the engine actually does with them.
        </p>
      </div>

      {health?.degraded && (
        <DataDelayed flags={health.flags} stalenessMs={health.max_staleness_ms}
                     venuesLive={health.venues_live} minVenues={health.min_venues_required} />
      )}

      <Panel title="How much weight order flow carries, by timeframe">
        <p className="text-tick text-ink-200 max-w-prose leading-relaxed">
          Book imbalance and order-flow imbalance are strong predictors over seconds to minutes and
          close to worthless over days. So they time entries on the fast charts and are hard-zeroed
          above the hour. This is enforced in the scoring code, not left to judgement.
        </p>
        <div className="mt-3 flex flex-wrap gap-2">
          {method?.orderflow_weight_by_timeframe &&
            Object.entries(method.orderflow_weight_by_timeframe as Record<string, number>).map(
              ([tf, w]) => (
                <Chip key={tf} tone={w > 0 ? "long" : "muted"}>
                  {tf} · weight {Number(w).toFixed(2)}
                </Chip>
              )
            )}
        </div>
      </Panel>

      {symbols.length === 0 ? (
        <Skeleton rows={6} />
      ) : (
        symbols.map((symbol) => {
          const s = markets[symbol];
          return (
            <Panel
              key={symbol}
              title={`${assetName(symbol)} · ${fmtPrice(s.price, symbol)}`}
              action={
                <Link href={`/asset/${symbol}`} className="text-micro text-ink-400 hover:text-ink-200">
                  Asset detail
                </Link>
              }
            >
              <div className="grid lg:grid-cols-4 gap-5">
                <div className="lg:col-span-1">
                  <h3 className="text-micro text-ink-400 mb-2">Book</h3>
                  <DepthLadder snapshot={s} levels={8} />
                </div>
                <div className="lg:col-span-1">
                  <h3 className="text-micro text-ink-400 mb-2">Imbalance readouts</h3>
                  <OrderFlowPanel micro={s.micro} />
                </div>
                <div className="lg:col-span-1">
                  <h3 className="text-micro text-ink-400 mb-2">Flow versus price</h3>
                  <CvdPanel snapshot={s} />
                </div>
                <div className="lg:col-span-1">
                  <h3 className="text-micro text-ink-400 mb-2">Liquidations</h3>
                  <LiquidationTape snapshot={s} />
                </div>
              </div>
            </Panel>
          );
        })
      )}

      <Panel title="How we keep the book honest">
        <div className="grid md:grid-cols-3 gap-6 text-tick text-ink-200 leading-relaxed">
          <div>
            <h3 className="text-ink-100 font-medium mb-1.5">Resting size counts, flickering size does not</h3>
            <p>
              Depth is weighted by how long each level has rested and discounted by distance from the
              touch. Orders that appear and vanish contribute almost nothing, which is what makes
              spoofing and layering expensive rather than free.
            </p>
          </div>
          <div>
            <h3 className="text-ink-100 font-medium mb-1.5">Two venues or nothing</h3>
            <p>
              A single order book is the easiest thing in crypto to paint. Flow that only one exchange
              can see has its weight halved, and below two live venues the engine stops publishing
              entirely.
            </p>
          </div>
          <div>
            <h3 className="text-ink-100 font-medium mb-1.5">A gap means resync, never guess</h3>
            <p>
              Every depth update is checked against its expected sequence number. One missed message
              and the book is marked unsynchronised and rebuilt from a fresh snapshot. A silently
              wrong book produces confidently wrong signals.
            </p>
          </div>
        </div>
      </Panel>
    </div>
  );
}
