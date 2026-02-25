"use client";

import { useDashboardStore } from "@/stores/dashboard-store";

const statusConfig: Record<string, { icon: string; color: string }> = {
  CONFIRMED: { icon: "\u2713", color: "text-green-500" },
  CONFLICT: { icon: "\u26A0", color: "text-yellow-500" },
  REVERSAL_ALERT: { icon: "\u203C", color: "text-red-500" },
};

export function ConfirmationBadge() {
  const blob = useDashboardStore((s) => s.analysis?.analysis_blob);
  if (!blob?.confirmation_status) return null;

  const config = statusConfig[blob.confirmation_status] ?? statusConfig.CONFLICT;

  return (
    <div className={`flex items-center gap-2 text-sm ${config.color}`}>
      <span className="text-base">{config.icon}</span>
      <span className="font-medium">{blob.confirmation_status}</span>
      {blob.confirmation_message && (
        <span className="text-muted-foreground text-xs">{blob.confirmation_message}</span>
      )}
    </div>
  );
}
