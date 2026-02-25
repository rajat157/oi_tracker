"use client";

import { useDashboardStore } from "@/stores/dashboard-store";

export function ScoreBreakdown() {
  const blob = useDashboardStore((s) => s.analysis?.analysis_blob);
  if (!blob?.weights) return null;

  const rows = [
    { label: "Below Spot", score: blob.below_spot_score, weight: blob.weights.below_spot },
    { label: "Above Spot", score: blob.above_spot_score, weight: blob.weights.above_spot },
  ];

  if (blob.weights.momentum > 0) {
    rows.push({ label: "Momentum", score: blob.momentum_score, weight: blob.weights.momentum });
  }

  return (
    <details className="text-sm">
      <summary className="cursor-pointer text-muted-foreground hover:text-foreground">
        Score Breakdown
      </summary>
      <div className="mt-2 space-y-1">
        {rows.map((r) => (
          <div key={r.label} className="flex justify-between">
            <span className="text-muted-foreground">{r.label}</span>
            <span className="font-mono">
              <span className={r.score > 0 ? "text-green-500" : r.score < 0 ? "text-red-500" : ""}>
                {r.score > 0 ? "+" : ""}{r.score?.toFixed(1) ?? "0"}
              </span>
              <span className="text-muted-foreground ml-2">({(r.weight * 100).toFixed(0)}%)</span>
            </span>
          </div>
        ))}
      </div>
    </details>
  );
}
