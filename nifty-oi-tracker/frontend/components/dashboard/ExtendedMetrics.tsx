"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useDashboardStore } from "@/stores/dashboard-store";

function MetricRow({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div className="flex justify-between">
      <span className="text-muted-foreground">{label}</span>
      <span className={`font-mono ${color ?? ""}`}>{value}</span>
    </div>
  );
}

export function ExtendedMetrics() {
  const analysis = useDashboardStore((s) => s.analysis);
  const blob = analysis?.analysis_blob;

  const ivSkew = analysis?.iv_skew ?? 0;
  const ivColor = ivSkew > 2 ? "text-red-500" : ivSkew < -2 ? "text-green-500" : "";

  const pcr = blob?.pcr ?? 0;
  const pcrColor = pcr > 1.2 ? "text-green-500" : pcr < 0.8 ? "text-red-500" : "";

  const trendDir = blob?.strength_analysis?.direction ?? "-";
  const trendArrow = trendDir.includes("Bull") ? "\u2191" : trendDir.includes("Bear") ? "\u2193" : "\u2194";
  const netStrength = blob?.strength_analysis?.net_strength;

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium text-muted-foreground">
          Key Metrics
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        {/* Price Levels */}
        <div className="space-y-1 text-sm">
          <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide">Price Levels</p>
          <MetricRow label="Spot" value={analysis?.spot_price?.toFixed(2) ?? "-"} />
          <MetricRow label="ATM Strike" value={analysis?.atm_strike?.toLocaleString() ?? "-"} />
          <MetricRow label="Max Pain" value={analysis?.max_pain?.toLocaleString() ?? "-"} />
        </div>

        {/* Sentiment */}
        <div className="space-y-1 text-sm border-t border-border pt-2">
          <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide">Sentiment</p>
          <MetricRow label="PCR" value={pcr.toFixed(2)} color={pcrColor} />
          <MetricRow label="Volume PCR" value={(blob?.volume_pcr ?? 0).toFixed(2)} />
          <MetricRow label="IV Skew" value={ivSkew.toFixed(2)} color={ivColor} />
          <MetricRow label="VIX" value={analysis?.vix?.toFixed(2) ?? "-"} />
          <MetricRow label="Futures Basis" value={`${analysis?.futures_basis?.toFixed(1) ?? "-"} pts`} />
          <MetricRow
            label="Futures OI"
            value={analysis?.futures_oi ? `${(analysis.futures_oi / 1000).toFixed(0)}K` : "-"}
          />
        </div>

        {/* Activity */}
        <div className="space-y-1 text-sm border-t border-border pt-2">
          <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide">Activity</p>
          <MetricRow
            label="Market Trend"
            value={`${trendArrow} ${trendDir}${netStrength != null ? ` (${netStrength.toFixed(1)})` : ""}`}
          />
          <MetricRow
            label="Price Change"
            value={`${(blob?.price_change_pct ?? 0).toFixed(2)}%`}
            color={(blob?.price_change_pct ?? 0) >= 0 ? "text-green-500" : "text-red-500"}
          />
          <MetricRow label="Avg Call Conv." value={`${(blob?.avg_call_conviction ?? 0).toFixed(1)}x`} />
          <MetricRow label="Avg Put Conv." value={`${(blob?.avg_put_conviction ?? 0).toFixed(1)}x`} />
          <MetricRow label="Expiry" value={blob?.expiry_date ?? "-"} />
        </div>
      </CardContent>
    </Card>
  );
}
