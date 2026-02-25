"use client";

import { useCallback, useEffect } from "react";
import { Header } from "@/components/shared/Header";
import { VerdictCard } from "@/components/dashboard/VerdictCard";
import { CombinedScoreGauge } from "@/components/dashboard/CombinedScoreGauge";
import { ExtendedMetrics } from "@/components/dashboard/ExtendedMetrics";
import { TradeCard } from "@/components/dashboard/TradeCard";
import { WinRateCard } from "@/components/dashboard/WinRateCard";
import { KiteAuthCard } from "@/components/dashboard/KiteAuthCard";
import { AlertCards } from "@/components/dashboard/AlertCards";
import { ZoneForceCard } from "@/components/dashboard/ZoneForceCard";
import { DirectionalForceCard } from "@/components/dashboard/DirectionalForceCard";
import { ZoneTables } from "@/components/dashboard/ZoneTables";
import { ForceChart } from "@/components/dashboard/ForceChart";
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

  const chartHistory = useDashboardStore((s) => s.chartHistory);

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
      <main className="container mx-auto p-4 lg:p-6">
        {/* Two-column layout */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
          {/* LEFT COLUMN */}
          <div className="space-y-4">
            <VerdictCard />
            <CombinedScoreGauge />
            <ExtendedMetrics />
            <KiteAuthCard />

            {/* Strategy cards */}
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              {strategies.map((s) => (
                <TradeCard key={s} strategy={s} />
              ))}
            </div>

            <WinRateCard />
            <AlertCards />
          </div>

          {/* RIGHT COLUMN */}
          <div className="lg:col-span-2 space-y-4">
            <ZoneForceCard />
            <DirectionalForceCard />
            <ZoneTables />
          </div>
        </div>

        {/* FULL WIDTH — Charts */}
        <div className="mt-4 space-y-4">
          <ForceChart
            title="OI Change Trend"
            data={chartHistory}
            line1Key="call_oi_change"
            line2Key="put_oi_change"
            line1Color="#ef4444"
            line2Color="#22c55e"
            line1Label="Call OI Change"
            line2Label="Put OI Change"
            height={300}
          />
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <ForceChart
              title="OTM Force Trend"
              data={chartHistory}
              line1Key="otm_call_force"
              line2Key="otm_put_force"
              line1Color="#ef4444"
              line2Color="#22c55e"
              line1Label="OTM Call Force"
              line2Label="OTM Put Force"
              height={220}
            />
            <ForceChart
              title="ITM Force Trend"
              data={chartHistory}
              line1Key="itm_call_force"
              line2Key="itm_put_force"
              line1Color="#ef4444"
              line2Color="#22c55e"
              line1Label="ITM Call Force"
              line2Label="ITM Put Force"
              height={220}
            />
          </div>
        </div>
      </main>
    </div>
  );
}
