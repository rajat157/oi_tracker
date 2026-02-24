"use client";

import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import type { TradeBase } from "@/lib/types";

interface TradeTableProps {
  trades: TradeBase[];
  loading?: boolean;
}

const statusColors: Record<string, string> = {
  ACTIVE: "bg-blue-500/10 text-blue-600",
  PENDING: "bg-yellow-500/10 text-yellow-600",
  WON: "bg-green-500/10 text-green-600",
  LOST: "bg-red-500/10 text-red-600",
  CANCELLED: "bg-gray-500/10 text-gray-500",
  EXPIRED: "bg-gray-500/10 text-gray-500",
};

export function TradeTable({ trades, loading }: TradeTableProps) {
  if (loading) {
    return <p className="text-muted-foreground py-8 text-center">Loading trades...</p>;
  }

  if (trades.length === 0) {
    return <p className="text-muted-foreground py-8 text-center">No trades found</p>;
  }

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Time</TableHead>
          <TableHead>Direction</TableHead>
          <TableHead>Strike</TableHead>
          <TableHead>Entry</TableHead>
          <TableHead>SL</TableHead>
          <TableHead>Exit</TableHead>
          <TableHead>P&L</TableHead>
          <TableHead>Status</TableHead>
          <TableHead>Reason</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {trades.map((trade) => (
          <TableRow key={trade.id}>
            <TableCell className="font-mono text-xs">
              {new Date(trade.created_at).toLocaleString("en-IN", {
                month: "short",
                day: "numeric",
                hour: "2-digit",
                minute: "2-digit",
              })}
            </TableCell>
            <TableCell>{trade.direction}</TableCell>
            <TableCell className="font-mono">
              {trade.strike} {trade.option_type}
            </TableCell>
            <TableCell className="font-mono">{trade.entry_premium.toFixed(2)}</TableCell>
            <TableCell className="font-mono">{trade.sl_premium.toFixed(2)}</TableCell>
            <TableCell className="font-mono">
              {trade.exit_premium?.toFixed(2) ?? "-"}
            </TableCell>
            <TableCell
              className={`font-mono ${
                (trade.profit_loss_pct ?? 0) >= 0 ? "text-green-600" : "text-red-600"
              }`}
            >
              {trade.profit_loss_pct != null ? `${trade.profit_loss_pct.toFixed(1)}%` : "-"}
            </TableCell>
            <TableCell>
              <Badge className={statusColors[trade.status] || ""} variant="outline">
                {trade.status}
              </Badge>
            </TableCell>
            <TableCell className="text-xs text-muted-foreground max-w-[150px] truncate">
              {trade.exit_reason ?? "-"}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
