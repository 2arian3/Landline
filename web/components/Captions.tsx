"use client";

import { useEffect, useRef, useState } from "react";
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
  const [turns, setTurns] = useState<Turn[]>([]);
  const last = useRef(-1);
  const onLine = ON_THE_LINE.includes(call.state);
  const over = call.state === "ended" || call.state === "voicemail";

  useEffect(() => {
    if (!onLine && !over) return;
    const ctl = new AbortController();
    let busy = false;
    let unknown = false; // a 404: the API doesn't know this call (it restarted)
    const pull = async () => {
      if (busy || unknown) return;
      busy = true;
      try {
        const res = await fetch(api(`/api/calls/${encodeURIComponent(call.call_id)}/turns?after=${last.current}`), {
          cache: "no-store",
          signal: ctl.signal,
        });
        unknown = res.status === 404;
        if (!res.ok) return;
        const fresh = ((await res.json()) as Turn[]).filter((t) => t.idx > last.current);
        if (ctl.signal.aborted || !fresh.length) return;
        last.current = fresh[fresh.length - 1].idx;
        setTurns((shown) => [...shown, ...fresh]);
      } catch {
        // keep what's shown; the next ask tries again
      } finally {
        busy = false;
      }
    };
    void pull();
    // Once it's over, three more asks: the last lines can be written just after the call ends, and an ask
    // that failed gets another go.
    let asks = 0;
    const tick = setInterval(() => {
      if (over && ++asks > 3) return clearInterval(tick);
      void pull();
    }, POLL_MS);
    return () => {
      clearInterval(tick);
      ctl.abort();
    };
  }, [call.call_id, onLine, over]);

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
