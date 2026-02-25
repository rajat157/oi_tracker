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
  Legend,
} from "recharts";
import type { AnalysisHistoryItem } from "@/lib/types";

interface ForceChartProps {
  title: string;
  data: AnalysisHistoryItem[];
  line1Key: keyof AnalysisHistoryItem;
  line2Key: keyof AnalysisHistoryItem;
  line1Color: string;
  line2Color: string;
  line1Label: string;
  line2Label: string;
  height?: number;
}

export function ForceChart({
  title,
  data,
  line1Key,
  line2Key,
  line1Color,
  line2Color,
  line1Label,
  line2Label,
  height = 250,
}: ForceChartProps) {
  const chartData = data.map((item) => ({
    time: new Date(item.timestamp).toLocaleTimeString("en-IN", {
      hour: "2-digit",
      minute: "2-digit",
    }),
    [line1Label]: item[line1Key],
    [line2Label]: item[line2Key],
  }));

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium text-muted-foreground">{title}</CardTitle>
      </CardHeader>
      <CardContent>
        {chartData.length === 0 ? (
          <p className="text-sm text-muted-foreground py-8 text-center">No chart data</p>
        ) : (
          <ResponsiveContainer width="100%" height={height}>
            <LineChart data={chartData}>
              <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" />
              <XAxis dataKey="time" tick={{ fontSize: 11 }} stroke="hsl(var(--muted-foreground))" />
              <YAxis tick={{ fontSize: 11 }} stroke="hsl(var(--muted-foreground))" />
              <Tooltip
                contentStyle={{
                  backgroundColor: "hsl(var(--card))",
                  border: "1px solid hsl(var(--border))",
                  borderRadius: "var(--radius)",
                  color: "hsl(var(--foreground))",
                }}
              />
              <Legend />
              <Line
                type="monotone"
                dataKey={line1Label}
                stroke={line1Color}
                strokeWidth={2}
                dot={false}
              />
              <Line
                type="monotone"
                dataKey={line2Label}
                stroke={line2Color}
                strokeWidth={2}
                dot={false}
              />
            </LineChart>
          </ResponsiveContainer>
        )}
      </CardContent>
    </Card>
  );
}
