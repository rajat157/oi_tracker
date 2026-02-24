import type {
  DashboardPayload,
  AnalysisHistoryItem,
  TradeBase,
  TradeStats,
  MarketStatus,
  StrategyName,
  LogEntry,
} from "./types";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api/v1";

async function fetchJSON<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!res.ok) {
    throw new Error(`API ${res.status}: ${res.statusText}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  // Analysis
  getLatest: () => fetchJSON<DashboardPayload>("/analysis/latest"),
  getHistory: (limit = 100) =>
    fetchJSON<{ data: AnalysisHistoryItem[]; count: number }>(
      `/analysis/history?limit=${limit}`
    ),

  // Trades
  getTrades: (strategy: StrategyName, params?: Record<string, string>) => {
    const qs = params ? "?" + new URLSearchParams(params).toString() : "";
    return fetchJSON<{ data: TradeBase[]; count: number; strategy: string }>(
      `/trades/${strategy}${qs}`
    );
  },
  getTradeStats: (strategy: StrategyName) =>
    fetchJSON<{ strategy: string; stats: TradeStats }>(`/trades/${strategy}/stats`),

  // Market
  getMarketStatus: () => fetchJSON<MarketStatus>("/market/status"),
  triggerRefresh: () =>
    fetchJSON<{ message: string }>("/market/refresh", { method: "POST" }),

  // Kite
  getKiteStatus: () => fetchJSON<{ authenticated: boolean }>("/kite/status"),

  // Logs
  getLogs: (params?: Record<string, string>) => {
    const qs = params ? "?" + new URLSearchParams(params).toString() : "";
    return fetchJSON<{ data: LogEntry[]; count: number }>(`/logs${qs}`);
  },

  // SSE stream URL
  getSSEUrl: () => `${API_BASE}/events/stream`,
};
