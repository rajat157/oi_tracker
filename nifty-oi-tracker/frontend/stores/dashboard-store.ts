import { create } from "zustand";
import type {
  Analysis,
  AnalysisBlob,
  AnalysisHistoryItem,
  MarketStatus,
  TradeBase,
  TradeStats,
  StrategyName,
} from "@/lib/types";

interface DashboardState {
  // Data
  analysis: Analysis | null;
  chartHistory: AnalysisHistoryItem[];
  activeTrades: Record<string, TradeBase | null>;
  tradeStats: Record<string, TradeStats>;
  connected: boolean;
  marketStatus: MarketStatus | null;
  kiteAuthenticated: boolean | null;

  // Actions
  setAnalysis: (analysis: Analysis) => void;
  setChartHistory: (history: AnalysisHistoryItem[]) => void;
  setActiveTrade: (strategy: StrategyName, trade: TradeBase | null) => void;
  setActiveTrades: (trades: Record<string, TradeBase | null>) => void;
  setTradeStats: (strategy: StrategyName, stats: TradeStats) => void;
  setAllTradeStats: (stats: Record<string, TradeStats>) => void;
  setConnected: (connected: boolean) => void;
  setMarketStatus: (status: MarketStatus) => void;
  setKiteAuthenticated: (auth: boolean | null) => void;
  updateFromSSE: (event: string, data: unknown) => void;
}

export const useDashboardStore = create<DashboardState>((set) => ({
  analysis: null,
  chartHistory: [],
  activeTrades: {},
  tradeStats: {},
  connected: false,
  marketStatus: null,
  kiteAuthenticated: null,

  setAnalysis: (analysis) => set({ analysis }),
  setChartHistory: (chartHistory) => set({ chartHistory }),
  setActiveTrade: (strategy, trade) =>
    set((state) => ({
      activeTrades: { ...state.activeTrades, [strategy]: trade },
    })),
  setActiveTrades: (trades) => set({ activeTrades: trades }),
  setTradeStats: (strategy, stats) =>
    set((state) => ({
      tradeStats: { ...state.tradeStats, [strategy]: stats },
    })),
  setAllTradeStats: (stats) => set({ tradeStats: stats }),
  setConnected: (connected) => set({ connected }),
  setMarketStatus: (marketStatus) => set({ marketStatus }),
  setKiteAuthenticated: (kiteAuthenticated) => set({ kiteAuthenticated }),

  updateFromSSE: (event, data) => {
    if (event === "analysis_update") {
      const payload = data as Record<string, unknown>;
      set((state) => {
        // Build the chart history item from SSE payload
        const historyItem: AnalysisHistoryItem = {
          timestamp: new Date().toISOString(),
          spot_price: (payload.spot_price as number) ?? 0,
          verdict: (payload.verdict as AnalysisHistoryItem["verdict"]) ?? "Neutral",
          signal_confidence: (payload.signal_confidence as number) ?? 0,
          vix: (payload.vix as number) ?? 0,
          call_oi_change: (payload.call_oi_change as number) ?? 0,
          put_oi_change: (payload.put_oi_change as number) ?? 0,
          otm_put_force: ((payload.otm_puts as Record<string, unknown>)?.total_force as number) ?? 0,
          otm_call_force: ((payload.otm_calls as Record<string, unknown>)?.total_force as number) ?? 0,
          itm_put_force: ((payload.itm_puts as Record<string, unknown>)?.total_force as number) ?? 0,
          itm_call_force: ((payload.itm_calls as Record<string, unknown>)?.total_force as number) ?? 0,
        };

        return {
          analysis: {
            ...state.analysis,
            spot_price: (payload.spot_price as number) ?? state.analysis?.spot_price ?? 0,
            verdict: (payload.verdict as string) ?? state.analysis?.verdict ?? "Neutral",
            signal_confidence:
              (payload.signal_confidence as number) ?? state.analysis?.signal_confidence ?? 0,
            vix: (payload.vix as number) ?? state.analysis?.vix ?? 0,
            max_pain: (payload.max_pain as number) ?? state.analysis?.max_pain ?? 0,
            iv_skew: (payload.iv_skew as number) ?? state.analysis?.iv_skew ?? 0,
            futures_oi: (payload.futures_oi as number) ?? state.analysis?.futures_oi ?? 0,
            futures_basis: (payload.futures_basis as number) ?? state.analysis?.futures_basis ?? 0,
            atm_strike: (payload.atm_strike as number) ?? state.analysis?.atm_strike ?? 0,
            call_oi_change: (payload.call_oi_change as number) ?? state.analysis?.call_oi_change ?? 0,
            put_oi_change: (payload.put_oi_change as number) ?? state.analysis?.put_oi_change ?? 0,
            analysis_blob: payload as unknown as AnalysisBlob,
          } as Analysis,
          chartHistory: [...state.chartHistory, historyItem].slice(-100),
        };
      });
    } else if (event === "trade_update") {
      const payload = data as { strategy: StrategyName; trade: TradeBase | null };
      set((state) => ({
        activeTrades: { ...state.activeTrades, [payload.strategy]: payload.trade },
      }));
    }
  },
}));
