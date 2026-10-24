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
 *   - a new session id starts from nothing: versions count per session, and an
 *     answer for the session the page just left is dropped
 * With no snapshot at all after UNREACHABLE_MS, `unreachable` says so, and the
 * polling carries on.
 */
const STALE_MS = 6500;
const POLL_MS = 1500;
const REOPEN_MS = 30_000;
const UNREACHABLE_MS = 9000;

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
  const [unreachable, setUnreachable] = useState(false);
  const current = useRef<SessionView | null>(null);
  const following = useRef(sessionId);
  const inFlight = useRef(false);
  const lastOk = useRef(Date.now());
  following.current = sessionId;

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
      if (data.session_id !== following.current) return;
      lastOk.current = Date.now();
      setStale(false);
      const cur = current.current?.session_id === data.session_id ? current.current : null;
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
    // Another session (judge mode's "Start again") starts from nothing.
    if (current.current && current.current.session_id !== sessionId) {
      current.current = null;
      setView(null);
    }
    setUnreachable(false);
    // Judge mode mounts the board before a session exists: nothing to follow yet.
    if (!sessionId) return;
    let source: EventSource | null = null;
    let errors = 0;
    let heardAt = Date.now(); // the stream's last word: opened, a version or a ping
    let openedAt = 0;
    let polledAt = 0;
    const since = (lastOk.current = Date.now());
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
      setUnreachable(!current.current && now - since > UNREACHABLE_MS);
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

  // Until the new session's first snapshot, the old one's board isn't shown under its id.
  return { view: view?.session_id === sessionId ? view : null, stale, unreachable, refresh };
}

export function useCatalogue() {
  const [points, setPoints] = useState<CataloguePoint[]>([]);
  useEffect(() => {
    let stopped = false;
    let wait = 1000;
    let timer: ReturnType<typeof setTimeout> | undefined;
    // An API that's down at first comes back with the board, so the dots keep trying too (2 s, 4 s, then
    // every 8 s). A retry skips the browser cache: force-cache would hand back the failed answer forever.
    const load = () =>
      fetch(api("/api/catalogue"), { cache: wait > 1000 ? "reload" : "force-cache" })
        .then((r) => (r.ok ? (r.json() as Promise<CataloguePoint[]>) : Promise.reject(r.status)))
        .then((p) => !stopped && setPoints(p))
        .catch(() => {
          if (!stopped) timer = setTimeout(load, (wait = Math.min(wait * 2, 8000)));
        });
    void load();
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
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
