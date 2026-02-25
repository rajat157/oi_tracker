"use client";

import { useCallback, useEffect } from "react";
import { Header } from "@/components/shared/Header";
import { VerdictCard } from "@/components/dashboard/VerdictCard";
import { KeyMetricsCard } from "@/components/dashboard/KeyMetricsCard";
import { AlertCards } from "@/components/dashboard/AlertCards";
import { ZoneForceCard } from "@/components/dashboard/ZoneForceCard";
import { DirectionalForceCard } from "@/components/dashboard/DirectionalForceCard";
import { OITrendChart } from "@/components/dashboard/OITrendChart";
import { DeepDiveTabs } from "@/components/dashboard/DeepDiveTabs";
import { useSSE } from "@/hooks/useSSE";
import { useDashboardStore } from "@/stores/dashboard-store";
import { api } from "@/lib/api";

export default function Dashboard() {
  const {
    setConnected,
    updateFromSSE,
    setAnalysis,
    setChartHistory,
    setActiveTrades,
    setAllTradeStats,
    setMarketStatus,
    setKiteAuthenticated,
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
      .catch(() => {});
  }, [setAnalysis, setChartHistory, setActiveTrades, setAllTradeStats]);

  // Poll market status every 60s
  useEffect(() => {
    const fetchStatus = () => {
      api.getMarketStatus().then(setMarketStatus).catch(() => {});
    };
    fetchStatus();
    const interval = setInterval(fetchStatus, 60_000);
    return () => clearInterval(interval);
  }, [setMarketStatus]);

  // Poll Kite auth status every 5 minutes
  useEffect(() => {
    const fetchKite = () => {
      api
        .getKiteStatus()
        .then((res) => setKiteAuthenticated(res.authenticated))
        .catch(() => setKiteAuthenticated(false));
    };
    fetchKite();
    const interval = setInterval(fetchKite, 5 * 60_000);
    return () => clearInterval(interval);
  }, [setKiteAuthenticated]);

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
      <main className="container mx-auto p-4 lg:p-6 space-y-4">
        {/* TWO-COLUMN LAYOUT */}
        <div className="grid grid-cols-1 lg:grid-cols-[380px_1fr] gap-4">
          {/* Left Sidebar */}
          <div className="space-y-4">
            <VerdictCard />
            <KeyMetricsCard />
            <AlertCards />
          </div>

          {/* Right Column */}
          <div className="space-y-4">
            <ZoneForceCard />
            <DirectionalForceCard />
            <OITrendChart />
          </div>
        </div>

        {/* Full-Width Deep Dive */}
        <DeepDiveTabs />
      </main>
    </div>
  );
}
