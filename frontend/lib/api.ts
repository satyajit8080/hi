import type {
  Health, MarketSnapshot, PerformanceReport, Signal, TierSummary,
} from "./types";

export const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const TOKEN_KEY = "sp_access_token";
const REFRESH_KEY = "sp_refresh_token";

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(TOKEN_KEY);
}

export function setTokens(access: string, refresh: string) {
  window.localStorage.setItem(TOKEN_KEY, access);
  window.localStorage.setItem(REFRESH_KEY, refresh);
}

export function clearTokens() {
  window.localStorage.removeItem(TOKEN_KEY);
  window.localStorage.removeItem(REFRESH_KEY);
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = getToken();
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...((init.headers as Record<string, string>) ?? {}),
  };
  if (token) headers.Authorization = `Bearer ${token}`;

  let res: Response;
  try {
    res = await fetch(`${API}${path}`, { ...init, headers, cache: "no-store" });
  } catch {
    throw new ApiError(0, "Cannot reach the API. Check that the backend is running on port 8000.");
  }

  if (res.status === 204) return undefined as T;

  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new ApiError(res.status, body.detail ?? body.message ?? `Request failed (${res.status}).`);
  }
  return body as T;
}

export const fetcher = <T,>(path: string) => api<T>(path);

/* ── typed endpoints ──────────────────────────────────────────────────────── */

export const getLiveSignals = (symbol?: string) =>
  api<{ signals: Signal[]; tier: TierSummary; is_simulated: boolean }>(
    `/v1/signals/live${symbol ? `?symbol=${symbol}` : ""}`
  );

export const getSignal = (id: string) => api<Signal>(`/v1/signals/${id}`);

export const getHistory = (params: Record<string, string | number | undefined>) => {
  const qs = new URLSearchParams(
    Object.entries(params).filter(([, v]) => v !== undefined && v !== "").map(([k, v]) => [k, String(v)])
  );
  return api<{ signals: Signal[]; total_published: number; history_limited: boolean; tier: TierSummary }>(
    `/v1/signals/history?${qs}`
  );
};

export const getPerformance = (window = "all") =>
  api<PerformanceReport>(`/v1/performance?window=${window}`);

export const getMarket = (symbol: string) => api<MarketSnapshot>(`/v1/market/${symbol}`);
export const getStatus = () => api<Health>("/v1/status");

export const verifySignal = (id: string) =>
  api<{
    signal_id: string; seq: number; stored_hash: string; recomputed_hash: string;
    valid: boolean; prev_hash: string; canonical_payload: string; how_to_verify: string;
  }>(`/v1/verify/${id}`);

export const verifyChain = () =>
  api<{ ok: boolean; checked: number; head?: string; broken_at: string | null; error?: string; signals_in_chain: number }>(
    "/v1/verify/chain/full"
  );

export const getSmartMoney = (symbol: string) =>
  api<{
    symbol: string;
    context: Record<string, any>;
    series: any[];
    disclosure: { role: string; explanation: string; promotion_requirements: string[] };
  }>(`/v1/smart-money/${symbol}`);

export const getMethodology = () => api<Record<string, any>>("/v1/methodology");
export const getPricing = () => api<Record<string, any>>("/v1/pricing");

export const login = (email: string, password: string) =>
  api<{ access_token: string; refresh_token: string; user: any; tier: TierSummary }>(
    "/v1/auth/login", { method: "POST", body: JSON.stringify({ email, password }) }
  );

export const signup = (email: string, password: string) =>
  api<{ access_token: string; refresh_token: string; user: any; tier: TierSummary }>(
    "/v1/auth/signup", { method: "POST", body: JSON.stringify({ email, password }) }
  );

export const getMe = () => api<any>("/v1/auth/me");
export const getAlerts = () => api<any>("/v1/me/alerts");
export const saveAlerts = (prefs: any[]) =>
  api<any>("/v1/me/alerts", { method: "PUT", body: JSON.stringify(prefs) });
export const bindChannel = (channel: string, address: string) =>
  api<any>("/v1/me/channels", { method: "PUT", body: JSON.stringify({ channel, address }) });
export const startCheckout = () => api<any>("/v1/billing/checkout", { method: "POST" });

/* ── added in v1.1 ───────────────────────────────────────────────────────── */

export const getCandles = (symbol: string, timeframe = "1h", limit = 300) =>
  api<{ symbol: string; timeframe: string; is_simulated: boolean;
        candles: { time: number; open: number; high: number; low: number; close: number; volume: number }[] }>(
    `/v1/market/${symbol}/candles?timeframe=${timeframe}&limit=${limit}`
  );

export const getCandidates = () =>
  api<{ candidates: any[]; explanation: string; is_simulated: boolean }>("/v1/candidates");

export const getMarketIntel = () => api<any>("/v1/market-intel");
export const getDerivatives = (symbol: string) => api<any>(`/v1/derivatives/${symbol}`);
export const getExplanation = (id: string) => api<any>(`/v1/signals/${id}/explain`);
