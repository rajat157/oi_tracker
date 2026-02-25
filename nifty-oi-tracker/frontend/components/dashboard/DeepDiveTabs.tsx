"use client";

import { useMemo } from "react";
import { Card, CardContent } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { BaselineChart } from "./BaselineChart";
import { ZoneTables } from "./ZoneTables";
import { ScoreBreakdown } from "./ScoreBreakdown";
import { useDashboardStore } from "@/stores/dashboard-store";

function toUnixSec(timestamp: string): number {
  return Math.floor(new Date(timestamp).getTime() / 1000);
}

function OITrendsContent() {
  const chartHistory = useDashboardStore((s) => s.chartHistory);

  const otmData = useMemo(
    () =>
      chartHistory.map((i) => ({
        time: toUnixSec(i.timestamp),
        value: (i.otm_put_force ?? 0) - (i.otm_call_force ?? 0),
      })),
    [chartHistory]
  );

  const itmData = useMemo(
    () =>
      chartHistory.map((i) => ({
        time: toUnixSec(i.timestamp),
        value: (i.itm_put_force ?? 0) - (i.itm_call_force ?? 0),
      })),
    [chartHistory]
  );

  return (
    <div key={chartHistory.length} className="grid grid-cols-1 md:grid-cols-2 gap-4">
      <BaselineChart title="OTM Net Force" data={otmData} label="Put − Call" height={250} />
      <BaselineChart title="ITM Net Force" data={itmData} label="Put − Call" height={250} />
    </div>
  );
}

export function DeepDiveTabs() {
  return (
    <Card>
      <CardContent className="pt-4">
        <Tabs defaultValue="trends">
          <TabsList variant="line">
            <TabsTrigger value="trends">OI Trends</TabsTrigger>
            <TabsTrigger value="tables">Strike Tables</TabsTrigger>
            <TabsTrigger value="breakdown">Score Breakdown</TabsTrigger>
          </TabsList>
          <TabsContent value="trends" className="mt-4">
            <OITrendsContent />
          </TabsContent>
          <TabsContent value="tables" className="mt-4">
            <ZoneTables />
          </TabsContent>
          <TabsContent value="breakdown" className="mt-4">
            <ScoreBreakdown />
          </TabsContent>
        </Tabs>
      </CardContent>
    </Card>
  );
}
