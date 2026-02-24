"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useDashboardStore } from "@/stores/dashboard-store";

export function ScoreGauge() {
  const analysis = useDashboardStore((s) => s.analysis);
  const confidence = analysis?.signal_confidence ?? 0;

  // Map 0-100 to a color gradient
  const getColor = (val: number) => {
    if (val >= 80) return "text-green-600";
    if (val >= 65) return "text-green-500";
    if (val >= 50) return "text-yellow-500";
    if (val >= 30) return "text-orange-500";
    return "text-red-500";
  };

  const getBarWidth = (val: number) => `${Math.min(Math.max(val, 0), 100)}%`;

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium text-muted-foreground">
          Signal Confidence
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className={`text-4xl font-bold font-mono ${getColor(confidence)}`}>
          {confidence.toFixed(0)}%
        </div>
        {/* Progress bar */}
        <div className="w-full bg-muted rounded-full h-2">
          <div
            className={`h-2 rounded-full transition-all duration-500 ${
              confidence >= 65 ? "bg-green-500" : confidence >= 50 ? "bg-yellow-500" : "bg-red-500"
            }`}
            style={{ width: getBarWidth(confidence) }}
          />
        </div>
        <div className="flex justify-between text-xs text-muted-foreground">
          <span>0</span>
          <span className="font-medium">
            {confidence >= 65 ? "Tradeable" : confidence >= 50 ? "Marginal" : "Low"}
          </span>
          <span>100</span>
        </div>
      </CardContent>
    </Card>
  );
}
