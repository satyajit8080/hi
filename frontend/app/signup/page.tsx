"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { Panel } from "@/components/ui";
import { setTokens, signup } from "@/lib/api";

export default function SignupPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const tooShort = password.length > 0 && password.length < 8;

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const r = await signup(email, password);
      setTokens(r.access_token, r.refresh_token);
      router.push("/dashboard");
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="max-w-sm mx-auto pt-10">
      <Panel title="Create your account">
        <p className="text-tick text-ink-300 mb-4 leading-relaxed">
          Free covers Bitcoin signals on a delay, plus the complete public track record. No card
          needed.
        </p>

        <form onSubmit={submit} className="space-y-3">
          <label className="block">
            <span className="text-micro text-ink-400">Email</span>
            <input
              type="email" required autoComplete="email" className="field mt-1"
              value={email} onChange={(e) => setEmail(e.target.value)}
            />
          </label>
          <label className="block">
            <span className="text-micro text-ink-400">Password</span>
            <input
              type="password" required minLength={8} autoComplete="new-password" className="field mt-1"
              value={password} onChange={(e) => setPassword(e.target.value)}
            />
            <span className={`text-micro mt-1 block ${tooShort ? "text-warn" : "text-ink-400"}`}>
              {tooShort ? "Needs at least 8 characters." : "At least 8 characters."}
            </span>
          </label>

          {error && (
            <p className="text-tick text-short border border-short/30 bg-short-deep rounded-panel p-2.5">
              {error}
            </p>
          )}

          <button type="submit" className="btn-primary w-full" disabled={busy || tooShort}>
            {busy ? "Creating…" : "Create account"}
          </button>
        </form>

        <p className="text-tick text-ink-400 mt-4">
          Already have one? <Link href="/login" className="text-ink-200 hover:text-long">Sign in</Link>.
        </p>
      </Panel>
    </div>
  );
}
