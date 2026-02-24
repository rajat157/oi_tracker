"use client";

import { useCallback, useEffect, useState } from "react";
import { Header } from "@/components/shared/Header";
import { TradeTable } from "@/components/trades/TradeTable";
import { TradeStatsPanel } from "@/components/trades/TradeStats";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import type { TradeBase, TradeStats, StrategyName } from "@/lib/types";

const STRATEGIES: { key: StrategyName; label: string }[] = [
  { key: "iron_pulse", label: "Iron Pulse" },
  { key: "selling", label: "Selling" },
  { key: "dessert", label: "Dessert" },
  { key: "momentum", label: "Momentum" },
];

const PAGE_SIZE = 20;

export default function TradesPage() {
  const [activeTab, setActiveTab] = useState<StrategyName>("iron_pulse");
  const [trades, setTrades] = useState<TradeBase[]>([]);
  const [stats, setStats] = useState<TradeStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [page, setPage] = useState(0);
  const [totalCount, setTotalCount] = useState(0);

  const fetchData = useCallback(async () => {
    setLoading(true);
    try {
      const [tradesRes, statsRes] = await Promise.all([
        api.getTrades(activeTab, {
          limit: String(PAGE_SIZE),
          offset: String(page * PAGE_SIZE),
        }),
        api.getTradeStats(activeTab),
      ]);
      setTrades(tradesRes.data);
      setTotalCount(tradesRes.count);
      setStats(statsRes.stats);
    } catch {
      setTrades([]);
      setStats(null);
    } finally {
      setLoading(false);
    }
  }, [activeTab, page]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  const totalPages = Math.ceil(totalCount / PAGE_SIZE);

  return (
    <div className="min-h-screen bg-background">
      <Header />
      <main className="container mx-auto p-6 space-y-6">
        <h2 className="text-2xl font-semibold">Trade History</h2>

        {/* Strategy tabs */}
        <div className="flex gap-2">
          {STRATEGIES.map((s) => (
            <Button
              key={s.key}
              variant={activeTab === s.key ? "default" : "outline"}
              size="sm"
              onClick={() => {
                setActiveTab(s.key);
                setPage(0);
              }}
            >
              {s.label}
            </Button>
          ))}
        </div>

        {/* Stats */}
        <TradeStatsPanel stats={stats} loading={loading} />

        {/* Trade table */}
        <TradeTable trades={trades} loading={loading} />

        {/* Pagination */}
        {totalPages > 1 && (
          <div className="flex items-center justify-center gap-4">
            <Button
              variant="outline"
              size="sm"
              disabled={page === 0}
              onClick={() => setPage((p) => p - 1)}
            >
              Prev
            </Button>
            <span className="text-sm text-muted-foreground">
              Page {page + 1} of {totalPages}
            </span>
            <Button
              variant="outline"
              size="sm"
              disabled={page >= totalPages - 1}
              onClick={() => setPage((p) => p + 1)}
            >
              Next
            </Button>
          </div>
        )}
      </main>
    </div>
  );
}
