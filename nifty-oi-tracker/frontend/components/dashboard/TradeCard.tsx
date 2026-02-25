"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import type { StrategyName } from "@/lib/types";
import { useDashboardStore } from "@/stores/dashboard-store";

const strategyLabels: Record<StrategyName, string> = {
  iron_pulse: "Iron Pulse",
  selling: "Selling",
  dessert: "Dessert",
  momentum: "Momentum",
};

const strategyEmoji: Record<StrategyName, string> = {
  iron_pulse: "\uD83E\uDEC0",
  selling: "\uD83D\uDCB0",
  dessert: "\uD83C\uDF70",
  momentum: "\uD83D\uDE80",
};

const LOT_SIZE = 75;

interface TradeCardProps {
  strategy: StrategyName;
}

export function TradeCard({ strategy }: TradeCardProps) {
  const trade = useDashboardStore((s) => s.activeTrades[strategy]) as Record<string, unknown> | null;
  const stats = useDashboardStore((s) => s.tradeStats[strategy]);
  const label = strategyLabels[strategy];

  const statusColor = trade?.status === "ACTIVE"
    ? "default"
    : trade?.status === "WON"
    ? "default"
    : trade?.status === "LOST"
    ? "destructive"
    : "secondary";

  const entry = trade?.entry_premium as number | undefined;
  const sl = trade?.sl_premium as number | undefined;
  const t1 = (trade?.target1_premium ?? trade?.target_premium) as number | undefined;
  const t2 = trade?.target2_premium as number | undefined;
  const trailing = trade?.trailing_sl as number | undefined;
  const t1Hit = trade?.t1_hit as boolean | undefined;
  const pnl = trade?.profit_loss_pct as number | undefined;

  // Risk in rupees
  const entryCost = entry != null ? entry * LOT_SIZE : null;
  const maxRisk = entry != null && sl != null ? Math.abs(entry - sl) * LOT_SIZE : null;
  const t1Profit = entry != null && t1 != null ? Math.abs(t1 - entry) * LOT_SIZE : null;

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium text-muted-foreground flex items-center justify-between">
          <span>{strategyEmoji[strategy]} {label}</span>
          {trade && (
            <Badge variant={statusColor}>
              {trade.status as string}
            </Badge>
          )}
        </CardTitle>
      </CardHeader>
      <CardContent>
        {trade ? (
          <div className="space-y-2 text-sm">
            <div className="font-medium">
              {trade.direction as string} {trade.strike as number} {trade.option_type as string}
            </div>

            {/* P&L */}
            {pnl != null && (
              <div className={`text-lg font-mono font-bold ${pnl >= 0 ? "text-green-500" : "text-red-500"}`}>
                {pnl >= 0 ? "+" : ""}{pnl.toFixed(1)}%
              </div>
            )}

            {/* Trade levels */}
            <div className="grid grid-cols-2 gap-x-3 gap-y-1 text-xs">
              {entry != null && (
                <div><span className="text-muted-foreground">Entry:</span> <span className="font-mono">{entry.toFixed(2)}</span></div>
              )}
              {sl != null && (
                <div><span className="text-muted-foreground">SL:</span> <span className="font-mono text-red-500">{sl.toFixed(2)}</span></div>
              )}
              {t1 != null && (
                <div><span className="text-muted-foreground">T1:</span> <span className="font-mono text-green-500">{t1.toFixed(2)}</span></div>
              )}
              {t2 != null && (
                <div><span className="text-muted-foreground">T2:</span> <span className="font-mono text-green-500">{t2.toFixed(2)}</span></div>
              )}
              {trailing != null && t1Hit && (
                <div className="col-span-2"><span className="text-muted-foreground">Trailing SL:</span> <span className="font-mono text-yellow-500">{trailing.toFixed(2)}</span></div>
              )}
            </div>

            {/* Risk in Rupees */}
            {entryCost != null && (
              <div className="border-t border-border pt-1 grid grid-cols-2 gap-x-3 gap-y-1 text-xs">
                <div><span className="text-muted-foreground">Lot (75):</span> <span className="font-mono">{entryCost.toFixed(0)}</span></div>
                {maxRisk != null && (
                  <div><span className="text-muted-foreground">Risk:</span> <span className="font-mono text-red-500">{maxRisk.toFixed(0)}</span></div>
                )}
                {t1Profit != null && (
                  <div><span className="text-muted-foreground">T1 Reward:</span> <span className="font-mono text-green-500">{t1Profit.toFixed(0)}</span></div>
                )}
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
