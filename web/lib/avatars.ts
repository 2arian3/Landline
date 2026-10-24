"use client";

import { useEffect, useState } from "react";
import { api } from "./live";

/** One call's clip, as GET /api/sessions/{id}/avatars lists it. No video bytes. */
export type AvatarClip = {
  call_id: string;
  role: string;
  address: string | null;
  status: string;
};

const WAITING = new Set(["queued", "rendering"]);
const POLL_MS = 2000;

/**
 * The session's clips, once the renter's call has ended (`calling`, `done` or `closed`). While a call
 * is still going out, or a clip is still being made, ask again every couple of seconds: a listing clip
 * appears only after that call ends. A failed fetch keeps the last list and tries again.
 */
export function useAvatarClips(sessionId: string | null, phase: string | null): AvatarClip[] {
  const [clips, setClips] = useState<AvatarClip[]>([]);

  useEffect(() => {
    const active = phase === "calling" || phase === "done" || phase === "closed";
    if (!sessionId || !active) {
      setClips([]);
      return;
    }
    const left = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    let empty = 0;

    const pull = async () => {
      try {
        const res = await fetch(api(`/api/sessions/${encodeURIComponent(sessionId)}/avatars`), {
          cache: "no-store",
          signal: left.signal,
        });
        if (!res.ok || left.signal.aborted) return;
        const body = (await res.json()) as AvatarClip[];
        if (left.signal.aborted) return;
        setClips(body);
        const waiting = body.some((clip) => WAITING.has(clip.status));
        if (body.length === 0) empty += 1;
        // During the calls, another clip can still appear. Once they're done, stop when each one has
        // settled, and after a short wait if none were stored.
        const more = phase === "calling" || waiting || (body.length === 0 && empty < 15);
        if (more) timer = setTimeout(pull, POLL_MS);
      } catch {
        if (!left.signal.aborted) timer = setTimeout(pull, POLL_MS);
      }
    };

    void pull();
    return () => {
      left.abort();
      clearTimeout(timer);
    };
  }, [sessionId, phase]);

  return clips;
}
