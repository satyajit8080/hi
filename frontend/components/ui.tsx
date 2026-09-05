"use client";

import clsx from "clsx";
import Link from "next/link";
import type { ReactNode } from "react";

/* ── panel ────────────────────────────────────────────────────────────────── */

export function Panel({
  title, action, children, className, dense, id,
}: {
  title?: ReactNode;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
  dense?: boolean;
  /** Anchor target, so deep links like /api-docs#methodology land correctly. */
  id?: string;
}) {
  return (
    <section id={id} className={clsx("panel", className)}>
      {title && (
        <header className="panel-head">
          <h2 className="panel-title">{title}</h2>
          {action}
        </header>
      )}
      <div className={dense ? "" : "panel-body"}>{children}</div>
    </section>
  );
}

/* ── numbers ──────────────────────────────────────────────────────────────── */

export function Stat({
  label, value, sub, tone = "neutral", size = "md",
}: {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  tone?: "neutral" | "long" | "short" | "warn";
  size?: "sm" | "md" | "lg";
}) {
  return (
    <div>
      <div className="text-micro text-ink-400">{label}</div>
      <div
        className={clsx(
          "num mt-0.5 leading-none",
          size === "lg" && "text-2xl",
          size === "md" && "text-lg",
          size === "sm" && "text-sm",
          tone === "long" && "text-long",
          tone === "short" && "text-short",
          tone === "warn" && "text-warn",
          tone === "neutral" && "text-ink-100"
        )}
      >
        {value}
      </div>
      {sub && <div className="text-micro text-ink-400 mt-1">{sub}</div>}
    </div>
  );
}

export function Delta({ value, digits = 2 }: { value: number | null | undefined; digits?: number }) {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return <span className="num text-ink-400">—</span>;
  }
  return (
    <span className={clsx("num", value > 0 ? "text-long" : value < 0 ? "text-short" : "text-ink-200")}>
      {value > 0 ? "+" : ""}
      {value.toFixed(digits)}%
    </span>
  );
}

/* ── badges ───────────────────────────────────────────────────────────────── */

export function DirectionTag({ direction, size = "md" }: { direction: string; size?: "sm" | "md" }) {
  const long = direction === "LONG";
  return (
    <span
      className={clsx(
        "inline-flex items-center rounded-panel font-medium tracking-wide",
        size === "sm" ? "text-micro px-1.5 h-4" : "text-tick px-2 h-5",
        long ? "bg-long-dim text-long" : "bg-short-dim text-short"
      )}
    >
      {direction}
    </span>
  );
}

export function OutcomeTag({ outcome, status }: { outcome: string | null; status?: string }) {
  if (!outcome) {
    return (
      <span className="inline-flex items-center gap-1.5 text-tick text-ink-200">
        <span className="live-dot" />
        {status === "ACTIVE" ? "Open" : status?.replace(/_/g, " ").toLowerCase() ?? "Open"}
      </span>
    );
  }
  const map: Record<string, string> = {
    WIN: "text-long",
    LOSS: "text-short",
    EXPIRED: "text-ink-300",
    CANCELLED: "text-ink-400",
  };
  const label: Record<string, string> = {
    WIN: "Win",
    LOSS: "Loss",
    EXPIRED: "Expired",
    CANCELLED: "Cancelled",
  };
  return <span className={clsx("text-tick", map[outcome])}>{label[outcome] ?? outcome}</span>;
}

export function Chip({
  children, tone = "neutral",
}: {
  children: ReactNode;
  tone?: "neutral" | "long" | "short" | "warn" | "muted";
}) {
  return (
    <span
      className={clsx(
        "inline-flex items-center h-5 px-1.5 rounded-panel text-micro border",
        tone === "neutral" && "border-edge text-ink-200",
        tone === "muted" && "border-edge/60 text-ink-400",
        tone === "long" && "border-long/40 text-long bg-long-deep",
        tone === "short" && "border-short/40 text-short bg-short-deep",
        tone === "warn" && "border-warn/40 text-warn bg-warn-dim/40"
      )}
    >
      {children}
    </span>
  );
}

export function SimulatedTag() {
  return (
    <Chip tone="warn">
      <span className="tracking-wide">Simulated data</span>
    </Chip>
  );
}

/* ── states ───────────────────────────────────────────────────────────────── */

export function Skeleton({ rows = 3, className }: { rows?: number; className?: string }) {
  return (
    <div className={clsx("space-y-2", className)} aria-busy="true" aria-label="Loading">
      {Array.from({ length: rows }).map((_, i) => (
        <div
          key={i}
          className="h-8 bg-base-700 rounded-panel"
          style={{ opacity: 1 - i * 0.12, animation: "pulse-soft 2.6s ease-in-out infinite" }}
        />
      ))}
    </div>
  );
}

