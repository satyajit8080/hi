"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { Chip, ErrorState, Panel, Skeleton, Stat } from "@/components/ui";
import { API, getMethodology, verifyChain } from "@/lib/api";
import { ratio, shortHash } from "@/lib/format";

const ENDPOINTS = [
  { method: "GET", path: "/v1/signals/live", desc: "Currently open signals." },
  { method: "GET", path: "/v1/signals/history", desc: "Every published signal. Filter by market, timeframe, outcome or direction." },
  { method: "GET", path: "/v1/signals/{id}", desc: "One signal with its full feature snapshot and lifecycle." },
  { method: "GET", path: "/v1/verify/{id}", desc: "Recompute a signal's hash and return the exact bytes that were hashed." },
  { method: "GET", path: "/v1/verify/chain/full", desc: "Walk the entire chain and report the first broken link." },
  { method: "GET", path: "/v1/anchors", desc: "Daily Merkle roots and their timestamping status." },
  { method: "GET", path: "/v1/anchors/{day}/proof/{id}", desc: "Merkle inclusion proof for one signal on one day." },
  { method: "GET", path: "/v1/export/signals.csv", desc: "The complete signal log as CSV." },
  { method: "GET", path: "/v1/performance", desc: "Aggregate statistics with breakdowns and the equity curve." },
  { method: "GET", path: "/v1/performance/calibration", desc: "Reliability curve — predicted against observed." },
  { method: "GET", path: "/v1/market/{symbol}", desc: "Live book, order flow and liquidations." },
  { method: "GET", path: "/v1/status", desc: "Feed health and whether signal generation is currently paused." },
  { method: "GET", path: "/v1/methodology", desc: "Engine weights, regime gates and the rules below, as data." },
];

