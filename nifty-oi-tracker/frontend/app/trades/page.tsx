"use client";

import { Header } from "@/components/shared/Header";

export default function TradesPage() {
  return (
    <div className="min-h-screen bg-background">
      <Header />
      <main className="container mx-auto p-6">
        <h2 className="text-2xl font-semibold mb-4">Trade History</h2>
        <p className="text-muted-foreground">Trade table and filters will be implemented in Phase 5.</p>
      </main>
    </div>
  );
}
