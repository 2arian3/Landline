"use client";

import Link from "next/link";
import { heardLine } from "@/lib/format";
import { unheardCounts } from "@/lib/outcome";
import { resultsByPlace } from "@/lib/results";
import type { Card, SessionView } from "@/lib/types";
import { splitWelcome } from "@/lib/welcome";

export function Brand({
  stale,
  connected,
  idle = false,
  line = null,
}: {
  stale: boolean;
  connected: boolean;
  idle?: boolean;
  line?: string | null;
}) {
  // Judge mode passes the voice line's state while it isn't live, so the header never says "Live" over a
  // dropped call. Otherwise it's the board's own connection.
  const label = line ?? (!connected ? "Connecting…" : stale ? "Reconnecting…" : "Live");
  const color = !line && connected && !stale ? "var(--color-real)" : "var(--color-ink-3)";
  return (
    <div className="flex items-center justify-between gap-3">
      <div className="flex min-w-0 items-baseline gap-2">
        <p className="text-fact font-strong tracking-[-0.01em]">Landline</p>
        <Link
          href="/me"
          className="shrink-0 text-meta text-ink-3 underline underline-offset-2 focus-visible:outline-2 focus-visible:outline-ink"
        >
          Your calls
        </Link>
      </div>
      {!idle && (
        <p className="flex items-center gap-1.5 text-meta text-ink-2" aria-live="polite">
          <span aria-hidden className="h-2 w-2 rounded-full" style={{ background: color }} />
          {label}
        </p>
      )}
    </div>
  );
}

/**
 * What the agents are doing right now, counted from the card statuses the
 * server sent. Nothing here is guessed: no "dialing", no ETAs. With no calls
 * yet it says how many places fit.
 */
export function Activity({ view, compact = false }: { view: SessionView | null; compact?: boolean }) {
  if (!view) return <div className="h-[18px] w-1/2 rounded bg-press" aria-hidden />;
  const n = count(view.board);
  const done: string[] = [];
  if (n.booked) done.push(`${n.booked} ${n.booked === 1 ? "viewing" : "viewings"} booked`);
  if (n.verified) done.push(`${n.verified} checked by phone`);
  if (n.suspect) done.push(`${n.suspect} flagged`);
  for (const [word, k] of unheardCounts(view.board, view.calls)) done.push(`${k} ${word}`);   // like the badges
  if (n.dead) done.push(`${n.dead} already rented`);
  const queued = view.calls.filter((c) => c.state === "queued").length;
  const rest = compact ? "min-w-0 truncate" : "";

  // The plan, before the renter hangs up, and after it until the calls are scheduled (A-06).
  if (view.plan.length && (view.phase === "open" || (view.phase === "calling" && !view.calls.length))) {
    return (
      <p className="flex items-baseline gap-x-1.5 text-meta" aria-live="polite">
        <span aria-hidden className="h-2 w-2 shrink-0 self-center rounded-full bg-ink" />
        <span className={`font-label text-ink ${rest}`}>
          Will call {view.plan.length} {view.plan.length === 1 ? "place" : "places"}
          {view.phase === "open" && " when you hang up"}
        </span>
      </p>
    );
  }

  if (n.calling || queued) {
    const now = [n.calling && `Calling ${n.calling} ${n.calling === 1 ? "place" : "places"}`, queued && `${queued} queued`]
      .filter(Boolean)
      .join(" · ");
    const label = (
      <span className={`shrink-0 whitespace-nowrap font-label ${n.calling ? "text-live" : "text-ink-2"}`}>{now}</span>
    );
    const dot = n.calling ? (
      <span aria-hidden className="live-dot h-2 w-2 shrink-0 self-center rounded-full bg-live" />
    ) : (
      <span aria-hidden className="h-2 w-2 shrink-0 self-center rounded-full bg-ink-3" />
    );
    if (compact) {
      return (
        <p className="flex items-baseline gap-x-1.5 text-meta" aria-live="polite">
          {dot}
          {label}
          {done.length > 0 && <span className={`text-ink-2 ${rest}`}>· {done.join(" · ")}</span>}
        </p>
      );
    }
    return (
      <div aria-live="polite">
        <p className="flex items-baseline gap-x-1.5 text-meta">
          {dot}
          {label}
        </p>
        {done.length > 0 && <p className="pl-3.5 text-meta text-ink-2">{capitalize(done.join(" · "))}</p>}
      </div>
    );
  }
  if (done.length) {
    // The calls' tally is the demo's payoff, so on a phone it wraps rather than losing its end.
    return (
      <p className="flex items-baseline gap-x-1.5 text-meta" aria-live="polite">
        <span aria-hidden className="h-2 w-2 shrink-0 self-center rounded-full bg-real" />
        <span className="font-label text-ink">{capitalize(done.join(" · "))}</span>
      </p>
    );
  }
  const shown = view.board.length;
  return (
    <p className={`text-meta text-ink-3 tnum ${compact ? "truncate" : ""}`}>
      {view.matches.length.toLocaleString("en-CA")} {view.matches.length === 1 ? "place fits" : "places fit"}
      {shown > 0 && ` · the best ${shown} are numbered on the map`}
    </p>
  );
}

