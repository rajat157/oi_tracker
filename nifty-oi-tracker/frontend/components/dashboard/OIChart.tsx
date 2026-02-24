"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from "recharts";
import { useDashboardStore } from "@/stores/dashboard-store";

export function OIChart() {
  const chartHistory = useDashboardStore((s) => s.chartHistory);

  const data = chartHistory.map((item) => ({
    time: new Date(item.timestamp).toLocaleTimeString("en-IN", {
      hour: "2-digit",
      minute: "2-digit",
    }),
    spot: item.spot_price,
    confidence: item.signal_confidence,
  }));

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm font-medium text-muted-foreground">
          Spot Price & Confidence
        </CardTitle>
      </CardHeader>
      <CardContent>
        {data.length === 0 ? (
          <p className="text-sm text-muted-foreground py-8 text-center">
            No chart data available
          </p>
        ) : (
          <ResponsiveContainer width="100%" height={300}>
            <LineChart data={data}>
              <CartesianGrid strokeDasharray="3 3" className="stroke-muted" />
              <XAxis dataKey="time" className="text-xs" />
              <YAxis yAxisId="spot" orientation="left" className="text-xs" />
              <YAxis yAxisId="conf" orientation="right" domain={[0, 100]} className="text-xs" />
              <Tooltip />
              <Line
                yAxisId="spot"
                type="monotone"
                dataKey="spot"
                stroke="hsl(var(--primary))"
                strokeWidth={2}
                dot={false}
              />
              <Line
                yAxisId="conf"
                type="monotone"
                dataKey="confidence"
                stroke="hsl(var(--chart-2))"
                strokeWidth={1}
                strokeDasharray="4 2"
                dot={false}
              />
            </LineChart>
          </ResponsiveContainer>
        )}
      </CardContent>
    </Card>
  );
}
