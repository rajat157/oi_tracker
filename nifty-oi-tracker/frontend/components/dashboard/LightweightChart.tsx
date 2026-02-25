"use client";

import { useEffect, useRef } from "react";
import type { UTCTimestamp } from "lightweight-charts";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export interface ChartLine {
  data: { time: number; value: number }[];
  color: string;
  label: string;
}

interface LightweightChartProps {
  title: string;
  lines: ChartLine[];
  height?: number;
}

export function LightweightChart({ title, lines, height = 250 }: LightweightChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<ReturnType<typeof import("lightweight-charts").createChart> | null>(null);

  useEffect(() => {
    if (!containerRef.current) return;

    let disposed = false;

    import("lightweight-charts").then(({ createChart, LineSeries }) => {
      if (disposed || !containerRef.current) return;

      const chart = createChart(containerRef.current, {
        height,
        layout: {
          background: { color: "transparent" },
          textColor: "#e0e0e0",
          fontSize: 11,
        },
        grid: {
          vertLines: { color: "rgba(255,255,255,0.06)" },
          horzLines: { color: "rgba(255,255,255,0.06)" },
        },
        rightPriceScale: {
          borderColor: "rgba(255,255,255,0.1)",
        },
        timeScale: {
          borderColor: "rgba(255,255,255,0.1)",
          timeVisible: true,
          secondsVisible: false,
        },
        crosshair: {
          horzLine: { color: "rgba(255,255,255,0.2)" },
          vertLine: { color: "rgba(255,255,255,0.2)" },
        },
      });

      chartRef.current = chart;

      lines.forEach((line) => {
        const series = chart.addSeries(LineSeries, {
          color: line.color,
          lineWidth: 2,
          title: line.label,
          priceLineVisible: false,
          lastValueVisible: true,
        });
        if (line.data.length > 0) {
          series.setData(
            line.data.map((d) => ({ time: d.time as UTCTimestamp, value: d.value }))
          );
        }
      });

      chart.timeScale().fitContent();

      const ro = new ResizeObserver((entries) => {
        for (const entry of entries) {
          const w = entry.contentRect.width;
          if (w > 0) chart.applyOptions({ width: w });
        }
      });
      ro.observe(containerRef.current!);

      // Store cleanup in ref-accessible way
      (containerRef.current as HTMLDivElement & { _cleanup?: () => void })._cleanup = () => {
        ro.disconnect();
        chart.remove();
      };
    });

    return () => {
      disposed = true;
      if (containerRef.current) {
        const el = containerRef.current as HTMLDivElement & { _cleanup?: () => void };
        el._cleanup?.();
      }
      chartRef.current = null;
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Update data when lines change (after initial chart creation)
  useEffect(() => {
    if (!chartRef.current) return;
    // Recreate is simpler for data updates — we rely on the key-based remount pattern
    // or just rebuild series. For simplicity, trigger full remount via key in parent.
  }, [lines]);

  const hasData = lines.some((l) => l.data.length > 0);

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium text-muted-foreground">{title}</CardTitle>
      </CardHeader>
      <CardContent>
        {!hasData ? (
          <p className="text-sm text-muted-foreground py-8 text-center">No chart data</p>
        ) : (
          <div ref={containerRef} />
        )}
      </CardContent>
    </Card>
  );
}
