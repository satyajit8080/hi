"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { Chip, ErrorState, Panel, Skeleton } from "@/components/ui";
import { bindChannel, getAlerts, getToken, saveAlerts } from "@/lib/api";

interface Pref {
  channel: string;
  symbol: string;
  timeframe: string;
  direction: string;
  min_strength: number;
  enabled: boolean;
}

const BLANK: Pref = {
  channel: "telegram", symbol: "ALL", timeframe: "ALL",
  direction: "ALL", min_strength: 60, enabled: true,
};

export default function AlertsPage() {
  const router = useRouter();
  const [prefs, setPrefs] = useState<Pref[] | null>(null);
  const [channels, setChannels] = useState<any[]>([]);
  const [available, setAvailable] = useState(true);
  const [error, setError] = useState<Error | null>(null);
  const [saved, setSaved] = useState(false);
  const [address, setAddress] = useState("");
  const [channelKind, setChannelKind] = useState("telegram");

  useEffect(() => {
    if (!getToken()) {
      router.push("/login");
      return;
    }
    getAlerts()
      .then((r) => {
        setPrefs(r.preferences ?? []);
        setChannels(r.channels ?? []);
        setAvailable(r.alerts_available);
      })
      .catch(setError);
  }, [router]);

  const save = async () => {
    if (!prefs) return;
    setSaved(false);
    try {
      await saveAlerts(prefs);
      setSaved(true);
      setTimeout(() => setSaved(false), 2500);
    } catch (e) {
      setError(e as Error);
    }
  };

  const connect = async () => {
    try {
      const r = await bindChannel(channelKind, address);
      setChannels((c) => [...c.filter((x) => x.channel !== channelKind), r]);
      setAddress("");
    } catch (e) {
      setError(e as Error);
    }
  };

  if (error) return <ErrorState error={error} />;
  if (!prefs) return <Skeleton rows={5} className="max-w-3xl" />;

  return (
    <div className="max-w-3xl space-y-4">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">Alert settings</h1>
        <p className="text-tick text-ink-400 mt-0.5">
          <Link href="/account" className="hover:text-ink-200">Back to account</Link>
        </p>
      </div>

      {!available && (
        <div className="border border-edge rounded-panel p-3 flex flex-wrap items-center justify-between gap-3">
          <p className="text-tick text-ink-300">Alerts are part of Pro.</p>
          <Link href="/account/billing" className="btn-ghost shrink-0">Upgrade</Link>
        </div>
      )}

      <Panel title="Where alerts go">
        {channels.length > 0 && (
          <div className="flex flex-wrap gap-2 mb-3">
            {channels.map((c) => (
              <Chip key={c.channel} tone={c.verified ? "long" : "warn"}>
                {c.channel} · {c.address}
              </Chip>
            ))}
          </div>
        )}
        <div className="flex flex-wrap gap-2 items-end">
          <label className="flex flex-col gap-1">
            <span className="text-micro text-ink-400">Channel</span>
            <select className="field w-36" value={channelKind} onChange={(e) => setChannelKind(e.target.value)}>
              <option value="telegram">Telegram</option>
              <option value="webpush">Browser push</option>
              <option value="email">Email</option>
            </select>
          </label>
          <label className="flex-1 min-w-[200px] flex flex-col gap-1">
            <span className="text-micro text-ink-400">
              {channelKind === "telegram" ? "Chat ID" : channelKind === "email" ? "Email address" : "Endpoint"}
            </span>
            <input className="field" value={address} onChange={(e) => setAddress(e.target.value)} />
          </label>
          <button className="btn-ghost" onClick={connect} disabled={!address || !available}>
            Connect
          </button>
        </div>
      </Panel>

      <Panel
        title="Rules"
        action={
          <button
            className="text-micro text-ink-400 hover:text-ink-200"
            onClick={() => setPrefs([...(prefs ?? []), { ...BLANK }])}
            disabled={!available}
          >
            Add a rule
          </button>
        }
      >
        {prefs.length === 0 ? (
          <p className="text-tick text-ink-400">
            No rules yet. Add one to decide which signals reach you.
          </p>
        ) : (
          <div className="space-y-3">
            {prefs.map((p, i) => (
              <div key={i} className="border border-edge rounded-panel p-3 grid sm:grid-cols-5 gap-3 items-end">
                <Field label="Channel" value={p.channel} options={["telegram", "webpush", "email"]}
                       onChange={(v) => update(setPrefs, i, { channel: v })} />
                <Field label="Market" value={p.symbol} options={["ALL", "BTCUSDT", "ETHUSDT", "SOLUSDT"]}
                       onChange={(v) => update(setPrefs, i, { symbol: v })} />
                <Field label="Timeframe" value={p.timeframe} options={["ALL", "15m", "1h", "4h"]}
                       onChange={(v) => update(setPrefs, i, { timeframe: v })} />
                <Field label="Direction" value={p.direction} options={["ALL", "LONG", "SHORT"]}
                       onChange={(v) => update(setPrefs, i, { direction: v })} />
                <label className="flex flex-col gap-1">
                  <span className="text-micro text-ink-400">Min strength {p.min_strength}</span>
                  <input
                    type="range" min={0} max={100} value={p.min_strength}
                    onChange={(e) => update(setPrefs, i, { min_strength: Number(e.target.value) })}
                    className="accent-[#3DD68C]"
                  />
                </label>
                <button
                  className="text-micro text-ink-400 hover:text-short sm:col-span-5 text-left"
                  onClick={() => setPrefs(prefs.filter((_, j) => j !== i))}
                >
                  Remove this rule
                </button>
              </div>
            ))}
          </div>
        )}

        <div className="flex items-center gap-3 mt-4">
          <button className="btn-primary" onClick={save} disabled={!available}>Save rules</button>
          {saved && <span className="text-tick text-long">Saved</span>}
        </div>

        <p className="text-micro text-ink-400 mt-4 leading-relaxed max-w-prose">
          One message per signal per channel — enforced by the database, not by a retry policy. Take
          profit and stop updates stay off by default so a single setup cannot become five
          notifications.
        </p>
      </Panel>
    </div>
  );
}

function update(
  setPrefs: React.Dispatch<React.SetStateAction<Pref[] | null>>,
  index: number,
  patch: Partial<Pref>
) {
  setPrefs((prev) => (prev ? prev.map((p, i) => (i === index ? { ...p, ...patch } : p)) : prev));
}

function Field({
  label, value, options, onChange,
}: {
  label: string;
  value: string;
  options: string[];
  onChange: (v: string) => void;
}) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-micro text-ink-400">{label}</span>
      <select className="field h-9" value={value} onChange={(e) => onChange(e.target.value)}>
        {options.map((o) => (
          <option key={o} value={o}>{o.replace("USDT", "")}</option>
        ))}
      </select>
    </label>
  );
}
