"use client";

import clsx from "clsx";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

import { clearTokens, getToken } from "@/lib/api";
import { assetName, price as fmtPrice } from "@/lib/format";
import type { Health, MarketSnapshot } from "@/lib/types";
import { useLive } from "@/lib/useLive";
import { DataDelayed, SimulatedTag } from "./ui";

const LINKS = [
  { href: "/dashboard", label: "Dashboard" },
  { href: "/market", label: "Market" },
  { href: "/order-flow", label: "Order flow" },
  { href: "/smart-money", label: "Smart money" },
  { href: "/history", label: "History" },
  { href: "/performance", label: "Track record" },
  { href: "/api-docs", label: "API" },
];

export function Nav() {
  const pathname = usePathname();
  const [signedIn, setSignedIn] = useState(false);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    setSignedIn(Boolean(getToken()));
  }, [pathname]);

  return (
    <header className="sticky top-0 z-40 bg-base-900/95 backdrop-blur border-b border-edge">
      <div className="mx-auto max-w-[1600px] px-4 h-12 flex items-center gap-6">
        <Link href="/" className="flex items-center gap-2 shrink-0">
          <Mark />
          <span className="text-sm font-semibold tracking-tight">SignalProof</span>
        </Link>

        <nav className="hidden lg:flex items-center gap-1" aria-label="Main">
          {LINKS.map((l) => (
            <Link
              key={l.href}
              href={l.href}
              className={clsx(
                "px-2.5 h-7 inline-flex items-center rounded-panel text-tick transition-colors",
                pathname.startsWith(l.href)
                  ? "text-ink-100 bg-base-700"
                  : "text-ink-300 hover:text-ink-100"
              )}
            >
              {l.label}
            </Link>
          ))}
        </nav>

        <div className="ml-auto flex items-center gap-2">
          <Link href="/pricing" className="hidden sm:inline-flex text-tick text-ink-300 hover:text-ink-100 px-2">
            Pricing
          </Link>
          {signedIn ? (
            <>
              <Link href="/account" className="btn-ghost h-7 text-tick">Account</Link>
              <button
                className="hidden sm:inline-flex btn-ghost h-7 text-tick"
                onClick={() => { clearTokens(); setSignedIn(false); window.location.href = "/"; }}
              >
                Sign out
              </button>
            </>
          ) : (
            <>
              <Link href="/login" className="btn-ghost h-7 text-tick">Sign in</Link>
              <Link href="/signup" className="btn-primary h-7 text-tick">Start free</Link>
            </>
          )}
          <button
            className="lg:hidden btn-ghost h-7 w-8 px-0"
            aria-label="Menu"
            aria-expanded={open}
            onClick={() => setOpen((o) => !o)}
          >
            <svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden>
              <path d="M1 3h12M1 7h12M1 11h12" stroke="currentColor" strokeWidth="1.4" />
            </svg>
          </button>
        </div>
      </div>

      {open && (
        <nav className="lg:hidden border-t border-edge px-4 py-2 grid grid-cols-2 gap-1" aria-label="Mobile">
          {LINKS.map((l) => (
            <Link
              key={l.href}
              href={l.href}
              onClick={() => setOpen(false)}
              className="px-2 h-9 inline-flex items-center rounded-panel text-tick text-ink-200 hover:bg-base-700"
            >
              {l.label}
            </Link>
          ))}
        </nav>
      )}
    </header>
  );
}

function Mark() {
  // A chain link over a candle: the ledger and the market, which is the product.
  return (
    <svg width="18" height="18" viewBox="0 0 18 18" fill="none" aria-hidden>
      <rect x="1" y="1" width="16" height="16" rx="2" className="stroke-edge" strokeWidth="1.2" />
      <path d="M5 12.5V7.5M5 6V5M5 15v-1" className="stroke-long" strokeWidth="1.4" strokeLinecap="round" />
      <rect x="3.4" y="7.5" width="3.2" height="5" rx="0.6" className="fill-long" />
      <path d="M12 5.5v7M12 4v-1M12 14v-1" className="stroke-short" strokeWidth="1.4" strokeLinecap="round" />
      <rect x="10.4" y="6.5" width="3.2" height="4" rx="0.6" className="fill-short" />
    </svg>
  );
}

/**
 * Live ticker rail. Prices, engine state and feed health on one line, so the
 * question "can I trust what I'm looking at right now" is always answered
 * without scrolling.
 */