/**
 * What the calls found, once they're all done: the results text (written by code from the outcomes,
 * the same words the renter gets by text), at the top of the panel, one line per place with the
 * booking first.
 */
export function Results({ view, compact = false }: { view: SessionView | null; compact?: boolean }) {
  if (view?.phase !== "done" || !view.results) return null;
  const lines = resultsByPlace(view.results, view.board.map((c) => c.address));
  return (
    <section aria-label="What the calls found">
      <p className="text-meta text-ink-3">What the calls found</p>
      <ul className="mt-1 space-y-1.5">
        {lines.map((line, i) => (
          <li key={i} className={`text-ink ${compact ? "text-support" : "text-fact"}`}>
            {line}
          </li>
        ))}
      </ul>
    </section>
  );
}

/**
 * The first thing in the panel for a renter Landline knows (U-08). Landline
 * opens a returning call with the welcome and what the last call's agents found
 * ("Welcome back, Sara. I booked 412 Markham…"); the panel shows it here, once.
 */
export function Welcome({ view, compact = false }: { view: SessionView | null; compact?: boolean }) {
  if (!view?.returning) return null;
  const { greeting, news } = splitWelcome(opensWithWelcome(view) ? view.agent_says : "", view.user_name);
  return (
    <div className="space-y-0.5">
      <p className={`font-label text-ink ${compact ? "text-support" : "text-fact"}`}>{greeting}</p>
      {news && <p className="text-support text-ink-2">{news}</p>}
    </div>
  );
}

/** An empty board, and Landline's last line is the stock one for it. */
const saysNothingFits = (view: SessionView) => !view.board.length && /^Nothing fits yet\b/.test(view.agent_says ?? "");

/** Landline's last line is the returning renter's welcome: the panel shows it once, as the welcome. */
const opensWithWelcome = (view: SessionView) => view.returning && /^welcome back/i.test(view.agent_says ?? "");

/**
 * What Landline heard and what it last said: the renter's half of the call, on
 * screen. `compact` (phones) keeps it to one line so the first place shows
 * above the fold of the sheet; the renter is hearing Landline already. `wrap`
 * lets that line wrap once the sheet is raised and there's room.
 */
export function Heard({
  view,
  compact = false,
  wrap = false,
}: {
  view: SessionView | null;
  compact?: boolean;
  wrap?: boolean;
}) {
  if (!view) {
    return (
      <div className="space-y-2" aria-hidden>
        <div className="h-4 w-2/3 rounded bg-press" />
        {!compact && <div className="h-4 w-1/2 rounded bg-press" />}
      </div>
    );
  }
  const heard = capitalize(heardLine(view.preferences).join(" · "));

  if (compact) {
    return (
      <div className="min-w-0">
        {heard && (
          <p className={`text-support text-ink ${wrap ? "" : "truncate"}`}>
            <span className="text-ink-3">Heard </span>
            {heard.charAt(0).toLowerCase() + heard.slice(1)}
          </p>
        )}
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {heard && (
        <div>
          <p className="text-meta text-ink-3">What I heard</p>
          <p className="text-support text-ink">{heard}</p>
        </div>
      )}
      {/* What Landline said during the brief: stale once the calls start. With nothing on the board, its
          stock "Nothing fits yet" is what the list's empty state says already; anything else it said shows. */}
      {view.agent_says && view.phase === "open" && !saysNothingFits(view) && !opensWithWelcome(view) && (
        <blockquote className="m-0 border-0 p-0">
          <p className="text-meta text-ink-3">Landline said</p>
          <p className="text-fact">“{view.agent_says}”</p>
        </blockquote>
      )}
    </div>
  );
}

function count(board: Card[]) {
  const n = { calling: 0, verified: 0, suspect: 0, dead: 0, no_answer: 0, booked: 0, pending: 0 };
  for (const c of board) n[c.status] += 1;
  return n;
}

const capitalize = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);
