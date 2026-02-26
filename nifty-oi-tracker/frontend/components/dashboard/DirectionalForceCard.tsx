"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useDashboardStore } from "@/stores/dashboard-store";
import { fmtSigned } from "@/lib/format";

function TugOfWarBar({
  putScore,
  callScore,
}: {
  putScore: number;
  callScore: number;
}) {
  const net = putScore - callScore; // positive = bullish (green), negative = bearish (red)
  const greenPct = Math.max(5, Math.min(95, 50 + net / 2));

  return (
    <div className="space-y-1.5">
      <div className="relative w-full h-2 rounded-full overflow-hidden bg-muted">
        <div
          className="absolute inset-y-0 left-0 bg-green-500/80 rounded-l-full transition-all duration-700"
          style={{ width: `${greenPct}%` }}
        />
        <div
          className="absolute inset-y-0 right-0 bg-red-500/80 rounded-r-full transition-all duration-700"
          style={{ width: `${100 - greenPct}%` }}
        />
        <div className="absolute inset-y-0 left-1/2 w-px bg-foreground/20" />
      </div>
      <div className="flex justify-between text-[10px] text-muted-foreground">
        <span>
          Put <span className="font-mono text-green-500">{putScore.toFixed(1)}</span>
        </span>
        <span>
          Call <span className="font-mono text-red-500">{callScore.toFixed(1)}</span>
        </span>
      </div>
    </div>
  );
}

function Row({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-xs text-muted-foreground">{label}</span>
      <span className="font-mono text-xs">{children}</span>
    </div>
  );
}

export function DirectionalForceCard() {
  const blob = useDashboardStore((s) => s.analysis?.analysis_blob);
  const sa = blob?.strength_analysis;

  if (!sa) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-medium text-muted-foreground">
            Directional Strength
          </CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">Waiting for data...</p>
        </CardContent>
      </Card>
    );
  }

  const dirColor =
    sa.direction === "Bullish"
      ? "text-green-500"
      : sa.direction === "Bearish"
        ? "text-red-500"
        : "text-yellow-500";

  const netColor =
    sa.net_strength > 0
      ? "text-green-500"
      : sa.net_strength < 0
        ? "text-red-500"
        : "text-muted-foreground";

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm font-medium text-muted-foreground">
          Directional Strength
        </CardTitle>
      </CardHeader>

      <CardContent className="space-y-3">
        <TugOfWarBar
          putScore={sa.put_strength.score}
          callScore={sa.call_strength.score}
        />

        <div className="space-y-1.5">
          <Row label="Put Strength">
            <span className="text-green-500">{sa.put_strength.ratio.toFixed(2)}</span>
            <span className="text-muted-foreground"> / {sa.put_strength.score.toFixed(1)}</span>
          </Row>
          <Row label="Call Strength">
            <span className="text-red-500">{sa.call_strength.ratio.toFixed(2)}</span>
            <span className="text-muted-foreground"> / {sa.call_strength.score.toFixed(1)}</span>
          </Row>
        </div>

        <div className="border-t border-border pt-2 space-y-1.5">
          <Row label="Direction">
            <span className={`font-medium ${dirColor}`}>{sa.direction}</span>
          </Row>
          <Row label="Net Strength">
            <span className={`font-medium ${netColor}`}>
              {fmtSigned(sa.net_strength, 1)}
            </span>
          </Row>
        </div>
      </CardContent>
    </Card>
  );
}
