"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useDashboardStore } from "@/stores/dashboard-store";

export function CombinedScoreGauge() {
  const analysis = useDashboardStore((s) => s.analysis);
  const blob = analysis?.analysis_blob;
  const score = blob?.combined_score ?? 0;
  const confidence = analysis?.signal_confidence ?? 0;

  // Map -100..+100 to 0..100% for marker position
  const markerPct = Math.min(Math.max((score + 100) / 2, 0), 100);

  const getScoreColor = (s: number) => {
    if (s > 30) return "text-green-500";
    if (s > 10) return "text-green-400";
    if (s >= -10) return "text-yellow-400";
    if (s >= -30) return "text-red-400";
    return "text-red-500";
  };

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium text-muted-foreground">
          Combined Score
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className={`text-4xl font-bold font-mono ${getScoreColor(score)}`}>
          {score > 0 ? "+" : ""}{score.toFixed(1)}
        </div>

        {/* Horizontal gauge */}
        <div className="relative w-full h-3 rounded-full overflow-hidden bg-gradient-to-r from-red-600 via-yellow-500 to-green-600">
          <div
            className="absolute top-0 w-1 h-full bg-white shadow-md transition-all duration-500"
            style={{ left: `${markerPct}%` }}
          />
        </div>
        <div className="flex justify-between text-xs text-muted-foreground">
          <span>Bearish</span>
          <span>Neutral</span>
          <span>Bullish</span>
        </div>

        {/* Confidence secondary */}
        <div className="flex items-center justify-between text-sm pt-1 border-t border-border">
          <span className="text-muted-foreground">Confidence</span>
          <span className="font-mono font-medium">
            {confidence.toFixed(0)}%
            <span className="text-xs text-muted-foreground ml-1">
              {confidence >= 65 ? "Tradeable" : confidence >= 50 ? "Marginal" : "Low"}
            </span>
          </span>
        </div>
      </CardContent>
    </Card>
  );
}
