"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { useDashboardStore } from "@/stores/dashboard-store";
import { ConfirmationBadge } from "./ConfirmationBadge";
import { ScoreBreakdown } from "./ScoreBreakdown";

const verdictColors: Record<string, string> = {
  "Slightly Bullish": "bg-green-500/10 text-green-500 border-green-500/20",
  "Slightly Bearish": "bg-red-500/10 text-red-500 border-red-500/20",
  Neutral: "bg-yellow-500/10 text-yellow-500 border-yellow-500/20",
  Bullish: "bg-green-600/10 text-green-400 border-green-600/20",
  Bearish: "bg-red-600/10 text-red-400 border-red-600/20",
};

export function VerdictCard() {
  const analysis = useDashboardStore((s) => s.analysis);

  if (!analysis) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-medium text-muted-foreground">
            Market Verdict
          </CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-muted-foreground">Waiting for data...</p>
        </CardContent>
      </Card>
    );
  }

  const colorClass = verdictColors[analysis.verdict] || verdictColors.Neutral;

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium text-muted-foreground">
          Market Verdict
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="flex items-center gap-3">
          <Badge className={`text-lg px-3 py-1 ${colorClass}`}>{analysis.verdict}</Badge>
        </div>
        <ConfirmationBadge />
        <div className="grid grid-cols-2 gap-2 text-sm">
          <div>
            <span className="text-muted-foreground">Spot: </span>
            <span className="font-mono">{analysis.spot_price.toFixed(2)}</span>
          </div>
          <div>
            <span className="text-muted-foreground">Confidence: </span>
            <span className="font-mono">{analysis.signal_confidence.toFixed(1)}%</span>
          </div>
          <div>
            <span className="text-muted-foreground">VIX: </span>
            <span className="font-mono">{analysis.vix.toFixed(2)}</span>
          </div>
          <div>
            <span className="text-muted-foreground">Max Pain: </span>
            <span className="font-mono">{analysis.max_pain}</span>
          </div>
        </div>
        <ScoreBreakdown />
      </CardContent>
    </Card>
  );
}
