"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useDashboardStore } from "@/stores/dashboard-store";
import { fmt, fmtCompact } from "@/lib/format";

function MetricRow({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div className="flex items-center justify-between py-0.5">
      <span className="text-xs text-muted-foreground">{label}</span>
      <span className="text-xs text-muted-foreground mx-1 flex-1 border-b border-dotted border-muted-foreground/30" />
      <span className={`font-mono text-sm ${color ?? ""}`}>{value}</span>
    </div>
  );
}

export function KeyMetricsCard() {
  const analysis = useDashboardStore((s) => s.analysis);
  const blob = analysis?.analysis_blob;

  if (!analysis) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-medium text-muted-foreground">Key Metrics</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">Waiting for data...</p>
        </CardContent>
      </Card>
    );
  }

  const basisColor =
    (analysis.futures_basis ?? 0) > 0
      ? "text-green-500"
      : (analysis.futures_basis ?? 0) < 0
        ? "text-red-500"
        : "";
  const ivSkew = analysis.iv_skew ?? 0;
  const ivColor = ivSkew > 2 ? "text-red-500" : ivSkew < -2 ? "text-green-500" : "";
  const pcr = blob?.pcr ?? 0;
  const pcrColor = pcr > 1.2 ? "text-green-500" : pcr < 0.8 ? "text-red-500" : "";

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium text-muted-foreground">Key Metrics</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        {/* Price Levels */}
        <div className="space-y-0.5">
          <p className="text-[10px] font-medium text-muted-foreground uppercase tracking-wider">
            Price Levels
          </p>
          <MetricRow label="Spot" value={fmt(analysis.spot_price, 2)} />
          <MetricRow label="ATM Strike" value={fmt(analysis.atm_strike)} />
          <MetricRow label="Max Pain" value={fmt(analysis.max_pain)} />
        </div>

        {/* Sentiment */}
        <div className="space-y-0.5 border-t border-border pt-2">
          <p className="text-[10px] font-medium text-muted-foreground uppercase tracking-wider">
            Sentiment
          </p>
          <MetricRow label="PCR" value={pcr.toFixed(2)} color={pcrColor} />
          <MetricRow label="Vol PCR" value={(blob?.volume_pcr ?? 0).toFixed(2)} />
          <MetricRow label="IV Skew" value={ivSkew.toFixed(2)} color={ivColor} />
          <MetricRow label="VIX" value={analysis.vix?.toFixed(2) ?? "-"} />
        </div>

        {/* Futures */}
        <div className="space-y-0.5 border-t border-border pt-2">
          <p className="text-[10px] font-medium text-muted-foreground uppercase tracking-wider">
            Futures
          </p>
          <MetricRow
            label="Basis"
            value={`${analysis.futures_basis?.toFixed(1) ?? "-"} pts`}
            color={basisColor}
          />
          <MetricRow
            label="OI"
            value={analysis.futures_oi ? fmtCompact(analysis.futures_oi) : "-"}
          />
          <MetricRow
            label="Regime"
            value={
              typeof blob?.market_regime === "object" && blob.market_regime !== null
                ? (blob.market_regime as unknown as { regime: string }).regime
                : (blob?.market_regime as string) ?? "-"
            }
          />
          <MetricRow label="Expiry" value={blob?.expiry_date ?? "-"} />
        </div>
      </CardContent>
    </Card>
  );
}
