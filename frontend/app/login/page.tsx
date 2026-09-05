"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { Panel } from "@/components/ui";
import { login, setTokens } from "@/lib/api";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const r = await login(email, password);
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
      <Panel title="Sign in">
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
              type="password" required autoComplete="current-password" className="field mt-1"
              value={password} onChange={(e) => setPassword(e.target.value)}
            />
          </label>

          {error && (
            <p className="text-tick text-short border border-short/30 bg-short-deep rounded-panel p-2.5">
              {error}
            </p>
          )}

          <button type="submit" className="btn-primary w-full" disabled={busy}>
            {busy ? "Signing in…" : "Sign in"}
          </button>
        </form>

        <p className="text-tick text-ink-400 mt-4">
          No account yet? <Link href="/signup" className="text-ink-200 hover:text-long">Create one free</Link>.
        </p>
      </Panel>
    </div>
  );
}
