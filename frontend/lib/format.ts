const PRICE_DECIMALS: Record<string, number> = {
  BTCUSDT: 1,
  ETHUSDT: 2,
  SOLUSDT: 3,
};

export function price(value: number | null | undefined, symbol = "BTCUSDT"): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  const d = PRICE_DECIMALS[symbol] ?? 2;
  return value.toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
}

export function pct(value: number | null | undefined, digits = 2, sign = false): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  const s = sign && value > 0 ? "+" : "";
  return `${s}${value.toFixed(digits)}%`;
}

export function ratio(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  return value.toFixed(digits);
}

export function rate(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return `${(value * 100).toFixed(1)}%`;
}

export function compact(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  const abs = Math.abs(value);
  if (abs >= 1e9) return `${(value / 1e9).toFixed(2)}B`;
  if (abs >= 1e6) return `${(value / 1e6).toFixed(2)}M`;
  if (abs >= 1e3) return `${(value / 1e3).toFixed(1)}K`;
  return value.toFixed(2);
}

export function timeAgo(iso: string | null | undefined): string {
  if (!iso) return "—";
  const seconds = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
  if (seconds < 0) return "just now";
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

export function until(iso: string | null | undefined): string {
  if (!iso) return "—";
  const seconds = Math.floor((new Date(iso).getTime() - Date.now()) / 1000);
  if (seconds <= 0) return "expired";
  const hours = Math.floor(seconds / 3600);
  if (hours >= 24) return `${Math.floor(hours / 24)}d ${hours % 24}h`;
  if (hours >= 1) return `${hours}h ${Math.floor((seconds % 3600) / 60)}m`;
  return `${Math.floor(seconds / 60)}m`;
}

export function datetime(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("en-GB", {
    day: "2-digit", month: "short", year: "numeric",
    hour: "2-digit", minute: "2-digit", timeZoneName: "short",
  });
}

export function shortHash(hash: string | null | undefined, chars = 8): string {
  if (!hash) return "—";
  return `${hash.slice(0, chars)}…${hash.slice(-4)}`;
}

export function assetName(symbol: string): string {
  return symbol.replace("USDT", "");
}

export const REGIME_LABELS: Record<string, string> = {
  trend_bull: "Uptrend",
  trend_bear: "Downtrend",
  range: "Ranging",
  high_volatility: "High volatility",
};
