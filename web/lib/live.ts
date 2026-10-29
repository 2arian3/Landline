"use client";

import { useEffect, useRef, useState } from "react";
import { announced, owed } from "./owed.ts";
import { sessionPath } from "./sessionPath";
import type { CataloguePoint, SessionView } from "./types";

/**
 * The live session: server-sent events say *that* something changed (a
 * version number); the snapshot says *what*. When the stream fails, or goes
 * quiet, we poll instead: quiet too, because a proxy that buffers the stream
 * (a Cloudflare tunnel did) stalls it without an error. While it's down the
 * snapshot is fetched every POLL_MS. A stream the browser closed (a refusal) is
 * opened again after RETRY_MS, backing off to REOPEN_MS; a quiet one gets a
 * fresh connection every REOPEN_MS. Any word from it (open, version or ping)
 * stops the polling. Safeguards
 * carried over from Realest's poller:
 *   - one request in flight at a time, so a slow network can't stack them
 *   - a response older than what's on screen is dropped
 *   - a successful but empty answer never blanks a live board unless its
 *     version is newer (a restart or an expired session answers empty)
 *   - errors keep the last good state; the page admits "Reconnecting…" only
 *     after STALE_MS without any word from the server
 *   - a new session id starts from nothing: versions count per session, and
 *     what's still in flight for the session the page left is cancelled
 * Until the first snapshot is in, it's asked for every POLL_MS even while the
 * stream is up. With no word from the server for UNREACHABLE_MS (and, before
 * the first snapshot, none of that either), `unreachable` says so, and the
 * asking carries on: the board in memory comes back with the next answer. A 404
 * with no board is an answer, not silence: `missing` says the link matches no
 * call (lib/pill.ts words the rest), and it's never called offline.
 */
