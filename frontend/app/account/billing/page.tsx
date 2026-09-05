"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { Chip, KeyValue, Panel, Skeleton } from "@/components/ui";
import { getMe, getToken, startCheckout } from "@/lib/api";
import { datetime } from "@/lib/format";

export default function BillingPage() {
  const router = useRouter();
  const [me, setMe] = useState<any>(null);
  const [checkout, setCheckout] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!getToken()) {
      router.push("/login");
      return;
    }
    getMe().then(setMe).catch((e) => setError(e.message));
  }, [router]);

  const upgrade = async () => {
    setBusy(true);
    setError(null);
    try {
      const r = await startCheckout();
      setCheckout(r);
      if (r.checkout_url) window.location.href = r.checkout_url;
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  if (!me && !error) return <Skeleton rows={5} className="max-w-2xl" />;

  return (
    <div className="max-w-2xl space-y-4">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">Billing</h1>
        <p className="text-tick text-ink-400 mt-0.5">
          <Link href="/account" className="hover:text-ink-200">Back to account</Link>
        </p>
      </div>

      <Panel title="Pro" action={<Chip tone={me?.tier === "pro" ? "long" : "muted"}>{me?.tier ?? "—"}</Chip>}>
        <div className="flex items-baseline gap-1.5">
          <span className="num text-3xl">$10</span>
          <span className="text-tick text-ink-400">/month</span>
        </div>
        <ul className="mt-4 space-y-2 text-tick text-ink-200">
          {[
            "BTC, ETH and SOL the moment a signal is generated",
            "Every timeframe from 5m to daily",
            "Telegram and browser alerts with your own filters",
            "The complete signal archive and replay",
            "Calibration, drawdown and per-regime analytics",
          ].map((f) => (
            <li key={f} className="flex gap-2.5">
              <span className="text-long" aria-hidden>·</span>
              <span>{f}</span>
            </li>
          ))}
        </ul>

        {me?.tier === "pro" ? (
          <div className="mt-5 border-t border-edge pt-4 space-y-2">
            <KeyValue k="Status" v={me.subscription?.status ?? "active"} mono={false} />
            {me.subscription?.current_period_end && (
              <KeyValue k="Renews" v={datetime(me.subscription.current_period_end)} mono={false} />
            )}
            <p className="text-micro text-ink-400 pt-1 leading-relaxed">
              Cancel any time from your payment provider. Access continues to the end of the period
              you have already paid for.
            </p>
          </div>
        ) : (
          <button className="btn-primary w-full mt-5" onClick={upgrade} disabled={busy}>
            {busy ? "Starting checkout…" : "Upgrade to Pro"}
          </button>
        )}

        {error && <p className="text-tick text-short mt-3">{error}</p>}

        {checkout && checkout.configured === false && (
          <div className="mt-4 border border-warn/40 bg-warn-dim/30 rounded-panel p-3">
            <div className="text-tick text-warn font-medium">Payments are not configured</div>
            <p className="text-tick text-ink-200 mt-1 leading-relaxed">{checkout.message}</p>
            <p className="text-micro text-ink-400 mt-2">
              Set <code className="num">SP_BILLING_PROVIDER</code> and the matching provider keys in
              your environment. The application never stores payment credentials itself.
            </p>
          </div>
        )}
      </Panel>

      <Panel title="What we store">
        <p className="text-tick text-ink-300 leading-relaxed">
          Payment details are handled entirely by the payment provider — we keep a customer
          reference, a subscription status and a renewal date. Card numbers never touch this
          application.
        </p>
      </Panel>
    </div>
  );
}
