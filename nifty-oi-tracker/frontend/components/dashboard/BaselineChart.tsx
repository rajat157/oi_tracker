"use client";

import { useEffect, useRef } from "react";
import type { UTCTimestamp } from "lightweight-charts";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { fmtCompact, fmtSigned } from "@/lib/format";

interface BaselineChartProps {
  title: string;
  data: { time: number; value: number }[];
  height?: number;
  label?: string;
}

export function BaselineChart({ title, data, height = 250, label }: BaselineChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const tooltipRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!containerRef.current || data.length === 0) return;

    let disposed = false;

    import("lightweight-charts").then(({ createChart, BaselineSeries }) => {
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
        rightPriceScale: { borderColor: "rgba(255,255,255,0.1)" },
        timeScale: {
          borderColor: "rgba(255,255,255,0.1)",
          timeVisible: true,
          secondsVisible: false,
        },
        crosshair: {
          horzLine: { color: "rgba(255,255,255,0.2)" },
          vertLine: { color: "rgba(255,255,255,0.2)" },
        },
        localization: {
          priceFormatter: (price: number) => fmtCompact(price),
        },
      });

      const series = chart.addSeries(BaselineSeries, {
        baseValue: { type: "price", price: 0 },
        topLineColor: "rgba(34, 197, 94, 1)",
        topFillColor1: "rgba(34, 197, 94, 0.28)",
        topFillColor2: "rgba(34, 197, 94, 0.05)",
        bottomLineColor: "rgba(239, 68, 68, 1)",
        bottomFillColor1: "rgba(239, 68, 68, 0.05)",
        bottomFillColor2: "rgba(239, 68, 68, 0.28)",
        lineWidth: 2,
        priceLineVisible: false,
        lastValueVisible: true,
        ...(label ? { title: label } : {}),
      });

      series.setData(data.map((d) => ({ time: d.time as UTCTimestamp, value: d.value })));
      chart.timeScale().fitContent();

      // Crosshair tooltip
      chart.subscribeCrosshairMove((param) => {
        if (!tooltipRef.current) return;
        if (!param.time || !param.seriesData.size) {
          tooltipRef.current.style.display = "none";
          return;
        }
        const point = param.seriesData.get(series) as { value?: number } | undefined;
        if (!point || point.value == null) {
          tooltipRef.current.style.display = "none";
          return;
        }
        const t = new Date((param.time as number) * 1000);
        const timeStr = t.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" });
        const val = point.value;
        const color = val >= 0 ? "#22c55e" : "#ef4444";
        tooltipRef.current.style.display = "block";
        tooltipRef.current.innerHTML =
          `<div style="color:#a1a1b5">${timeStr}</div>` +
          `<div style="color:${color};font-family:monospace">${fmtSigned(val)}</div>`;
      });

      const ro = new ResizeObserver((entries) => {
        for (const entry of entries) {
          const w = entry.contentRect.width;
          if (w > 0) chart.applyOptions({ width: w });
        }
      });
      ro.observe(containerRef.current!);

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
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <Card>
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between">
          <CardTitle className="text-sm font-medium text-muted-foreground">{title}</CardTitle>
          <div className="flex items-center gap-3 text-[10px]">
            <span className="flex items-center gap-1">
              <span className="inline-block w-2 h-2 rounded-full bg-green-500" />
              <span className="text-muted-foreground">Bullish (above 0)</span>
            </span>
            <span className="flex items-center gap-1">
              <span className="inline-block w-2 h-2 rounded-full bg-red-500" />
              <span className="text-muted-foreground">Bearish (below 0)</span>
            </span>
          </div>
        </div>
      </CardHeader>
      <CardContent>
        {data.length === 0 ? (
          <p className="text-sm text-muted-foreground py-8 text-center">No chart data</p>
        ) : (
          <div className="relative">
            <div
              ref={tooltipRef}
              className="absolute top-2 left-2 z-10 bg-card/90 border border-border rounded px-2 py-1 text-xs pointer-events-none"
              style={{ display: "none" }}
            />
            <div ref={containerRef} />
          </div>
        )}
      </CardContent>
    </Card>
  );
}
