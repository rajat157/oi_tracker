"use client";

import Link from "next/link";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useDashboardStore } from "@/stores/dashboard-store";

export function WinRateCard() {
  const stats = useDashboardStore((s) => s.tradeStats.iron_pulse);

  if (!stats || stats.total === 0) {
    return (
      <Card>
        <CardHeader><CardTitle className="text-sm font-medium text-muted-foreground">Win Rate</CardTitle></CardHeader>
        <CardContent><p className="text-sm text-muted-foreground">No trade data yet</p></CardContent>
      </Card>
    );
  }

  const wrColor = stats.win_rate >= 65 ? "text-green-500" : stats.win_rate >= 50 ? "text-yellow-500" : "text-red-500";

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium text-muted-foreground">Win Rate (Iron Pulse)</CardTitle>
      </CardHeader>
      <CardContent className="space-y-2">
        <div className={`text-4xl font-bold font-mono ${wrColor}`}>
          {stats.win_rate.toFixed(0)}%
        </div>
        <div className="grid grid-cols-3 gap-2 text-sm">
          <div>
            <p className="text-muted-foreground">Total</p>
            <p className="font-mono font-medium">{stats.total}</p>
          </div>
          <div>
            <p className="text-muted-foreground">Wins</p>
            <p className="font-mono font-medium text-green-500">{stats.won}</p>
          </div>
          <div>
            <p className="text-muted-foreground">Losses</p>
            <p className="font-mono font-medium text-red-500">{stats.lost}</p>
          </div>
        </div>
        <div className="text-sm border-t border-border pt-2 flex justify-between">
          <span className="text-muted-foreground">Avg P&L</span>
          <span className={`font-mono ${stats.avg_pnl >= 0 ? "text-green-500" : "text-red-500"}`}>
            {stats.avg_pnl >= 0 ? "+" : ""}{stats.avg_pnl.toFixed(1)}%
          </span>
        </div>
        <Link href="/trades" className="block text-center text-xs text-primary hover:underline pt-1">
          View All Trades
        </Link>
      </CardContent>
    </Card>
  );
}
