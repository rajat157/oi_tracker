"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useDashboardStore } from "@/stores/dashboard-store";

export function AlertCards() {
  const blob = useDashboardStore((s) => s.analysis?.analysis_blob);

  const trapWarning = blob?.trap_warning;

  // No alerts to show
  if (!trapWarning) return null;

  return (
    <div className="space-y-3">
      {trapWarning && (
        <Card className="border-yellow-500/30">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium text-yellow-500">
              Trap Warning
            </CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-sm">{trapWarning}</p>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
