"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useDashboardStore } from "@/stores/dashboard-store";

export function DirectionalForceCard() {
  const blob = useDashboardStore((s) => s.analysis?.analysis_blob);
  const sa = blob?.strength_analysis;

  if (!sa) {
    return (
      <Card>
        <CardHeader><CardTitle className="text-sm font-medium text-muted-foreground">Directional Strength</CardTitle></CardHeader>
        <CardContent><p className="text-sm text-muted-foreground">Waiting for data...</p></CardContent>
      </Card>
    );
  }

  const dirColor = sa.direction.includes("Bull") ? "text-green-500" : sa.direction.includes("Bear") ? "text-red-500" : "text-yellow-500";

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium text-muted-foreground">Directional Strength</CardTitle>
      </CardHeader>
      <CardContent className="space-y-2 text-sm">
        <div className="flex justify-between">
          <span className="text-muted-foreground">Put Strength</span>
          <span className="font-mono">
            {sa.put_strength.ratio.toFixed(2)} / {sa.put_strength.score.toFixed(1)}
          </span>
        </div>
        <div className="flex justify-between">
          <span className="text-muted-foreground">Call Strength</span>
          <span className="font-mono">
            {sa.call_strength.ratio.toFixed(2)} / {sa.call_strength.score.toFixed(1)}
          </span>
        </div>
        <div className="border-t border-border pt-2 flex justify-between">
          <span className="text-muted-foreground">Direction</span>
          <span className={`font-mono font-medium ${dirColor}`}>{sa.direction}</span>
        </div>
        <div className="flex justify-between">
          <span className="text-muted-foreground">Net Strength</span>
          <span className={`font-mono font-medium ${sa.net_strength > 0 ? "text-green-500" : sa.net_strength < 0 ? "text-red-500" : ""}`}>
            {sa.net_strength > 0 ? "+" : ""}{sa.net_strength.toFixed(1)}
          </span>
        </div>
      </CardContent>
    </Card>
  );
}
