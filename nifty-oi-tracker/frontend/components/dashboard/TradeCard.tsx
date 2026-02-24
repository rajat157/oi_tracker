"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import type { TradeBase, StrategyName } from "@/lib/types";
import { useDashboardStore } from "@/stores/dashboard-store";

const strategyLabels: Record<StrategyName, string> = {
  iron_pulse: "Iron Pulse",
  selling: "Selling",
  dessert: "Dessert",
  momentum: "Momentum",
};

interface TradeCardProps {
  strategy: StrategyName;
}

export function TradeCard({ strategy }: TradeCardProps) {
  const trade = useDashboardStore((s) => s.activeTrades[strategy]);
  const stats = useDashboardStore((s) => s.tradeStats[strategy]);
  const label = strategyLabels[strategy];

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium text-muted-foreground flex items-center justify-between">
          {label}
          {trade && (
            <Badge variant={trade.status === "ACTIVE" ? "default" : "secondary"}>
              {trade.status}
            </Badge>
          )}
        </CardTitle>
      </CardHeader>
      <CardContent>
        {trade ? (
          <div className="space-y-1 text-sm">
            <div className="font-medium">
              {trade.direction} {trade.strike} {trade.option_type}
            </div>
            <div className="text-muted-foreground">
              Entry: {trade.entry_premium.toFixed(2)} | SL: {trade.sl_premium.toFixed(2)}
            </div>
            {trade.profit_loss_pct != null && (
              <div className={trade.profit_loss_pct >= 0 ? "text-green-600" : "text-red-600"}>
                P&L: {trade.profit_loss_pct.toFixed(1)}%
              </div>
            )}
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">No active trade</p>
        )}
        {stats && (
          <div className="mt-2 pt-2 border-t text-xs text-muted-foreground">
            WR: {stats.win_rate.toFixed(0)}% | {stats.won}W/{stats.lost}L | P&L:{" "}
            {stats.total_pnl.toFixed(1)}%
          </div>
        )}
      </CardContent>
    </Card>
  );
}
