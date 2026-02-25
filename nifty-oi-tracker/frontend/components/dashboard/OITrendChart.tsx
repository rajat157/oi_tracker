"use client";

import { useMemo } from "react";
import { HistogramChart } from "./HistogramChart";
import { useDashboardStore } from "@/stores/dashboard-store";

function toUnixSec(timestamp: string): number {
  return Math.floor(new Date(timestamp).getTime() / 1000);
}

export function OITrendChart() {
  const chartHistory = useDashboardStore((s) => s.chartHistory);

  const data = useMemo(
    () =>
      chartHistory.map((item) => {
        const net = (item.put_oi_change ?? 0) - (item.call_oi_change ?? 0);
        return {
          time: toUnixSec(item.timestamp),
          value: net,
          color: net >= 0 ? "#22c55e" : "#ef4444",
        };
      }),
    [chartHistory]
  );

  return (
    <div key={chartHistory.length}>
      <HistogramChart title="OI Change Trend" data={data} label="Net OI Change" height={250} />
    </div>
  );
}