export function StatusBar() {
  const { markets, health, connected, everConnected } = useLive();
  const symbols = Object.keys(markets);

  return (
    <div className="border-b border-edge bg-base-800">
      <div className="mx-auto max-w-[1600px] px-4 h-9 flex items-center gap-5 overflow-x-auto">
        {symbols.length === 0 ? (
          <span className="text-micro text-ink-400">
            {everConnected ? "Waiting for market data…" : "Connecting to market feed…"}
          </span>
        ) : (
          symbols.map((s) => <Ticker key={s} symbol={s} snapshot={markets[s]} />)
        )}

        <div className="ml-auto flex items-center gap-3 shrink-0 pl-4">
          {health?.is_simulated && <SimulatedTag />}
          {health?.degraded ? (
            <DataDelayed compact />
          ) : (
            <span className="inline-flex items-center gap-1.5 text-micro text-ink-300">
              <span className={clsx(connected ? "live-dot" : "w-1.5 h-1.5 rounded-full bg-ink-400")} />
              {connected ? `${health?.venues_live ?? 0} venues live` : "Reconnecting"}
            </span>
          )}
        </div>
      </div>
    </div>
  );
}

function Ticker({ symbol, snapshot }: { symbol: string; snapshot: MarketSnapshot }) {
  const series = snapshot.price_series ?? [];
  const change =
    series.length > 2 && series[0] ? ((series[series.length - 1] - series[0]) / series[0]) * 100 : null;

  return (
    <Link href={`/asset/${symbol}`} className="flex items-baseline gap-2 shrink-0 group">
      <span className="text-micro text-ink-400 group-hover:text-ink-200">{assetName(symbol)}</span>
      <span className="num text-tick text-ink-100">{fmtPrice(snapshot.price, symbol)}</span>
      {change !== null && (
        <span className={clsx("num text-micro", change >= 0 ? "text-long" : "text-short")}>
          {change >= 0 ? "+" : ""}
          {change.toFixed(2)}%
        </span>
      )}
    </Link>
  );
}

export function Footer() {
  return (
    <footer className="mt-12 border-t border-edge">
      <div className="mx-auto max-w-[1600px] px-4 py-8 grid gap-8 md:grid-cols-[2fr_1fr_1fr]">
        <div>
          <div className="flex items-center gap-2">
            <Mark />
            <span className="text-sm font-semibold">SignalProof</span>
          </div>
          <p className="text-tick text-ink-300 mt-3 max-w-md leading-relaxed">
            Automated, impersonal market analytics for BTC, ETH and SOL. Every signal is published to
            an append-only ledger before its outcome is known, and every outcome — including the
            losses — stays on the record permanently.
          </p>
        </div>

        <nav aria-label="Product">
          <h3 className="text-micro text-ink-400 mb-2">Product</h3>
          <ul className="space-y-1.5 text-tick text-ink-300">
            <li><Link href="/dashboard" className="hover:text-ink-100">Live dashboard</Link></li>
            <li><Link href="/performance" className="hover:text-ink-100">Track record</Link></li>
            <li><Link href="/history" className="hover:text-ink-100">Every signal</Link></li>
            <li><Link href="/pricing" className="hover:text-ink-100">Pricing</Link></li>
          </ul>
        </nav>

        <nav aria-label="Verify">
          <h3 className="text-micro text-ink-400 mb-2">Verify</h3>
          <ul className="space-y-1.5 text-tick text-ink-300">
            <li><Link href="/api-docs" className="hover:text-ink-100">API and hash verification</Link></li>
            <li><Link href="/api-docs#methodology" className="hover:text-ink-100">Methodology</Link></li>
            <li>
              <a
                href={`${process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"}/v1/export/signals.csv`}
                className="hover:text-ink-100"
              >
                Download the full log (CSV)
              </a>
            </li>
          </ul>
        </nav>
      </div>

      <div className="border-t border-edge">
        <p className="mx-auto max-w-[1600px] px-4 py-4 text-micro text-ink-400 leading-relaxed">
          SignalProof publishes automated market analytics for informational and educational purposes.
          Nothing here is personalised investment advice or a recommendation tailored to your
          circumstances — every subscriber sees identical, algorithmically generated output. Trading
          cryptocurrency carries substantial risk of loss. Past performance does not predict future
          results. We do not custody funds, execute trades, or manage accounts.
        </p>
      </div>
    </footer>
  );
}
