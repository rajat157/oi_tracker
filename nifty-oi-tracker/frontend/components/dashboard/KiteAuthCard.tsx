"use client";

import { useCallback, useEffect, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";

export function KiteAuthCard() {
  const [authenticated, setAuthenticated] = useState<boolean | null>(null);
  const [token, setToken] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const checkStatus = useCallback(async () => {
    try {
      const res = await api.getKiteStatus();
      setAuthenticated(res.authenticated);
    } catch {
      setAuthenticated(false);
    }
  }, []);

  useEffect(() => {
    checkStatus();
    const interval = setInterval(checkStatus, 5 * 60 * 1000);
    return () => clearInterval(interval);
  }, [checkStatus]);

  const handleLogin = async () => {
    try {
      const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api/v1"}/kite/login`);
      const data = await res.json();
      if (data.login_url) {
        window.open(data.login_url, "_blank");
      }
    } catch { /* ignore */ }
  };

  const handleSaveToken = async () => {
    if (!token.trim()) return;
    setSaving(true);
    setError("");
    try {
      const res = await fetch(
        `${process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api/v1"}/kite/token`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ access_token: token.trim() }),
        }
      );
      const data = await res.json();
      if (res.ok && data.success) {
        setAuthenticated(true);
        setToken("");
      } else {
        setError(data.detail || "Failed to save token");
      }
    } catch {
      setError("Network error — is the API running?");
    }
    setSaving(false);
  };

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium text-muted-foreground flex items-center gap-2">
          Kite Auth
          <span className={`inline-block w-2 h-2 rounded-full ${authenticated ? "bg-green-500" : "bg-red-500"}`} />
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-2">
        <p className="text-sm">
          {authenticated === null ? "Checking..." : authenticated ? "Connected" : "Not connected"}
        </p>
        {!authenticated && (
          <>
            <Button size="sm" variant="outline" onClick={handleLogin}>
              Kite Login
            </Button>
            <div className="flex gap-2 items-center">
              <input
                type="text"
                placeholder="Paste access token..."
                value={token}
                onChange={(e) => setToken(e.target.value)}
                className="flex-1 px-2 py-1 text-xs rounded border border-input bg-background font-mono"
              />
              <Button size="xs" variant="outline" onClick={handleSaveToken} disabled={saving || !token.trim()}>
                {saving ? "..." : "Save"}
              </Button>
            </div>
            {error && <p className="text-xs text-red-500">{error}</p>}
          </>
        )}
      </CardContent>
    </Card>
  );
}
