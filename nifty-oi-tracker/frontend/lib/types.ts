// Enums mirroring backend Pydantic schemas

export type TradeStatus = "PENDING" | "ACTIVE" | "WON" | "LOST" | "CANCELLED" | "EXPIRED";
export type TradeDirection = "BUY_CALL" | "BUY_PUT" | "SELL_CALL" | "SELL_PUT";
export type OptionType = "CE" | "PE";
export type StrategyName = "iron_pulse" | "selling" | "dessert" | "momentum";
export type Verdict =
  | "Slightly Bullish"
  | "Slightly Bearish"
  | "Neutral"
  | "Bullish"
  | "Bearish";

// Analysis
export interface Analysis {
  id: number;
  timestamp: string;
  spot_price: number;
  atm_strike: number;
  total_call_oi: number;
  total_put_oi: number;
  call_oi_change: number;
  put_oi_change: number;
  verdict: Verdict;
  prev_verdict: Verdict | null;
  vix: number;
  iv_skew: number;
  max_pain: number;
  signal_confidence: number;
  futures_oi: number;
  futures_basis: number;
  analysis_blob: Record<string, unknown> | null;
}

export interface AnalysisHistoryItem {
  timestamp: string;
  spot_price: number;
  verdict: Verdict;
  signal_confidence: number;
  vix: number;
}

// Trades
export interface TradeBase {
  id: number;
  created_at: string;
  direction: TradeDirection;
  strike: number;
  option_type: OptionType;
  entry_premium: number;
  sl_premium: number;
  spot_at_creation: number;
  verdict_at_creation: string;
  signal_confidence: number | null;
  status: TradeStatus;
  resolved_at: string | null;
  exit_premium: number | null;
  exit_reason: string | null;
  profit_loss_pct: number | null;
  max_premium_reached: number | null;
  min_premium_reached: number | null;
}

export interface IronPulseTrade extends TradeBase {
  moneyness: string;
  target1_premium: number;
  target2_premium: number | null;
  risk_pct: number;
  hit_sl: boolean;
  hit_target: boolean;
  t1_hit: boolean;
  trailing_sl: number | null;
}

export interface SellingTrade extends TradeBase {
  target_premium: number;
  target2_premium: number | null;
  t1_hit: boolean;
  t1_hit_at: string | null;
}

export interface DessertTrade extends TradeBase {
  strategy_name: string;
  target_premium: number;
  iv_skew_at_creation: number | null;
  vix_at_creation: number | null;
  spot_move_30m: number | null;
}

export interface MomentumTrade extends TradeBase {
  strategy_name: string;
  target_premium: number;
  combined_score: number | null;
  confirmation_status: string | null;
}

export interface TradeStats {
  total: number;
  won: number;
  lost: number;
  win_rate: number;
  avg_pnl: number;
  total_pnl: number;
}

// Dashboard
export interface DashboardPayload {
  analysis: Analysis | null;
  active_trades: Record<StrategyName, TradeBase | null>;
  trade_stats: Record<StrategyName, TradeStats>;
  chart_history: AnalysisHistoryItem[];
}

// Market
export interface MarketStatus {
  is_open: boolean;
  market_open: string;
  market_close: string;
  server_time: string;
}

// SSE Events
export interface SSEEventData {
  event: "analysis_update" | "trade_update" | "market_status";
  data: Record<string, unknown>;
  timestamp: string;
}
