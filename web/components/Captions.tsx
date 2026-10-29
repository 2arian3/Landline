"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/live";
import type { CallState, CallView } from "@/lib/types";

/** One line of a call, as GET /api/calls/{id}/turns sends it (U-05). Not in lib/types.ts until the contract has it. */
type Turn = { idx: number; speaker: "agent" | "landlord" | "renter"; text: string; lang: string | null; t_ms: number | null };

const SPEAKER: Record<Turn["speaker"], string> = { agent: "Landline", landlord: "Listing agent", renter: "You" };
const ON_THE_LINE: CallState[] = ["dialing", "ringing", "live"];
const POLL_MS = 1000;

/**
 * What's said on a place's call, line by line and labelled by speaker. While the call is on the line it
 * asks every second for the lines after the last one shown; once the call is over, a few more asks bring
 * the rest, and the lines stay, folded, under the facts. Under ?dev=1 the demo store's scripted calls
 * answer with their script until A-02 records real ones.
 */
export function Captions({ call }: { call: CallView }) {
  const { turns, onLine } = useTurns(call);

  if (!turns.length) return null;
  const lines = (
    <ol className="mt-2 space-y-1.5" aria-live={onLine ? "polite" : undefined}>
      {turns.map((t) => (
        <li key={t.idx} lang={t.lang ?? undefined} className="text-support text-ink">
          <span className="font-label text-ink-3">{SPEAKER[t.speaker]}</span> {t.text}
        </li>
      ))}
    </ol>
  );
  if (onLine) {
    return (
      <div className="mt-3">
        <p className="flex items-center gap-1.5 text-meta font-label text-live">
          <span aria-hidden className="live-dot h-1.5 w-1.5 rounded-full bg-live" />
          On the call now
        </p>
        {lines}
      </div>
    );
  }
  return (
    <details className="mt-3">
      <summary className="cursor-pointer list-none text-meta text-ink-3 underline decoration-dotted underline-offset-2">
        The call, line by line
      </summary>
      {lines}
    </details>
  );
}

const WHO: Record<Turn["speaker"], string> = { agent: "Landline", landlord: "Agent", renter: "You" };

/**
 * The card's live lines (Lucent): the call's last two lines while it's on, the older one quieter, read out
 * as they arrive. Nothing once the call is over: its lines stay in the detail.
 */
export function LiveLines({ call }: { call: CallView }) {
  const { turns, onLine } = useTurns(call);
  if (!onLine || !turns.length) return null;
  const shown = turns.slice(-2);
  return (
    // Seen, not read out: with several calls on at once a screen reader would hear every line of every call.
    // The open detail's captions announce its one call.
    <span className="lines" aria-hidden>
      {shown.map((t, i) => (
        <span
          key={t.idx}
          lang={t.lang ?? undefined}
          className={`line ${i < shown.length - 1 ? "is-old" : "is-new"}`}
        >
          <span className="who">{WHO[t.speaker]}</span>
          {t.text}
        </span>
      ))}
    </span>
  );
}

/**
 * One poller per call, shared by everything showing it (its card's live lines and the open detail), so a
 * call is asked for its lines once a second however many places show them. It asks while the call is on,
 * then a few more times once it's over (the last lines can be written just after it ends), and stops when
 * nothing shows the call any more. Only the lines a call already said are kept, nothing about the session.
 */
type Feed = {
  turns: Turn[];
  last: number;
  over: boolean;
  unknown: boolean; // a 404: the API doesn't know this call (it restarted)
  busy: boolean;
  asksAfter: number; // asks left once the call is over
  timer: ReturnType<typeof setInterval> | null;
  ctl: AbortController;
  subs: Set<() => void>;
};
const FEEDS = new Map<string, Feed>();

function feedFor(callId: string): Feed {
  let f = FEEDS.get(callId);
  if (!f) {
    f = { turns: [], last: -1, over: false, unknown: false, busy: false, asksAfter: 3, timer: null,
      ctl: new AbortController(), subs: new Set() };
    FEEDS.set(callId, f);
  }
  return f;
}

async function pull(callId: string, f: Feed) {
  if (f.busy || f.unknown) return;
  f.busy = true;
  try {
    const res = await fetch(api(`/api/calls/${encodeURIComponent(callId)}/turns?after=${f.last}`), {
      cache: "no-store",
      signal: f.ctl.signal,
    });
    f.unknown = res.status === 404;
    if (!res.ok) return;
    const fresh = ((await res.json()) as Turn[]).filter((t) => t.idx > f.last);
    if (f.ctl.signal.aborted || !fresh.length) return;
    f.last = fresh[fresh.length - 1].idx;
    f.turns = [...f.turns, ...fresh];
    f.subs.forEach((notify) => notify());
  } catch {
    // keep what's shown; the next ask tries again
  } finally {
    f.busy = false;
  }
}

function watch(callId: string, onLine: boolean, over: boolean, notify: () => void): () => void {
  const f = feedFor(callId);
  f.subs.add(notify);
  if (over) f.over = true;
  if ((onLine || over) && !f.timer) {
    void pull(callId, f);
    f.timer = setInterval(() => {
      if (f.over && f.asksAfter-- <= 0) {
        clearInterval(f.timer!);
        f.timer = null;
        return;
      }
      void pull(callId, f);
    }, POLL_MS);
  }
  return () => {
    f.subs.delete(notify);
    if (f.subs.size) return;
    if (f.timer) clearInterval(f.timer);
    f.ctl.abort();
    FEEDS.delete(callId);
  };
}

/** A call's lines from GET /api/calls/{id}/turns, from the call's shared poller. */
function useTurns(call: CallView): { turns: Turn[]; onLine: boolean } {
  const onLine = ON_THE_LINE.includes(call.state);
  const over = call.state === "ended" || call.state === "voicemail";
  const [, redraw] = useState(0);
  const id = call.call_id;
  useEffect(() => watch(id, onLine, over, () => redraw((n) => n + 1)), [id, onLine, over]);
  return { turns: FEEDS.get(id)?.turns ?? [], onLine };
}
