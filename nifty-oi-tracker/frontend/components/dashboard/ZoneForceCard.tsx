"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useDashboardStore } from "@/stores/dashboard-store";
import { fmtSigned } from "@/lib/format";

function ForceColumn({
  label,
  value,
  score,
  maxVal,
  color,
}: {
  label: string;
  value: number;
  score: number;
  maxVal: number;
  color: "green" | "red";
}) {
  const pct = maxVal > 0 ? Math.min(Math.abs(value) / maxVal * 100, 100) : 0;
  const barColor = color === "green" ? "bg-green-500" : "bg-red-500";
  const textColor = color === "green" ? "text-green-500" : "text-red-500";

  return (
    <div className="flex-1 space-y-1.5">
      <p className="text-xs text-muted-foreground font-medium">{label}</p>
      <p className={`font-mono text-lg font-bold ${textColor}`}>
        {fmtSigned(value)}
      </p>
      <div className="w-full h-2 bg-muted rounded-full overflow-hidden">
        <div
          className={`h-full rounded-full transition-all duration-500 ${barColor}`}
          style={{ width: `${pct}%` }}
        />
      </div>
      <p className="text-[10px] text-muted-foreground">
        Score: <span className="font-mono">{score.toFixed(1)}</span>
      </p>
    </div>
  );
}

export function ZoneForceCard() {
  const blob = useDashboardStore((s) => s.analysis?.analysis_blob);

  if (!blob) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-medium text-muted-foreground">Zone Forces</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">Waiting for data...</p>
        </CardContent>
      </Card>
    );
  }

  const belowNet = blob.below_spot?.net_force ?? 0;
  const aboveNet = blob.above_spot?.net_force ?? 0;
  const belowScore = blob.below_spot?.score ?? 0;
  const aboveScore = blob.above_spot?.score ?? 0;
  const netOI = blob.net_oi_change ?? 0;
  const maxVal = Math.max(Math.abs(belowNet), Math.abs(aboveNet), 1);

  const netColor = netOI > 0 ? "text-green-500" : netOI < 0 ? "text-red-500" : "text-muted-foreground";

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium text-muted-foreground">Zone Forces</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        {/* Side-by-side: Support vs Resistance */}
        <div className="flex gap-4">
          <ForceColumn
            label="Support (Below)"
            value={belowNet}
            score={belowScore}
            maxVal={maxVal}
            color="green"
          />
          <div className="w-px bg-border" />
          <ForceColumn
            label="Resistance (Above)"
            value={aboveNet}
            score={aboveScore}
            maxVal={maxVal}
            color="red"
          />
        </div>

        {/* Net Force summary */}
        <div className="border-t border-border pt-2 flex items-center justify-between">
          <span className="text-xs text-muted-foreground">Net OI Change</span>
          <span className={`font-mono text-sm font-bold ${netColor}`}>
            {fmtSigned(netOI)}
          </span>
        </div>
      </CardContent>
    </Card>
  );
}
