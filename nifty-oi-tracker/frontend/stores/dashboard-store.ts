import { create } from "zustand";
import type {
  Analysis,
  AnalysisHistoryItem,
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

  // Actions
  setAnalysis: (analysis: Analysis) => void;
  setChartHistory: (history: AnalysisHistoryItem[]) => void;
  setActiveTrade: (strategy: StrategyName, trade: TradeBase | null) => void;
  setActiveTrades: (trades: Record<string, TradeBase | null>) => void;
  setTradeStats: (strategy: StrategyName, stats: TradeStats) => void;
  setAllTradeStats: (stats: Record<string, TradeStats>) => void;
  setConnected: (connected: boolean) => void;
  updateFromSSE: (event: string, data: unknown) => void;
}

export const useDashboardStore = create<DashboardState>((set) => ({
  analysis: null,
  chartHistory: [],
  activeTrades: {},
  tradeStats: {},
  connected: false,

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

  updateFromSSE: (event, data) => {
    if (event === "analysis_update") {
      // The SSE data is the raw analysis dict from fetch_and_analyze
      const payload = data as Record<string, unknown>;
      set((state) => ({
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
        } as Analysis,
      }));
    } else if (event === "trade_update") {
      const payload = data as { strategy: StrategyName; trade: TradeBase | null };
      set((state) => ({
        activeTrades: { ...state.activeTrades, [payload.strategy]: payload.trade },
      }));
    }
  },
}));
