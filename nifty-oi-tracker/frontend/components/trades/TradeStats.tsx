"use client";

import { Card, CardContent } from "@/components/ui/card";
import type { TradeStats as TradeStatsType } from "@/lib/types";

interface TradeStatsProps {
  stats: TradeStatsType | null;
  loading?: boolean;
}

export function TradeStatsPanel({ stats, loading }: TradeStatsProps) {
  if (loading || !stats) {
    return <p className="text-muted-foreground py-4 text-center">Loading stats...</p>;
  }

  const items = [
    { label: "Total", value: stats.total, color: "" },
    { label: "Won", value: stats.won, color: "text-green-600" },
    { label: "Lost", value: stats.lost, color: "text-red-600" },
    {
      label: "Win Rate",
      value: `${stats.win_rate.toFixed(1)}%`,
      color: stats.win_rate >= 60 ? "text-green-600" : "text-red-600",
    },
    {
      label: "Avg P&L",
      value: `${stats.avg_pnl >= 0 ? "+" : ""}${stats.avg_pnl.toFixed(1)}%`,
      color: stats.avg_pnl >= 0 ? "text-green-600" : "text-red-600",
    },
    {
      label: "Total P&L",
      value: `${stats.total_pnl >= 0 ? "+" : ""}${stats.total_pnl.toFixed(1)}%`,
      color: stats.total_pnl >= 0 ? "text-green-600" : "text-red-600",
    },
  ];

  return (
    <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
      {items.map((item) => (
        <Card key={item.label}>
          <CardContent className="pt-4 pb-3 px-4 text-center">
            <p className="text-xs text-muted-foreground">{item.label}</p>
            <p className={`text-lg font-semibold font-mono ${item.color}`}>{item.value}</p>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}
