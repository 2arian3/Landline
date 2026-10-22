"use client";

import { heardLine } from "@/lib/format";
import type { SessionView } from "@/lib/types";

export function Brand({ stale, connected }: { stale: boolean; connected: boolean }) {
  const label = !connected ? "Connecting…" : stale ? "Reconnecting…" : "Live";
  const color = connected && !stale ? "var(--color-real)" : "var(--color-ink-3)";
  return (
    <div className="flex items-center justify-between gap-3">
      <div className="min-w-0">
        <p className="text-fact font-strong tracking-[-0.01em]">Landline</p>
      </div>
      <p className="flex items-center gap-1.5 text-meta text-ink-2" aria-live="polite">
        <span aria-hidden className="h-2 w-2 rounded-full" style={{ background: color }} />
        {label}
      </p>
    </div>
  );
}

/** What Landline heard and what it last said: the renter's half of the call, on screen. */
export function Heard({ view }: { view: SessionView | null }) {
  if (!view) {
    return (
      <div className="space-y-2" aria-hidden>
        <div className="h-4 w-2/3 rounded bg-press" />
        <div className="h-4 w-1/2 rounded bg-press" />
      </div>
    );
  }
  const heard = heardLine(view.preferences);
  const shown = view.board.length;
  return (
    <div className="space-y-3">
      {heard.length > 0 && (
        <div>
          <p className="text-meta text-ink-3">What I heard</p>
          <p className="text-support text-ink">{capitalize(heard.join(" · "))}</p>
        </div>
      )}
      {view.agent_says && (
        <blockquote className="m-0 border-0 p-0">
          <p className="text-meta text-ink-3">Landline said</p>
          <p className="text-fact">“{view.agent_says}”</p>
        </blockquote>
      )}
      <p className="text-meta text-ink-3 tnum">
        {view.matches.length.toLocaleString("en-CA")} {view.matches.length === 1 ? "place fits" : "places fit"}
        {shown > 0 && ` · the best ${shown} are numbered on the map`}
      </p>
    </div>
  );
}

const capitalize = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);
