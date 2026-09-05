"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { Panel, Skeleton } from "@/components/ui";
import { getPricing } from "@/lib/api";

export default function PricingPage() {
  const [pricing, setPricing] = useState<any>(null);

  useEffect(() => {
    getPricing().then(setPricing).catch(() => setPricing(null));
  }, []);

  const plans = pricing?.plans ?? [];

  return (
    <div className="space-y-6 max-w-5xl">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Pricing</h1>
        <p className="text-ink-200 mt-2 max-w-prose leading-relaxed">
          The proof is free for everyone. What you pay for is speed, coverage and filtering — not
          access to the evidence.
        </p>
      </div>

      {!pricing ? (
        <Skeleton rows={5} />
      ) : (
        <div className="grid md:grid-cols-2 gap-4">
          {plans.map((plan: any) => {
            const pro = plan.id === "pro";
            return (
              <section
                key={plan.id}
                className={`panel ${pro ? "border-long/40" : ""}`}
              >
                <div className="panel-head">
                  <span className="panel-title">{plan.name}</span>
                  {pro && <span className="text-micro text-long">Everything, in real time</span>}
                </div>
                <div className="panel-body">
                  <div className="flex items-baseline gap-1.5">
                    <span className="num text-3xl">${plan.price}</span>
                    {plan.interval && <span className="text-tick text-ink-400">/{plan.interval}</span>}
                  </div>

                  <ul className="mt-5 space-y-2.5">
                    {plan.features.map((f: string) => (
                      <li key={f} className="flex gap-2.5 text-tick text-ink-200">
                        <span className={pro ? "text-long" : "text-ink-400"} aria-hidden>·</span>
                        <span>{f}</span>
                      </li>
                    ))}
                  </ul>

                  <Link
                    href={pro ? "/account/billing" : "/signup"}
                    className={`${pro ? "btn-primary" : "btn-ghost"} w-full mt-6`}
                  >
                    {pro ? "Upgrade to Pro" : "Create a free account"}
                  </Link>
                </div>
              </section>
            );
          })}
        </div>
      )}

      <Panel title="Free for everyone, on every plan">
        <ul className="grid sm:grid-cols-2 gap-2.5">
          {(pricing?.always_free ?? [
            "The public track record",
            "Every closed signal, including losses",
            "Hash and chain verification",
            "CSV export of the full signal log",
          ]).map((f: string) => (
            <li key={f} className="flex gap-2.5 text-tick text-ink-200">
              <span className="text-long" aria-hidden>·</span>
              <span>{f}</span>
            </li>
          ))}
        </ul>
        <p className="text-micro text-ink-400 mt-4 max-w-prose leading-relaxed">
          {pricing?.note ?? "The proof is free. The product is the speed and the filtering."} If the
          record were only visible to paying customers, it would prove nothing to anyone deciding
          whether to become one.
        </p>
      </Panel>

      <Panel title="Questions people actually ask">
        <dl className="grid md:grid-cols-2 gap-x-8 gap-y-5">
          {[
            {
              q: "Do you guarantee returns?",
              a: "No, and we would not believe anyone who did. We publish signals before their outcome is known and then publish the outcome, whatever it is. That is the entire offer.",
            },
            {
              q: "Is this financial advice?",
              a: "No. Every subscriber receives identical, algorithmically generated analytics. Nothing is tailored to your circumstances, and we never custody funds or place trades.",
            },
            {
              q: "Why is it only $10?",
              a: "Because the engine costs roughly the same to run for a thousand subscribers as for ten. Charging $99 would mean charging for exclusivity we do not actually provide.",
            },
            {
              q: "Can I cancel?",
              a: "Any time, and you keep access until the end of the period you paid for. Your account can be deactivated on request; the signal ledger itself is never altered.",
            },
            {
              q: "What happens when the data breaks?",
              a: "Signal generation pauses and the interface shows a data-delayed state. We would rather publish nothing than publish a signal built on a stale order book.",
            },
            {
              q: "Do whale alerts drive the signals?",
              a: "No. On-chain and smart-money data is displayed as context and scored at exactly zero for BTC, ETH and SOL, because the evidence for it on majors is weak. We say so on the page rather than implying otherwise.",
            },
          ].map((item) => (
            <div key={item.q}>
              <dt className="text-tick text-ink-100 font-medium">{item.q}</dt>
              <dd className="text-tick text-ink-300 mt-1 leading-relaxed">{item.a}</dd>
            </div>
          ))}
        </dl>
      </Panel>
    </div>
  );
}
