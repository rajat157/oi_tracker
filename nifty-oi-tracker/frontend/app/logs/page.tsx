"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { RefreshCw } from "lucide-react";
import { Header } from "@/components/shared/Header";
import { LogViewer } from "@/components/logs/LogViewer";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import type { LogEntry } from "@/lib/types";

const LEVEL_OPTIONS = ["ALL", "DEBUG", "INFO", "WARNING", "ERROR"];
const HOUR_OPTIONS = [
  { value: "1", label: "1h" },
  { value: "6", label: "6h" },
  { value: "12", label: "12h" },
  { value: "24", label: "24h" },
  { value: "48", label: "48h" },
  { value: "168", label: "7d" },
];

export default function LogsPage() {
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [level, setLevel] = useState("ALL");
  const [component, setComponent] = useState("");
  const [hours, setHours] = useState("24");
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const fetchLogs = useCallback(async () => {
    setLoading(true);
    try {
      const params: Record<string, string> = { hours };
      if (level !== "ALL") params.level = level;
      if (component.trim()) params.component = component.trim();
      const res = await api.getLogs(params);
      setLogs(res.data);
    } catch {
      setLogs([]);
    } finally {
      setLoading(false);
    }
  }, [level, component, hours]);

  useEffect(() => {
    fetchLogs();
  }, [fetchLogs]);

  // Auto-refresh every 30s
  useEffect(() => {
    intervalRef.current = setInterval(fetchLogs, 30_000);
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, [fetchLogs]);

  return (
    <div className="min-h-screen bg-background">
      <Header />
      <main className="container mx-auto p-6 space-y-6">
        <div className="flex items-center justify-between">
          <h2 className="text-2xl font-semibold">System Logs</h2>
          <Button variant="outline" size="sm" onClick={fetchLogs}>
            <RefreshCw className="h-4 w-4 mr-1" />
            Refresh
          </Button>
        </div>

        {/* Filters */}
        <div className="flex flex-wrap gap-3 items-center">
          <select
            value={level}
            onChange={(e) => setLevel(e.target.value)}
            className="rounded-md border border-input bg-background px-3 py-1.5 text-sm"
          >
            {LEVEL_OPTIONS.map((l) => (
              <option key={l} value={l}>
                {l}
              </option>
            ))}
          </select>

          <input
            type="text"
            placeholder="Component..."
            value={component}
            onChange={(e) => setComponent(e.target.value)}
            className="rounded-md border border-input bg-background px-3 py-1.5 text-sm w-40"
          />

          <select
            value={hours}
            onChange={(e) => setHours(e.target.value)}
            className="rounded-md border border-input bg-background px-3 py-1.5 text-sm"
          >
            {HOUR_OPTIONS.map((h) => (
              <option key={h.value} value={h.value}>
                {h.label}
              </option>
            ))}
          </select>

          <span className="text-xs text-muted-foreground">
            {logs.length} entries &middot; auto-refresh 30s
          </span>
        </div>

        <LogViewer logs={logs} loading={loading} />
      </main>
    </div>
  );
}
