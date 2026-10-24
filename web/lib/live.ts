"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { CataloguePoint, SessionView } from "./types";

/**
 * The live session: server-sent events say *that* something changed (a
 * version number); the snapshot says *what*. When the stream goes quiet we
 * poll instead: quiet, not failing, because a proxy that buffers the stream
 * (a Cloudflare tunnel did) stalls it without an error. While it's quiet the
 * snapshot is fetched every POLL_MS and the stream gets a fresh connection
 * every REOPEN_MS; its first version or ping stops the polling. Safeguards
 * carried over from Realest's poller:
 *   - one request in flight at a time, so a slow network can't stack them
 *   - a response older than what's on screen is dropped
 *   - a successful but empty answer never blanks a live board unless its
 *     version is newer (a restart or an expired session answers empty)
 *   - errors keep the last good state; the page admits "Reconnecting…" only
 *     after STALE_MS without any word from the server
 */
const STALE_MS = 6500;
const POLL_MS = 1500;
const REOPEN_MS = 30_000;

/**
 * Where the API lives. Empty means this origin's /api proxy (next.config.ts),
 * which needs no CORS. Set NEXT_PUBLIC_API_URL to call the API directly, for
 * example to keep the event stream off Vercel's proxy; that origin must then be
 * in the API's CORS_ORIGINS.
 */
const API_BASE = (process.env.NEXT_PUBLIC_API_URL ?? "").replace(/\/+$/, "");

export function api(path: string): string {
  return `${API_BASE}${path}`;
}

export function useLiveSession(sessionId: string | null) {
  const [view, setView] = useState<SessionView | null>(null);
  const [stale, setStale] = useState(false);
  const current = useRef<SessionView | null>(null);
  const inFlight = useRef(false);
  const lastOk = useRef(Date.now());

  const refresh = useCallback(async () => {
    if (!sessionId || inFlight.current) return;
    inFlight.current = true;
    try {
      const res = await fetch(api(`/api/sessions/${encodeURIComponent(sessionId)}`), {
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
    // Judge mode mounts the board before a session exists: nothing to follow yet.
    if (!sessionId) return;
    let source: EventSource | null = null;
    let errors = 0;
    let heardAt = Date.now(); // the stream's last word: opened, a version or a ping
    let openedAt = 0;
    let polledAt = 0;
    void refresh();

    // Any word on the stream means it's up again; the next tick stops polling.
    const heard = () => {
      heardAt = lastOk.current = Date.now();
      setStale(false);
      errors = 0;
    };
    const open = () => {
      source?.close();
      openedAt = Date.now();
      const s = new EventSource(api(`/api/sessions/${encodeURIComponent(sessionId)}/stream`));
      s.onopen = heard;
      s.addEventListener("ping", heard);
      s.onmessage = (ev) => {
        heard();
        try {
          const { version } = JSON.parse(ev.data) as { version: number };
          if (!current.current || version > current.current.version) void refresh();
        } catch {
          void refresh();
        }
      };
      // The browser retries a failed stream on its own; three failures in a row and it waits for the
      // next reopen, with polling holding the board meanwhile.
      s.onerror = () => {
        errors += 1;
        if (errors >= 3) s.close();
      };
      source = s;
    };
    open();

    const tick = setInterval(() => {
      const now = Date.now();
      if (now - heardAt > STALE_MS) {
        if (now - polledAt >= POLL_MS) {
          polledAt = now;
          void refresh();
        }
        if (now - openedAt >= REOPEN_MS) open();
      }
      setStale(now - lastOk.current > STALE_MS);
    }, 500);
    const onVisible = () => {
      if (document.visibilityState !== "visible") return;
      lastOk.current = Date.now();
      void refresh();
      // A phone that slept can lose the stream without a word.
      if (Date.now() - heardAt > STALE_MS) open();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      source?.close();
      clearInterval(tick);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [sessionId, refresh]);

  return { view, stale, refresh };
}

export function useCatalogue() {
  const [points, setPoints] = useState<CataloguePoint[]>([]);
  useEffect(() => {
    fetch(api("/api/catalogue"), { cache: "force-cache" })
      .then((r) => (r.ok ? r.json() : []))
      .then(setPoints)
      .catch(() => setPoints([]));
  }, []);
  return points;
}

export async function postJSON<T>(path: string, body: unknown): Promise<T | null> {
  try {
    const res = await fetch(api(path), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    return res.ok ? ((await res.json()) as T) : null;
  } catch {
    return null;
  }
}
