"use client";

import { useCallback, useEffect } from "react";
import { Header } from "@/components/shared/Header";
import { VerdictCard } from "@/components/dashboard/VerdictCard";
import { TradeCard } from "@/components/dashboard/TradeCard";
import { OIChart } from "@/components/dashboard/OIChart";
import { ScoreGauge } from "@/components/dashboard/ScoreGauge";
import { MetricsPanel } from "@/components/dashboard/MetricsPanel";
import { useSSE } from "@/hooks/useSSE";
import { useDashboardStore } from "@/stores/dashboard-store";
import { api } from "@/lib/api";
import type { StrategyName } from "@/lib/types";

const strategies: StrategyName[] = ["iron_pulse", "selling", "dessert", "momentum"];

export default function Dashboard() {
  const {
    setConnected,
    updateFromSSE,
    setAnalysis,
    setChartHistory,
    setActiveTrades,
    setAllTradeStats,
  } = useDashboardStore();

  // Fetch initial data on mount
  useEffect(() => {
    api
      .getLatest()
      .then((payload) => {
        if (payload.analysis) setAnalysis(payload.analysis);
        if (payload.chart_history) setChartHistory(payload.chart_history);
        if (payload.active_trades) setActiveTrades(payload.active_trades);
        if (payload.trade_stats) setAllTradeStats(payload.trade_stats);
      })
      .catch(() => {
        // API not available yet — SSE will catch up
      });
  }, [setAnalysis, setChartHistory, setActiveTrades, setAllTradeStats]);

  const onMessage = useCallback(
    (event: string, data: unknown) => {
      updateFromSSE(event, data);
    },
    [updateFromSSE]
  );

  const { connected } = useSSE({
    url: api.getSSEUrl(),
    onMessage,
  });

  // Sync SSE connection state to store
  useEffect(() => {
    useDashboardStore.setState({ connected });
  }, [connected]);

  return (
    <div className="min-h-screen bg-background">
      <Header />
      <main className="container mx-auto p-6 space-y-6">
        {/* Top row: Verdict + Confidence Gauge + Metrics */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <VerdictCard />
          <ScoreGauge />
          <MetricsPanel />
        </div>

        {/* Strategy cards */}
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
          {strategies.map((s) => (
            <TradeCard key={s} strategy={s} />
          ))}
        </div>

        {/* Chart */}
        <OIChart />
      </main>
    </div>
  );
}
