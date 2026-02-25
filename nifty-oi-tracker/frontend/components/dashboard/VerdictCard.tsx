"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { useDashboardStore } from "@/stores/dashboard-store";
import { ScoreBreakdown } from "./ScoreBreakdown";

const verdictColors: Record<string, string> = {
  "Slightly Bullish": "bg-green-500/10 text-green-500 border-green-500/20",
  "Slightly Bearish": "bg-red-500/10 text-red-500 border-red-500/20",
  Neutral: "bg-yellow-500/10 text-yellow-500 border-yellow-500/20",
  Bullish: "bg-green-600/10 text-green-400 border-green-600/20",
  Bearish: "bg-red-600/10 text-red-400 border-red-600/20",
};

const confirmationConfig: Record<string, { icon: string; color: string }> = {
  CONFIRMED: { icon: "✓", color: "text-green-500" },
  CONFLICT: { icon: "⚠", color: "text-yellow-500" },
  REVERSAL_ALERT: { icon: "‼", color: "text-red-500" },
};

function getScoreColor(s: number) {
  if (s > 30) return "text-green-500";
  if (s > 10) return "text-green-400";
  if (s >= -10) return "text-yellow-400";
  if (s >= -30) return "text-red-400";
  return "text-red-500";
}

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

  const blob = analysis.analysis_blob;
  const score = blob?.combined_score ?? 0;
  const confidence = analysis.signal_confidence ?? 0;
  const markerPct = Math.min(Math.max((score + 100) / 2, 0), 100);
  const colorClass = verdictColors[analysis.verdict] || verdictColors.Neutral;

  const confLabel = confidence >= 65 ? "Tradeable" : confidence >= 50 ? "Marginal" : "Low";
  const confLabelColor =
    confidence >= 65 ? "text-green-500" : confidence >= 50 ? "text-yellow-500" : "text-red-500";

  const confStatus = blob?.confirmation_status;
  const confConfig = confStatus
    ? confirmationConfig[confStatus] ?? confirmationConfig.CONFLICT
    : null;

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium text-muted-foreground">
          Market Verdict
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        {/* Verdict badge */}
        <Badge className={`text-lg px-3 py-1 ${colorClass}`}>{analysis.verdict}</Badge>

        {/* Score gauge */}
        <div className="space-y-1">
          <div className="flex items-center justify-between">
            <span className="text-xs text-muted-foreground">Score</span>
            <span className={`font-mono text-xl font-bold ${getScoreColor(score)}`}>
              {score > 0 ? "+" : ""}
              {score.toFixed(1)}
            </span>
          </div>
          <div className="relative w-full h-2.5 rounded-full overflow-hidden bg-gradient-to-r from-red-600 via-yellow-500 to-green-600">
            <div
              className="absolute top-0 w-1 h-full bg-white shadow-md transition-all duration-500"
              style={{ left: `${markerPct}%` }}
            />
          </div>
          <div className="flex justify-between text-[10px] text-muted-foreground">
            <span>Bearish</span>
            <span>Neutral</span>
            <span>Bullish</span>
          </div>
        </div>

        {/* Confidence */}
        <div className="flex items-center justify-between">
          <span className="text-sm text-muted-foreground">Confidence</span>
          <div className="flex items-center gap-2">
            <span className="font-mono text-sm font-medium">{confidence.toFixed(0)}%</span>
            <span className={`text-xs font-medium ${confLabelColor}`}>{confLabel}</span>
          </div>
        </div>

        {/* Confirmation */}
        {confConfig && (
          <div className={`flex items-center gap-2 text-sm ${confConfig.color}`}>
            <span className="text-base">{confConfig.icon}</span>
            <span className="font-medium">{confStatus}</span>
            {blob?.confirmation_message && (
              <span className="text-muted-foreground text-xs">
                {blob.confirmation_message}
              </span>
            )}
          </div>
        )}

        {/* Score Breakdown (collapsible) */}
        <ScoreBreakdown />
      </CardContent>
    </Card>
  );
}
