"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { useDashboardStore } from "@/stores/dashboard-store";

export function KiteDot() {
  const authenticated = useDashboardStore((s) => s.kiteAuthenticated);
  const [showLogin, setShowLogin] = useState(false);
  const [token, setToken] = useState("");
  const [saving, setSaving] = useState(false);

  const apiBase = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api/v1";

  const handleLogin = async () => {
    try {
      const res = await fetch(`${apiBase}/kite/login`);
      const data = await res.json();
      if (data.login_url) window.open(data.login_url, "_blank");
    } catch { /* ignore */ }
  };

  const handleSaveToken = async () => {
    if (!token.trim()) return;
    setSaving(true);
    try {
      const res = await fetch(
        `${apiBase}/kite/callback?request_token=${encodeURIComponent(token.trim())}`
      );
      const data = await res.json();
      if (data.success) {
        useDashboardStore.setState({ kiteAuthenticated: true });
        setToken("");
        setShowLogin(false);
      }
    } catch { /* ignore */ }
    setSaving(false);
  };

  return (
    <div className="relative">
      <button
        onClick={() => !authenticated && setShowLogin(!showLogin)}
        className="flex items-center gap-1.5 cursor-pointer"
        title={authenticated ? "Kite Connected" : "Kite Disconnected — Click to login"}
      >
        <span
          className={`inline-block w-2 h-2 rounded-full ${
            authenticated ? "bg-green-500" : "bg-red-500"
          }`}
        />
        <span className="text-xs text-muted-foreground">Kite</span>
      </button>
      {showLogin && !authenticated && (
        <div className="absolute right-0 top-full mt-2 z-50 bg-card border border-border rounded-lg p-3 shadow-lg space-y-2 w-64">
          <Button size="sm" variant="outline" onClick={handleLogin} className="w-full">
            Open Kite Login
          </Button>
          <div className="flex gap-2 items-center">
            <input
              type="text"
              placeholder="Paste request token..."
              value={token}
              onChange={(e) => setToken(e.target.value)}
              className="flex-1 px-2 py-1 text-xs rounded border border-input bg-background font-mono"
            />
            <Button
              size="sm"
              variant="outline"
              onClick={handleSaveToken}
              disabled={saving || !token.trim()}
            >
              {saving ? "..." : "Save"}
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