export function ErrorState({ error, retry }: { error: Error | string; retry?: () => void }) {
  const message = typeof error === "string" ? error : error.message;
  return (
    <div className="border border-short/30 bg-short-deep rounded-panel p-4">
      <div className="text-sm text-short font-medium">This did not load</div>
      <p className="text-tick text-ink-200 mt-1.5 max-w-prose">{message}</p>
      {retry && (
        <button className="btn-ghost mt-3" onClick={retry}>
          Try again
        </button>
      )}
    </div>
  );
}

export function EmptyState({
  title, body, action,
}: {
  title: string;
  body: string;
  action?: ReactNode;
}) {
  return (
    <div className="border border-dashed border-edge rounded-panel p-6 text-center">
      <div className="text-sm text-ink-200">{title}</div>
      <p className="text-tick text-ink-400 mt-1.5 max-w-md mx-auto">{body}</p>
      {action && <div className="mt-4 flex justify-center">{action}</div>}
    </div>
  );
}

/**
 * The safety state. When feeds degrade, the engine stops publishing and the UI
 * says so in plain language rather than showing prices that may be minutes old
 * as if they were live.
 */
export function DataDelayed({
  flags = [], stalenessMs, venuesLive, minVenues, compact: isCompact,
}: {
  flags?: string[];
  stalenessMs?: number | null;
  venuesLive?: number;
  minVenues?: number;
  compact?: boolean;
}) {
  if (isCompact) {
    return (
      <span className="inline-flex items-center gap-1.5 text-micro text-warn">
        <span className="w-1.5 h-1.5 rounded-full bg-warn" />
        Data delayed
      </span>
    );
  }
  return (
    <div className="border border-warn/40 bg-warn-dim/30 rounded-panel p-3">
      <div className="flex items-center gap-2">
        <span className="w-1.5 h-1.5 rounded-full bg-warn" />
        <span className="text-tick font-medium text-warn">Data delayed</span>
      </div>
      <p className="text-tick text-ink-200 mt-1.5 max-w-prose">
        Market feeds are behind or incomplete, so signal generation is paused. Nothing new will be
        published until the feeds recover — showing you a signal built on stale data would be worse
        than showing you none.
      </p>
      <dl className="mt-2.5 flex flex-wrap gap-x-6 gap-y-1 text-micro text-ink-300">
        {venuesLive !== undefined && (
          <div>
            <dt className="inline text-ink-400">Venues live </dt>
            <dd className="inline num">{venuesLive}/{minVenues ?? 2} required</dd>
          </div>
        )}
        {stalenessMs !== undefined && stalenessMs !== null && (
          <div>
            <dt className="inline text-ink-400">Feed lag </dt>
            <dd className="inline num">{(stalenessMs / 1000).toFixed(1)}s</dd>
          </div>
        )}
        {flags.length > 0 && (
          <div>
            <dt className="inline text-ink-400">Detail </dt>
            <dd className="inline num">{flags.slice(0, 3).join(", ")}</dd>
          </div>
        )}
      </dl>
    </div>
  );
}

export function LockedNotice({ reason }: { reason?: string }) {
  return (
    <div className="border border-edge bg-base-700/50 rounded-panel p-3">
      <p className="text-tick text-ink-200">
        {reason ?? "Entry, stop and targets unlock later on the free plan."}
      </p>
      <Link href="/pricing" className="btn-primary mt-2.5">
        Upgrade to Pro — $10/month
      </Link>
    </div>
  );
}

/* ── small layout helpers ─────────────────────────────────────────────────── */

export function Bar({
  value, tone = "neutral",
}: {
  value: number; // -1 .. 1
  tone?: "neutral" | "signed";
}) {
  const clamped = Math.max(-1, Math.min(1, value));
  const width = Math.abs(clamped) * 50;
  const positive = clamped >= 0;
  return (
    <div className="relative h-1.5 bg-base-700 rounded-panel overflow-hidden" role="img"
         aria-label={`Imbalance ${(clamped * 100).toFixed(0)} percent`}>
      <div className="absolute inset-y-0 left-1/2 w-px bg-base-500" />
      <div
        className={clsx(
          "absolute inset-y-0",
          tone === "signed" ? (positive ? "bg-long" : "bg-short") : "bg-ink-300"
        )}
        style={{
          width: `${width}%`,
          left: positive ? "50%" : `${50 - width}%`,
        }}
      />
    </div>
  );
}

export function KeyValue({ k, v, mono = true }: { k: string; v: ReactNode; mono?: boolean }) {
  return (
    <div className="flex items-baseline justify-between gap-4 py-1">
      <span className="text-micro text-ink-400">{k}</span>
      <span className={clsx("text-tick text-ink-100", mono && "num")}>{v}</span>
    </div>
  );
}
