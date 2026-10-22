"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { CataloguePoint, SessionView } from "./types";

/**
 * The live session: server-sent events say *that* something changed (a
 * version number); the snapshot says *what*. If the stream keeps failing we
 * fall back to polling. Safeguards carried over from Realest's poller:
 *   - one request in flight at a time, so a slow network can't stack them
 *   - a response older than what's on screen is dropped
 *   - a successful but empty answer never blanks a live board unless its
 *     version is newer (a restart or an expired session answers empty)
 *   - errors keep the last good state; the page admits "Reconnecting…" only
 *     after STALE_MS without any word from the server
 */
const STALE_MS = 6500;
const POLL_MS = 1500;

export function useLiveSession(sessionId: string) {
  const [view, setView] = useState<SessionView | null>(null);
  const [stale, setStale] = useState(false);
  const current = useRef<SessionView | null>(null);
  const inFlight = useRef(false);
  const lastOk = useRef(Date.now());

  const refresh = useCallback(async () => {
    if (inFlight.current) return;
    inFlight.current = true;
    try {
      const res = await fetch(`/api/sessions/${encodeURIComponent(sessionId)}`, {
        cache: "no-store",
        signal: AbortSignal.timeout(4000),
      });
      if (!res.ok) return;
      const data = (await res.json()) as SessionView;
      lastOk.current = Date.now();
      setStale(false);
      const cur = current.current;
      if (cur && data.version < cur.version) return;
      if (cur && data.board.length === 0 && cur.board.length > 0 && data.version <= cur.version) return;
      if (cur && data.version === cur.version && JSON.stringify(data) === JSON.stringify(cur)) return;
      current.current = data;
      setView(data);
    } catch {
      // keep the last good state
    } finally {
      inFlight.current = false;
    }
  }, [sessionId]);

  useEffect(() => {
    let alive = true;
    let poll: ReturnType<typeof setInterval> | null = null;
    let errors = 0;
    void refresh();

    const source = new EventSource(`/api/sessions/${encodeURIComponent(sessionId)}/stream`);
    // Any word from the server, a version or a ping, means the line is up.
    const markAlive = () => {
      lastOk.current = Date.now();
      setStale(false);
      errors = 0;
    };
    source.onopen = markAlive;
    source.addEventListener("ping", markAlive);
    source.onmessage = (ev) => {
      markAlive();
      try {
        const { version } = JSON.parse(ev.data) as { version: number };
        if (!current.current || version > current.current.version) void refresh();
      } catch {
        void refresh();
      }
    };
    source.onerror = () => {
      errors += 1;
      if (errors >= 3 && !poll && alive) {
        source.close();
        poll = setInterval(() => void refresh(), POLL_MS);
      }
    };

    const watch = setInterval(() => setStale(Date.now() - lastOk.current > STALE_MS), 1000);
    const onVisible = () => {
      if (document.visibilityState === "visible") {
        lastOk.current = Date.now();
        void refresh();
      }
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      alive = false;
      source.close();
      if (poll) clearInterval(poll);
      clearInterval(watch);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [sessionId, refresh]);

  return { view, stale, refresh };
}

export function useCatalogue() {
  const [points, setPoints] = useState<CataloguePoint[]>([]);
  useEffect(() => {
    fetch("/api/catalogue", { cache: "force-cache" })
      .then((r) => (r.ok ? r.json() : []))
      .then(setPoints)
      .catch(() => setPoints([]));
  }, []);
  return points;
}

export async function postJSON<T>(path: string, body: unknown): Promise<T | null> {
  try {
    const res = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    return res.ok ? ((await res.json()) as T) : null;
  } catch {
    return null;
  }
}
