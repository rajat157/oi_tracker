"use client";

import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import type { LogEntry } from "@/lib/types";

interface LogViewerProps {
  logs: LogEntry[];
  loading?: boolean;
}

const levelColors: Record<string, string> = {
  DEBUG: "bg-gray-500/10 text-gray-500",
  INFO: "bg-blue-500/10 text-blue-600",
  WARNING: "bg-yellow-500/10 text-yellow-600",
  ERROR: "bg-red-500/10 text-red-600",
};

export function LogViewer({ logs, loading }: LogViewerProps) {
  if (loading) {
    return <p className="text-muted-foreground py-8 text-center">Loading logs...</p>;
  }

  if (logs.length === 0) {
    return <p className="text-muted-foreground py-8 text-center">No logs found</p>;
  }

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead className="w-[160px]">Time</TableHead>
          <TableHead className="w-[80px]">Level</TableHead>
          <TableHead className="w-[120px]">Component</TableHead>
          <TableHead>Message</TableHead>
          <TableHead className="w-[200px]">Details</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {logs.map((log) => (
          <TableRow key={log.id}>
            <TableCell className="font-mono text-xs">
              {new Date(log.timestamp).toLocaleString("en-IN", {
                month: "short",
                day: "numeric",
                hour: "2-digit",
                minute: "2-digit",
                second: "2-digit",
              })}
            </TableCell>
            <TableCell>
              <Badge className={levelColors[log.level] || ""} variant="outline">
                {log.level}
              </Badge>
            </TableCell>
            <TableCell>
              <Badge variant="secondary">{log.component}</Badge>
            </TableCell>
            <TableCell className="text-sm">{log.message}</TableCell>
            <TableCell className="text-xs text-muted-foreground max-w-[200px] truncate">
              {log.details ? JSON.stringify(log.details) : "-"}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