const STALE_MS = 6500;
const POLL_MS = 1500;
const REOPEN_MS = 30_000;
const RETRY_MS = 2000;
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
  const [missing, setMissing] = useState(false);
  const current = useRef<SessionView | null>(null);
  const lastOk = useRef(Date.now());

  useEffect(() => {
    // Another session (judge mode's "Start again") starts from nothing: versions count per session.
    current.current = null;
    setUnreachable(false);
    setMissing(false);
    // Judge mode mounts the board before a session exists: nothing to follow yet.
    if (!sessionId) return;
    // Everything below belongs to this session. Leaving it cancels what's still in flight, so an answer for
    // the session the page left can't land on the next one, or hold up its first request.
    const left = new AbortController();
    let inFlight = false;
    let wanted = 0; // the newest version the stream announced (lib/owed.ts)
    let chased = 0; // asks since then for a version still owed; a few, then the tick's polling is the net
    let source: EventSource | null = null;
    let errors = 0;
    let retryIn = RETRY_MS; // until a closed stream is opened again; doubles up to REOPEN_MS
    let retry: ReturnType<typeof setTimeout> | undefined;
    let heardAt = Date.now(); // the stream's last word: opened, a version or a ping (0 once it fails)
    let openedAt = 0;
    const since = (lastOk.current = Date.now());
    let polledAt = since;

    const refresh = async () => {
      if (inFlight) return;
      inFlight = true;
      try {
        const res = await fetch(api(sessionPath(sessionId, window.location.search)), {
          cache: "no-store",
          signal: AbortSignal.any([left.signal, AbortSignal.timeout(4000)]),
        });
        if (res.status === 404 && !current.current && !left.signal.aborted) {
          // The server answered: this id matches no call. Not "Reconnecting", not "Offline".
          lastOk.current = Date.now();
          setStale(false);
          setMissing(true);
          return;
        }
        if (!res.ok) return;
        const data = (await res.json()) as SessionView;
        if (left.signal.aborted) return;
        lastOk.current = Date.now();
        setStale(false);
        setMissing(false);
        const cur = current.current;
        if (cur && data.version < cur.version) return;
        if (cur && data.board.length === 0 && cur.board.length > 0 && data.version <= cur.version) return;
        if (cur && data.version === cur.version && JSON.stringify(data) === JSON.stringify(cur)) return;
        current.current = data;
        setView(data);
      } catch {
        // keep the last good state
      } finally {
        inFlight = false;
        // A newer version announced while this fetch was out, and it brought an older one: ask again, or
        // the board sits on it for good (the stream's pings stop the tick from polling). Spaced out, so a
        // server that keeps failing (or restarted, counting from 1 again) isn't asked in a loop.
        if (!left.signal.aborted && owed(wanted, current.current?.version) && chased++ < 8) {
          setTimeout(() => void refresh(), 250);
        }
      }
    };
    void refresh();

    // Any word on the stream means it's up again; the next tick stops polling.
    const heard = () => {
      heardAt = lastOk.current = Date.now();
      setStale(false);
      errors = 0;
      retryIn = RETRY_MS;
    };
    const open = () => {
      clearTimeout(retry);
      source?.close();
      openedAt = Date.now();
      const s = new EventSource(api(`/api/sessions/${encodeURIComponent(sessionId)}/stream`));
      s.onopen = heard;
      s.addEventListener("ping", heard);
      s.onmessage = (ev) => {
        heard();
        try {
          const { version } = JSON.parse(ev.data) as { version: number };
          if (announced(wanted, version) > wanted) chased = 0;
          wanted = announced(wanted, version);
          if (!current.current || version > current.current.version) void refresh();
        } catch {
          void refresh();
        }
      };
      // An error makes the stream quiet at once, so polling holds the board from the next tick. A dropped
      // connection the browser retries by itself, until three errors in a row; a refused one (a 502 from a
      // proxy) it closes for good, so it's opened again here, after RETRY_MS, doubling up to REOPEN_MS.
      s.onerror = () => {
        heardAt = 0;
        errors += 1;
        if (errors >= 3) s.close();
        if (s.readyState === EventSource.CLOSED && source === s) {
          retry = setTimeout(open, retryIn);
          retryIn = Math.min(retryIn * 2, REOPEN_MS);
        }
      };
      source = s;
    };
    open();

    const tick = setInterval(() => {
      const now = Date.now();
      const quiet = now - heardAt > STALE_MS;
      // Poll while the stream is down or quiet, and until the first snapshot is in: that one can fail while
      // the stream is fine, and then nothing else would ask again.
      if ((quiet || !current.current) && now - polledAt >= POLL_MS) {
        polledAt = now;
        void refresh();
      }
      if (quiet && now - openedAt >= REOPEN_MS) open();
      setStale(now - lastOk.current > STALE_MS);
      // No snapshot yet, and no word from the server either: a stream that's up means it's there. With a board,
      // Reconnecting turns to Offline once nothing at all has been heard for UNREACHABLE_MS (the Status board).
      setUnreachable(
        current.current
          ? now - lastOk.current > UNREACHABLE_MS
          : now - since > UNREACHABLE_MS && now - lastOk.current > STALE_MS,
      );
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
      left.abort();
      clearTimeout(retry);
      source?.close();
      clearInterval(tick);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [sessionId]);

  // Until the new session's first snapshot, the old one's board isn't shown under its id.
  return { view: view?.session_id === sessionId ? view : null, stale, unreachable, missing };
}

export function useCatalogue() {
  const [points, setPoints] = useState<CataloguePoint[]>([]);
  useEffect(() => {
    let stopped = false;
    let wait = 1000;
    let timer: ReturnType<typeof setTimeout> | undefined;
    // An API that's down at first comes back with the board, so the dots keep trying too (2 s, 4 s, then
    // every 8 s). The browser's cache follows the API's headers, so a listing refresh shows; a retry skips
    // it, so a failed answer can't be handed back.
    const load = (retry: boolean) =>
      fetch(api("/api/catalogue"), { cache: retry ? "reload" : "default" })
        .then((r) => (r.ok ? (r.json() as Promise<CataloguePoint[]>) : Promise.reject(r.status)))
        .then((p) => !stopped && setPoints(p))
        .catch(() => {
          if (!stopped) timer = setTimeout(() => load(true), (wait = Math.min(wait * 2, 8000)));
        });
    void load(false);
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, []);
  return points;
}

/** A tap's answer: the server's new snapshot, or why it wasn't saved, so the page can put it back and say so. */
export type Tapped<T> = { ok: true; value: T } | { ok: false; why: "refused" | "unreachable" };

export async function tap<T>(path: string, body: unknown): Promise<Tapped<T>> {
  try {
    const res = await fetch(api(path), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(8000),
    });
    return res.ok ? { ok: true, value: (await res.json()) as T } : { ok: false, why: "refused" };
  } catch {
    return { ok: false, why: "unreachable" };
  }
}

export async function postJSON<T>(path: string, body: unknown): Promise<T | null> {
  const got = await tap<T>(path, body);
  return got.ok ? got.value : null;
}
