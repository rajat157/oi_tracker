"use client";

import { Header } from "@/components/shared/Header";

export default function LogsPage() {
  return (
    <div className="min-h-screen bg-background">
      <Header />
      <main className="container mx-auto p-6">
        <h2 className="text-2xl font-semibold mb-4">System Logs</h2>
        <p className="text-muted-foreground">Log viewer will be implemented in Phase 5.</p>
      </main>
    </div>
  );
}
