"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableFooter,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useDashboardStore } from "@/stores/dashboard-store";
import type { ZoneStrike, OTMITMStrike, ZoneData, OTMITMZone } from "@/lib/types";

function fmt(n: number | undefined) {
  if (n == null) return "-";
  return n.toLocaleString("en-IN", { maximumFractionDigits: 0 });
}

function fmtForce(n: number | undefined) {
  if (n == null) return "-";
  const s = n.toFixed(1);
  return n > 0 ? `+${s}` : s;
}

function colorForce(n: number | undefined) {
  if (n == null || n === 0) return "";
  return n > 0 ? "text-green-500" : "text-red-500";
}

function SpotZoneTable({ title, zone }: { title: string; zone: ZoneData | undefined }) {
  if (!zone?.strikes?.length) return null;
  return (
    <details className="group">
      <summary className="cursor-pointer text-sm font-medium text-muted-foreground hover:text-foreground">
        {title} (Net: <span className={colorForce(zone.net_force)}>{fmtForce(zone.net_force)}</span>)
      </summary>
      <div className="mt-2">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Strike</TableHead>
              <TableHead className="text-right">Bullish</TableHead>
              <TableHead className="text-right">Bearish</TableHead>
              <TableHead className="text-right">Net</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {zone.strikes.map((s: ZoneStrike) => (
              <TableRow key={s.strike}>
                <TableCell className="font-mono">{s.strike}</TableCell>
                <TableCell className="text-right font-mono text-green-500">{fmtForce(s.bullish_force)}</TableCell>
                <TableCell className="text-right font-mono text-red-500">{fmtForce(s.bearish_force)}</TableCell>
                <TableCell className={`text-right font-mono ${colorForce(s.net_force)}`}>{fmtForce(s.net_force)}</TableCell>
              </TableRow>
            ))}
          </TableBody>
          <TableFooter>
            <TableRow>
              <TableCell className="font-medium">Total</TableCell>
              <TableCell className="text-right font-mono text-green-500">{fmtForce(zone.total_bullish_force)}</TableCell>
              <TableCell className="text-right font-mono text-red-500">{fmtForce(zone.total_bearish_force)}</TableCell>
              <TableCell className={`text-right font-mono font-medium ${colorForce(zone.net_force)}`}>{fmtForce(zone.net_force)}</TableCell>
            </TableRow>
          </TableFooter>
        </Table>
      </div>
    </details>
  );
}

function OTMITMTable({ title, zone, type }: { title: string; zone: OTMITMZone | undefined; type: "put" | "call" }) {
  if (!zone?.strikes?.length) return null;
  return (
    <details className="group">
      <summary className="cursor-pointer text-sm font-medium text-muted-foreground hover:text-foreground">
        {title} (Force: <span className={colorForce(zone.total_force)}>{fmtForce(zone.total_force)}</span>)
      </summary>
      <div className="mt-2">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Strike</TableHead>
              <TableHead className="text-right">OI</TableHead>
              <TableHead className="text-right">Change</TableHead>
              <TableHead className="text-right">Force</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {zone.strikes.map((s: OTMITMStrike) => {
              const oi = type === "put" ? s.put_oi : s.call_oi;
              const change = type === "put" ? s.put_oi_change : s.call_oi_change;
              const force = type === "put" ? s.put_force : s.call_force;
              return (
                <TableRow key={s.strike}>
                  <TableCell className="font-mono">{s.strike}</TableCell>
                  <TableCell className="text-right font-mono">{fmt(oi)}</TableCell>
                  <TableCell className={`text-right font-mono ${colorForce(change)}`}>{fmtForce(change)}</TableCell>
                  <TableCell className={`text-right font-mono ${colorForce(force)}`}>{fmtForce(force)}</TableCell>
                </TableRow>
              );
            })}
          </TableBody>
          <TableFooter>
            <TableRow>
              <TableCell className="font-medium">Total</TableCell>
              <TableCell className="text-right font-mono">{fmt(zone.total_oi)}</TableCell>
              <TableCell className={`text-right font-mono ${colorForce(zone.total_oi_change)}`}>{fmtForce(zone.total_oi_change)}</TableCell>
              <TableCell className={`text-right font-mono font-medium ${colorForce(zone.total_force)}`}>{fmtForce(zone.total_force)}</TableCell>
            </TableRow>
          </TableFooter>
        </Table>
      </div>
    </details>
  );
}

export function ZoneTables() {
  const blob = useDashboardStore((s) => s.analysis?.analysis_blob);

  if (!blob) {
    return (
      <Card>
        <CardHeader><CardTitle className="text-sm font-medium text-muted-foreground">Strike Tables</CardTitle></CardHeader>
        <CardContent><p className="text-sm text-muted-foreground">Waiting for data...</p></CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium text-muted-foreground">Strike Tables</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        {/* Below/Above Spot */}
        <SpotZoneTable title="Below Spot (Support)" zone={blob.below_spot} />
        <SpotZoneTable title="Above Spot (Resistance)" zone={blob.above_spot} />

        {/* OTM/ITM Zones */}
        <div className="border-t border-border pt-3 space-y-4">
          <OTMITMTable title="OTM Puts" zone={blob.otm_puts} type="put" />
          <OTMITMTable title="ITM Calls" zone={blob.itm_calls} type="call" />
          <OTMITMTable title="OTM Calls" zone={blob.otm_calls} type="call" />
          <OTMITMTable title="ITM Puts" zone={blob.itm_puts} type="put" />
        </div>
      </CardContent>
    </Card>
  );
}
