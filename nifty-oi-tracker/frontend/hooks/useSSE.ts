"use client";

import { useEffect, useRef, useCallback, useState } from "react";

interface UseSSEOptions {
  url: string;
  onMessage?: (event: string, data: unknown) => void;
  reconnectInterval?: number;
}

export function useSSE({ url, onMessage, reconnectInterval = 3000 }: UseSSEOptions) {
  const [connected, setConnected] = useState(false);
  const esRef = useRef<EventSource | null>(null);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout>>(undefined);

  const connect = useCallback(() => {
    if (esRef.current) {
      esRef.current.close();
    }

    const es = new EventSource(url);
    esRef.current = es;

    es.onopen = () => setConnected(true);

    es.onerror = () => {
      setConnected(false);
      es.close();
      reconnectTimer.current = setTimeout(connect, reconnectInterval);
    };

    // Listen for typed events
    for (const eventType of ["analysis_update", "trade_update", "market_status"]) {
      es.addEventListener(eventType, (e: MessageEvent) => {
        try {
          const data = JSON.parse(e.data);
          onMessage?.(eventType, data);
        } catch {
          // Ignore parse errors
        }
      });
    }
  }, [url, onMessage, reconnectInterval]);

  useEffect(() => {
    connect();
    return () => {
      esRef.current?.close();
      if (reconnectTimer.current) clearTimeout(reconnectTimer.current);
    };
  }, [connect]);

  return { connected };
}
