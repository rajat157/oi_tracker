"use client";

import Link from "next/link";
import { Button } from "@/components/ui/button";
import { KiteDot } from "./KiteDot";
import { useDashboardStore } from "@/stores/dashboard-store";
import { api } from "@/lib/api";

function StatusIndicator() {
  const connected = useDashboardStore((s) => s.connected);
  const marketStatus = useDashboardStore((s) => s.marketStatus);

  if (!connected) {
    return (
      <div className="flex items-center gap-1.5">
        <span className="inline-block w-2 h-2 rounded-full bg-red-500" />
        <span className="text-xs text-red-400">Disconnected</span>
      </div>
    );
  }

  if (!marketStatus?.is_open) {
    return (
      <div className="flex items-center gap-1.5">
        <span className="inline-block w-2 h-2 rounded-full bg-orange-500" />
        <span className="text-xs text-orange-400">Market Closed</span>
      </div>
    );
  }

  return (
    <div className="flex items-center gap-1.5">
      <span className="inline-block w-2 h-2 rounded-full bg-green-500 animate-status-pulse" />
      <span className="text-xs text-green-400">Live</span>
    </div>
  );
}

export function Header() {
  const analysis = useDashboardStore((s) => s.analysis);

  const lastUpdate = analysis?.timestamp
    ? new Date(analysis.timestamp).toLocaleTimeString("en-IN", {
        hour: "2-digit",
        minute: "2-digit",
      })
    : null;

  const handleRefresh = () => {
    api.triggerRefresh().catch(() => {});
  };

  return (
    <header className="border-b bg-background px-6 py-3 flex items-center justify-between">
      <div className="flex items-center gap-6">
        <h1 className="text-lg font-semibold">NIFTY OI Tracker</h1>
        <nav className="flex gap-4">
          <Link href="/" className="text-sm text-muted-foreground hover:text-foreground">
            Dashboard
          </Link>
          <Link href="/trades" className="text-sm text-muted-foreground hover:text-foreground">
            Trades
          </Link>
          <Link href="/logs" className="text-sm text-muted-foreground hover:text-foreground">
            Logs
          </Link>
        </nav>
      </div>
      <div className="flex items-center gap-4">
        <StatusIndicator />
        <KiteDot />
        {lastUpdate && (
          <span className="text-xs text-muted-foreground">Last: {lastUpdate}</span>
        )}
        <Button variant="ghost" size="sm" onClick={handleRefresh} title="Trigger refresh">
          ↻
        </Button>
      </div>
    </header>
  );
}
