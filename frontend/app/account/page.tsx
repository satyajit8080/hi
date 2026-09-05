"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { Chip, ErrorState, KeyValue, Panel, Skeleton } from "@/components/ui";
import { clearTokens, getMe, getToken } from "@/lib/api";
import { datetime } from "@/lib/format";

export default function AccountPage() {
  const router = useRouter();
  const [me, setMe] = useState<any>(null);
  const [error, setError] = useState<Error | null>(null);

  useEffect(() => {
    if (!getToken()) {
      router.push("/login");
      return;
    }
    getMe().then(setMe).catch(setError);
  }, [router]);

  if (error) return <ErrorState error={error} />;
  if (!me) return <Skeleton rows={5} className="max-w-2xl" />;

  const e = me.entitlements;

  return (
    <div className="max-w-3xl space-y-4">
      <h1 className="text-xl font-semibold tracking-tight">Account</h1>

      <Panel
        title="Plan"
        action={<Chip tone={me.tier === "pro" ? "long" : "muted"}>{me.tier}</Chip>}
      >
        <div className="space-y-2">
          <KeyValue k="Email" v={me.email} mono={false} />
          <KeyValue k="Markets" v={e.symbols.map((s: string) => s.replace("USDT", "")).join(", ")} mono={false} />
          <KeyValue k="Signal delay" v={e.delay_minutes === 0 ? "none — real time" : `${e.delay_minutes} minutes`} mono={false} />
          <KeyValue k="History" v={e.history_days ? `${e.history_days} days` : "complete archive"} mono={false} />
          <KeyValue k="Timeframes" v={e.timeframes.join(", ")} mono={false} />
          <KeyValue k="Alerts" v={e.alerts ? "enabled" : "Pro only"} mono={false} />
        </div>

        {me.tier !== "pro" && (
          <Link href="/account/billing" className="btn-primary w-full mt-4">
            Upgrade to Pro — $10/month
          </Link>
        )}
      </Panel>

      {me.subscription && (
        <Panel title="Subscription">
          <div className="space-y-2">
            <KeyValue k="Status" v={me.subscription.status} mono={false} />
            <KeyValue k="Provider" v={me.subscription.provider} mono={false} />
            <KeyValue k="Price" v={`$${me.subscription.price_usd}/month`} />
            {me.subscription.current_period_end && (
              <KeyValue k="Renews" v={datetime(me.subscription.current_period_end)} mono={false} />
            )}
          </div>
          <Link href="/account/billing" className="btn-ghost w-full mt-3">Manage billing</Link>
        </Panel>
      )}

      <Panel title="Alerts">
        <p className="text-tick text-ink-300">
          Choose which signals reach you, on which channel, and at what strength.
        </p>
        <Link href="/account/alerts" className="btn-ghost w-full mt-3">Alert settings</Link>
      </Panel>

      <Panel title="Sign out">
        <p className="text-tick text-ink-400 mb-3 leading-relaxed">
          Signing out clears this device only. Deactivating an account never alters the signal
          ledger — published records stay published.
        </p>
        <button
          className="btn-ghost w-full"
          onClick={() => { clearTokens(); router.push("/"); }}
        >
          Sign out
        </button>
      </Panel>
    </div>
  );
}
