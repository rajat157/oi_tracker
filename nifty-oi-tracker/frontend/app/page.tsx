"use client";

import { useCallback } from "react";
import { Header } from "@/components/shared/Header";
import { VerdictCard } from "@/components/dashboard/VerdictCard";
import { TradeCard } from "@/components/dashboard/TradeCard";
import { OIChart } from "@/components/dashboard/OIChart";
import { useSSE } from "@/hooks/useSSE";
import { useDashboardStore } from "@/stores/dashboard-store";
import { api } from "@/lib/api";
import type { StrategyName } from "@/lib/types";

const strategies: StrategyName[] = ["iron_pulse", "selling", "dessert", "momentum"];

export default function Dashboard() {
  const { setConnected, updateFromSSE } = useDashboardStore();

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
  useDashboardStore.setState({ connected });

  return (
    <div className="min-h-screen bg-background">
      <Header />
      <main className="container mx-auto p-6 space-y-6">
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-5 gap-4">
          <VerdictCard />
          {strategies.map((s) => (
            <TradeCard key={s} strategy={s} />
          ))}
        </div>
        <OIChart />
      </main>
    </div>
  );
}