export default function ApiDocsPage() {
  const [chain, setChain] = useState<any>(null);
  const [method, setMethod] = useState<any>(null);
  const [error, setError] = useState<Error | null>(null);

  useEffect(() => {
    verifyChain().then(setChain).catch(setError);
    getMethodology().then(setMethod).catch(() => setMethod(null));
  }, []);

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">API and verification</h1>
        <p className="text-tick text-ink-400 mt-0.5 max-w-prose">
          Read endpoints, hash verification and the published methodology. The verification and
          export endpoints need no key — a proof behind a paywall is not a proof.
        </p>
      </div>

      <div className="grid lg:grid-cols-[1fr_360px] gap-4">
        <div className="space-y-4">
          <Panel title="Endpoints" dense>
            <table className="w-full">
              <tbody>
                {ENDPOINTS.map((e) => (
                  <tr key={e.path} className="hover:bg-base-700/40">
                    <td className="td w-14">
                      <span className="num text-micro text-long">{e.method}</span>
                    </td>
                    <td className="td">
                      <a
                        href={`${API}${e.path.replace(/\{[^}]+\}/g, "")}`}
                        className="num text-ink-100 hover:text-long"
                      >
                        {e.path}
                      </a>
                    </td>
                    <td className="td text-ink-400 hidden md:table-cell">{e.desc}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Panel>

          <Panel title="Verifying a signal yourself">
            <ol className="space-y-3 text-tick text-ink-200 leading-relaxed">
              <li>
                <span className="text-ink-100 font-medium">1. Fetch the signal.</span>{" "}
                <code className="num text-ink-300">GET /v1/verify/{"{id}"}</code> returns the stored
                hash, the recomputed hash, and the canonical payload string that was hashed.
              </li>
              <li>
                <span className="text-ink-100 font-medium">2. Hash it yourself.</span> Compute{" "}
                <code className="num text-ink-300">sha256(canonical_payload + prev_hash)</code>. The
                canonical payload is sorted-key JSON with compact separators and every number
                rendered to eight decimal places — no ambiguity about what was hashed.
              </li>
              <li>
                <span className="text-ink-100 font-medium">3. Follow the chain.</span> Each signal
                embeds the previous signal&apos;s hash, so altering an old record breaks every link
                after it. <code className="num text-ink-300">GET /v1/verify/chain/full</code> checks
                all of them and names the first break.
              </li>
              <li>
                <span className="text-ink-100 font-medium">4. Check the timestamp.</span> Each day&apos;s
                hashes form a Merkle tree whose root can be committed to Bitcoin, which pins the
                record to a point in time we do not control.
              </li>
            </ol>

            <pre className="mt-4 text-micro text-ink-300 bg-base-900 border border-edge rounded-panel p-3 overflow-x-auto">
{`# verify one signal from the command line
curl -s ${API}/v1/verify/SIGNAL_ID \\
  | python3 -c "
import sys, json, hashlib
d = json.load(sys.stdin)
h = hashlib.sha256((d['canonical_payload'] + d['prev_hash']).encode()).hexdigest()
print('recomputed:', h)
print('stored    :', d['stored_hash'])
print('MATCH' if h == d['stored_hash'] else 'MISMATCH')
"`}
            </pre>
          </Panel>

          <Panel title="Methodology" id="methodology">
            {!method ? (
              <Skeleton rows={4} />
            ) : (
              <div className="space-y-4">
                <div>
                  <h3 className="text-micro text-ink-400 mb-2">Component weights</h3>
                  <div className="flex flex-wrap gap-2">
                    {Object.entries(method.component_weights as Record<string, number>).map(([k, v]) => (
                      <Chip key={k}>{k.replace(/_/g, " ")} · {Number(v).toFixed(2)}</Chip>
                    ))}
                  </div>
                  <p className="text-micro text-ink-400 mt-2 max-w-prose leading-relaxed">
                    Fixed and published rather than learned. A model that retunes itself is a model
                    whose past signals cannot be reproduced, which would defeat the point of the
                    ledger.
                  </p>
                </div>

                <div>
                  <h3 className="text-micro text-ink-400 mb-2">Intrabar rule</h3>
                  <p className="text-tick text-ink-200 max-w-prose leading-relaxed">{method.intrabar_rule}</p>
                </div>

                <div>
                  <h3 className="text-micro text-ink-400 mb-2">Publication gates</h3>
                  <div className="flex flex-wrap gap-2">
                    <Chip>min venues · {method.data_gates.min_live_venues}</Chip>
                    <Chip>book staleness · {method.data_gates.max_book_staleness_ms} ms</Chip>
                    <Chip>trade staleness · {method.data_gates.max_trade_staleness_ms} ms</Chip>
                    <Chip>min R:R · 1:{ratio(method.min_risk_reward)}</Chip>
                    <Chip>min sample for a win rate · {method.min_calibration_sample}</Chip>
                  </div>
                </div>

                <div>
                  <h3 className="text-micro text-ink-400 mb-2">Strategy versions</h3>
                  <ul className="space-y-1.5">
                    {(method.versions ?? []).map((v: any) => (
                      <li key={v.version} className="text-tick text-ink-200">
                        <span className="num text-ink-100">{v.version}</span>{" "}
                        <span className="text-ink-400">— {v.notes}</span>
                      </li>
                    ))}
                  </ul>
                  <p className="text-micro text-ink-400 mt-2 max-w-prose leading-relaxed">
                    Every signal records the version that produced it, so a change in method can
                    never be used to reinterpret old results.
                  </p>
                </div>
              </div>
            )}
          </Panel>
        </div>

        <div className="space-y-4">
          <Panel title="Chain status">
            {error ? (
              <ErrorState error={error} />
            ) : !chain ? (
              <Skeleton rows={3} />
            ) : (
              <div className="space-y-3">
                <Stat
                  label="Links verified"
                  value={chain.ok ? "all valid" : "break found"}
                  tone={chain.ok ? "long" : "short"}
                />
                <Stat label="Signals in chain" value={chain.signals_in_chain?.toLocaleString() ?? "—"} size="sm" />
                <div>
                  <div className="text-micro text-ink-400">Head</div>
                  <div className="num text-micro text-ink-200 break-all mt-0.5">
                    {shortHash(chain.head, 20)}
                  </div>
                </div>
                {!chain.ok && (
                  <p className="text-micro text-short">
                    First break at {chain.broken_at}: {chain.error}
                  </p>
                )}
              </div>
            )}
          </Panel>

          <Panel title="Access">
            <p className="text-tick text-ink-200 leading-relaxed">
              Verification, performance and CSV export are open to everyone. Live signal endpoints
              follow your plan: Bitcoin on a delay for free, all three markets in real time on Pro.
            </p>
            <Link href="/pricing" className="btn-ghost w-full mt-3">See plans</Link>
          </Panel>

          <Panel title="Interactive docs">
            <p className="text-tick text-ink-200">
              The full OpenAPI schema, with request and response shapes for every endpoint.
            </p>
            <a href={`${API}/docs`} className="btn-ghost w-full mt-3">Open the API reference</a>
          </Panel>
        </div>
      </div>
    </div>
  );
}
