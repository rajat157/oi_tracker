"use client";

import Link from "next/link";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useDashboardStore } from "@/stores/dashboard-store";

export function Header() {
  const connected = useDashboardStore((s) => s.connected);

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
      <div className="flex items-center gap-3">
        <Badge variant={connected ? "default" : "destructive"}>
          {connected ? "Live" : "Disconnected"}
        </Badge>
      </div>
    </header>
  );
}
