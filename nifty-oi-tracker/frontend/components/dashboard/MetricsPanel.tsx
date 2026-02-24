"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useDashboardStore } from "@/stores/dashboard-store";

export function MetricsPanel() {
  const analysis = useDashboardStore((s) => s.analysis);

  const metrics = [
    { label: "VIX", value: analysis?.vix?.toFixed(2) ?? "-", unit: "" },
    { label: "IV Skew", value: analysis?.iv_skew?.toFixed(2) ?? "-", unit: "" },
    { label: "Max Pain", value: analysis?.max_pain?.toLocaleString() ?? "-", unit: "" },
    {
      label: "Futures Basis",
      value: analysis?.futures_basis?.toFixed(1) ?? "-",
      unit: "pts",
    },
    {
      label: "Futures OI",
      value: analysis?.futures_oi
        ? (analysis.futures_oi / 1000).toFixed(0) + "K"
        : "-",
      unit: "",
    },
    { label: "ATM Strike", value: analysis?.atm_strike?.toLocaleString() ?? "-", unit: "" },
  ];

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium text-muted-foreground">
          Key Metrics
        </CardTitle>
      </CardHeader>
      <CardContent>
        <div className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
          {metrics.map((m) => (
            <div key={m.label} className="flex justify-between">
              <span className="text-muted-foreground">{m.label}</span>
              <span className="font-mono">
                {m.value}
                {m.unit ? ` ${m.unit}` : ""}
              </span>
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}
