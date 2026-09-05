export type Direction = "LONG" | "SHORT";
export type Outcome = "WIN" | "LOSS" | "EXPIRED" | "CANCELLED";

export interface ReasonCode {
  code: string;
  label: string;
  group: string;
  contribution: number;
  value: number | string | null;
  /** "driver" contributions sum to the score. "context_only" never does. */
  role: "driver" | "context_only";
  sign: "support" | "oppose" | "neutral";
  note?: string | null;
  cohort_size?: number | null;
  validation_ref?: string | null;
}

export interface RiskFlag {
  code: string;
  label: string;
  group: string;
  value: number | string | null;
  severity: "low" | "medium" | "high";
}

export interface DataQuality {
  venues_live: number;
  book_synced: boolean;
  max_staleness_ms: number;
  clock_skew_ms: number;
  degraded: boolean;
  flags: string[];
}

export interface LifecycleEvent {
  event: string;
  ts: string;
  price: number | null;
  reason: string | null;
  meta?: Record<string, unknown>;
}

export interface Signal {
  signal_id: string;
  published_at: string;
  symbol: string;
  direction: Direction;
  timeframe: string;
  regime: string;
  strategy_version: string;
  strength: number;
  mtf_alignment: number;
  calibrated_winrate: number | null;
  calibration_sample: number | null;
  calibration_ci_low: number | null;
  calibration_ci_high: number | null;
  entry: number | null;
  stop_loss: number | null;
  tp1: number | null;
  tp2: number | null;
  tp3: number | null;
  risk_reward: number;
  risk_category: string;
  expires_at: string;
  invalidation: string;
  reason_codes: ReasonCode[];
  risk_flags: RiskFlag[];
  data_quality: DataQuality;
  is_simulated: boolean;
  row_hash: string;
  prev_hash: string;
  seq: number;
  status: string;
  outcome: Outcome | null;
  tp_hits: number;
  mfe_pct: number | null;
  mae_pct: number | null;
  close_price: number | null;
  closed_at: string | null;
  close_reason: string | null;
  pnl_pct: number | null;
  r_multiple: number | null;
  locked?: boolean;
  locked_reason?: string;
  signal_class?: string | null;
  conflict?: number | null;
  lifecycle?: LifecycleEvent[];
  features?: Record<string, any>;
  verification?: { row_hash: string; prev_hash: string; verify_url: string; method: string };
}

export interface MicroFeatures {
  available: boolean;
  book_synced: boolean;
  best_bid: number | null;
  best_ask: number | null;
  mid: number | null;
  micro_price: number | null;
  spread_rel: number | null;
  spread_z: number | null;
  obi_5: number | null;
  obi_20: number | null;
  obi_persistent: number | null;
  queue_imbalance: number | null;
  ofi: number | null;
  ofi_z: number | null;
  cvd_spot: number | null;
  cvd_perp: number | null;
  cvd_divergence: number | null;
  trade_imbalance: number | null;
  large_print_z: number | null;
  venues_agreeing: number;
}

export interface MarketSnapshot {
  symbol: string;
  price: number | null;
  book: { bids: [number, number][]; asks: [number, number][]; synced: boolean };
  micro: MicroFeatures;
  liquidations: { ts: number; side: "long" | "short"; price: number; qty: number; notional: number }[];
  cvd_series: number[];
  price_series: number[];
  is_simulated: boolean;
  ts: number;
}

export interface Health {
  venues: Record<string, { connected: boolean; books_synced: number; trades_fresh: number; symbols: number }>;
  venues_live: number;
  min_venues_required: number;
  max_staleness_ms: number | null;
  degraded: boolean;
  flags: string[];
  mode: string;
  is_simulated: boolean;
  data_delayed?: boolean;
  message?: string;
  strategy_version?: string;
  symbols?: string[];
}

export interface PerfStats {
  total_signals: number;
  winning_signals: number;
  losing_signals: number;
  expired_signals: number;
  cancelled_signals: number;
  win_rate: number | null;
  win_rate_ci: [number | null, number | null];
  profit_factor: number | null;
  expectancy_pct: number | null;
  avg_return_pct: number | null;
  avg_win_pct: number | null;
  avg_loss_pct: number | null;
  max_drawdown_pct: number | null;
  sharpe: number | null;
  sortino: number | null;
  max_consecutive_wins: number;
  max_consecutive_losses: number;
  current_streak: number;
  current_streak_kind: string;
  avg_holding_hours: number | null;
  avg_mfe_pct: number | null;
  avg_mae_pct: number | null;
  sufficient_sample: boolean;
  minimum_sample: number;
}

export interface PerformanceReport {
  window: string;
  overall: PerfStats;
  by_symbol: Record<string, PerfStats>;
  by_timeframe: Record<string, PerfStats>;
  by_regime: Record<string, PerfStats>;
  by_strength_bucket: Record<string, PerfStats>;
  by_month: (PerfStats & { month: string })[];
  equity_curve: { ts: string | null; cumulative_pct: number; drawdown_pct: number; signal_id: string; outcome: string }[];
  calibration_curve: {
    bin_low: number; bin_high: number; predicted: number | null; observed: number | null;
    n: number; ci_low: number | null; ci_high: number | null;
  }[];
  calibration_sample: number;
  calibration_quality?: {
    sufficient: boolean; n: number; brier?: number; brier_baseline?: number;
    log_loss?: number; ece?: number; skill?: number; note?: string;
  };
  includes_simulated: boolean;
  disclaimer: string;
  generated_at: string;
}

export interface TierSummary {
  tier: string;
  symbols: string[];
  delay_minutes: number;
  history_days: number | null;
  alerts: boolean;
  timeframes: string[];
  api_access: boolean;
  analytics: boolean;
  price_usd: number;
}
