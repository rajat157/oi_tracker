import { create } from "zustand";
import type { Analysis, AnalysisHistoryItem, TradeBase, TradeStats, StrategyName } from "@/lib/types";

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
  setTradeStats: (strategy: StrategyName, stats: TradeStats) => void;
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
  setTradeStats: (strategy, stats) =>
    set((state) => ({
      tradeStats: { ...state.tradeStats, [strategy]: stats },
    })),
  setConnected: (connected) => set({ connected }),

  updateFromSSE: (event, data) => {
    if (event === "analysis_update") {
      const payload = data as { analysis: Analysis; chart_history?: AnalysisHistoryItem[] };
      set((state) => ({
        analysis: payload.analysis,
        ...(payload.chart_history ? { chartHistory: payload.chart_history } : {}),
      }));
    } else if (event === "trade_update") {
      const payload = data as { strategy: StrategyName; trade: TradeBase | null };
      set((state) => ({
        activeTrades: { ...state.activeTrades, [payload.strategy]: payload.trade },
      }));
    }
  },
}));
