"use client";

import { useEffect, useRef, useCallback, useState } from "react";

interface UseSSEOptions {
  url: string;
  onMessage?: (event: string, data: unknown) => void;
  reconnectInterval?: number;
}

export function useSSE({ url, onMessage, reconnectInterval = 10000 }: UseSSEOptions) {
  const [connected, setConnected] = useState(false);
  const esRef = useRef<EventSource | null>(null);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout>>(undefined);
  const mountedRef = useRef(true);

  const connect = useCallback(() => {
    if (!mountedRef.current) return;
    if (esRef.current) {
      esRef.current.close();
    }

    const es = new EventSource(url);
    esRef.current = es;

    es.onopen = () => {
      if (mountedRef.current) setConnected(true);
    };

    es.onerror = () => {
      if (mountedRef.current) setConnected(false);
      es.close();
      if (mountedRef.current) {
        reconnectTimer.current = setTimeout(connect, reconnectInterval);
      }
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
    mountedRef.current = true;
    connect();
    return () => {
      mountedRef.current = false;
      esRef.current?.close();
      if (reconnectTimer.current) clearTimeout(reconnectTimer.current);
    };
  }, [connect]);

  return { connected };
}
