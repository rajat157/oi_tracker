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
import { fmt, fmtSigned, colorDir } from "@/lib/format";
import type { ZoneStrike, OTMITMStrike, ZoneData, OTMITMZone } from "@/lib/types";

function SpotZoneTable({ title, zone }: { title: string; zone: ZoneData | undefined }) {
  if (!zone?.strikes?.length) return null;
  return (
    <details className="group">
      <summary className="cursor-pointer text-sm font-medium text-muted-foreground hover:text-foreground">
        {title} (Net: <span className={colorDir(zone.net_force)}>{fmtSigned(zone.net_force, 1)}</span>)
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
                <TableCell className="text-right font-mono text-green-500">{fmtSigned(s.bullish_force, 1)}</TableCell>
                <TableCell className="text-right font-mono text-red-500">{fmtSigned(s.bearish_force, 1)}</TableCell>
                <TableCell className={`text-right font-mono ${colorDir(s.net_force)}`}>{fmtSigned(s.net_force, 1)}</TableCell>
              </TableRow>
            ))}
          </TableBody>
          <TableFooter>
            <TableRow>
              <TableCell className="font-medium">Total</TableCell>
              <TableCell className="text-right font-mono text-green-500">{fmtSigned(zone.total_bullish_force, 1)}</TableCell>
              <TableCell className="text-right font-mono text-red-500">{fmtSigned(zone.total_bearish_force, 1)}</TableCell>
              <TableCell className={`text-right font-mono font-medium ${colorDir(zone.net_force)}`}>{fmtSigned(zone.net_force, 1)}</TableCell>
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
        {title} (Force: <span className={colorDir(zone.total_force)}>{fmtSigned(zone.total_force, 1)}</span>)
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
                  <TableCell className={`text-right font-mono ${colorDir(change)}`}>{fmtSigned(change)}</TableCell>
                  <TableCell className={`text-right font-mono ${colorDir(force)}`}>{fmtSigned(force, 1)}</TableCell>
                </TableRow>
              );
            })}
          </TableBody>
          <TableFooter>
            <TableRow>
              <TableCell className="font-medium">Total</TableCell>
              <TableCell className="text-right font-mono">{fmt(zone.total_oi)}</TableCell>
              <TableCell className={`text-right font-mono ${colorDir(zone.total_oi_change)}`}>{fmtSigned(zone.total_oi_change)}</TableCell>
              <TableCell className={`text-right font-mono font-medium ${colorDir(zone.total_force)}`}>{fmtSigned(zone.total_force, 1)}</TableCell>
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
