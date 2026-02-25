"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useDashboardStore } from "@/stores/dashboard-store";

function ForceBar({ label, value, maxVal }: { label: string; value: number; maxVal: number }) {
  const pct = maxVal > 0 ? Math.min(Math.abs(value) / maxVal * 100, 100) : 0;
  const color = value > 0 ? "bg-green-500" : value < 0 ? "bg-red-500" : "bg-muted";
  const textColor = value > 0 ? "text-green-500" : value < 0 ? "text-red-500" : "";

  return (
    <div className="space-y-1">
      <div className="flex justify-between text-sm">
        <span className="text-muted-foreground">{label}</span>
        <span className={`font-mono ${textColor}`}>{value > 0 ? "+" : ""}{value.toFixed(0)}</span>
      </div>
      <div className="w-full h-2 bg-muted rounded-full overflow-hidden">
        <div className={`h-full rounded-full transition-all duration-500 ${color}`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

export function ZoneForceCard() {
  const blob = useDashboardStore((s) => s.analysis?.analysis_blob);
  if (!blob) {
    return (
      <Card>
        <CardHeader><CardTitle className="text-sm font-medium text-muted-foreground">Zone Forces</CardTitle></CardHeader>
        <CardContent><p className="text-sm text-muted-foreground">Waiting for data...</p></CardContent>
      </Card>
    );
  }

  const belowNet = blob.below_spot?.net_force ?? 0;
  const aboveNet = blob.above_spot?.net_force ?? 0;
  const netOI = blob.net_oi_change ?? 0;
  const maxVal = Math.max(Math.abs(belowNet), Math.abs(aboveNet), Math.abs(netOI), 1);

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium text-muted-foreground">Zone Forces</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <ForceBar label="Below Spot (Support)" value={belowNet} maxVal={maxVal} />
        <ForceBar label="Above Spot (Resistance)" value={aboveNet} maxVal={maxVal} />
        <div className="border-t border-border pt-2">
          <ForceBar label="Net OI Change" value={netOI} maxVal={maxVal} />
        </div>
      </CardContent>
    </Card>
  );
}
