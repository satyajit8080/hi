"use client";

import { useEffect, useRef, useState } from "react";
import type { Health, MarketSnapshot } from "./types";

const WS_URL = process.env.NEXT_PUBLIC_WS_URL ?? "ws://localhost:8000/ws/market";

export interface LiveState {
  markets: Record<string, MarketSnapshot>;
  health: Health | null;
  connected: boolean;
  /** Distinguishes "still connecting" from "connected but no data". */
  everConnected: boolean;
}

/**
 * Market socket with backoff reconnect.
 *
 * Deliberately keeps the last good snapshot on disconnect rather than blanking
 * the screen — a terminal that empties itself on a dropped socket is worse than
 * one showing stale prices clearly marked as stale.
 */
export function useLive(): LiveState {
  const [state, setState] = useState<LiveState>({
    markets: {}, health: null, connected: false, everConnected: false,
  });
  const socket = useRef<WebSocket | null>(null);
  const backoff = useRef(1000);
  const closed = useRef(false);

  useEffect(() => {
    closed.current = false;

    const connect = () => {
      if (closed.current) return;
      let ws: WebSocket;
      try {
        ws = new WebSocket(WS_URL);
      } catch {
        setTimeout(connect, backoff.current);
        backoff.current = Math.min(backoff.current * 2, 20000);
        return;
      }
      socket.current = ws;

      ws.onopen = () => {
        backoff.current = 1000;
        setState((s) => ({ ...s, connected: true, everConnected: true }));
      };

      ws.onmessage = (event) => {
        try {
          const payload = JSON.parse(event.data);
          if (payload.type === "tick") {
            setState((s) => ({
              ...s,
              markets: { ...s.markets, ...payload.markets },
              health: payload.health ?? s.health,
              connected: true,
              everConnected: true,
            }));
          }
        } catch {
          /* a malformed frame should never take the terminal down */
        }
      };

      ws.onclose = () => {
        setState((s) => ({ ...s, connected: false }));
        if (closed.current) return;
        setTimeout(connect, backoff.current);
        backoff.current = Math.min(backoff.current * 2, 20000);
      };

      ws.onerror = () => ws.close();
    };

    connect();
    return () => {
      closed.current = true;
      socket.current?.close();
    };
  }, []);

  return state;
}

/** Polling fallback for pages that do not need sub-second updates. */
export function usePoll<T>(fn: () => Promise<T>, intervalMs = 15000) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let alive = true;
    const run = async () => {
      try {
        const result = await fn();
        if (alive) { setData(result); setError(null); }
      } catch (e) {
        if (alive) setError(e as Error);
      } finally {
        if (alive) setLoading(false);
      }
    };
    run();
    const id = setInterval(run, intervalMs);
    return () => { alive = false; clearInterval(id); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [intervalMs]);

  return { data, error, loading };
}
