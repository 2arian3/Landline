"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { Icon, type IconName, Mark } from "./Icon";
import { heardLine } from "@/lib/format";
import { unheardCounts } from "@/lib/outcome";
import { type Pill, type PillKind, pill } from "@/lib/pill";
import { resultsByPlace } from "@/lib/results";
import type { Card, SessionView } from "@/lib/types";
import { splitWelcome } from "@/lib/welcome";

/** Landline's mark and its name: the canvas's wordmark. */
export function Wordmark() {
  return (
    <p className="wordmark">
      <Mark />
      Landline
    </p>
  );
}

/** The glyph for each of the pill's kinds; Connecting and Reconnecting wait with dots instead. */
const PILL_ICON: Record<PillKind, IconName> = {
  connecting: "refresh-cw",
  reconnecting: "refresh-cw",
  offline: "wifi-off",
  listening: "audio-lines",
  calling: "phone",
  finished: "check",
  ended: "x",
  line: "phone",
};

/** What the call is doing (lib/pill.ts), as Lucent's pill: its glyph and its words, never colour alone. */
export function PhasePill({ pill: p }: { pill: Pill }) {
  return (
    <span className={`pill${p.tone === "plain" ? "" : ` is-${p.tone}`}`} aria-live="polite">
      {p.waiting ? (
        <span className="dots" aria-hidden>
          <i />
          <i />
          <i />
        </span>
      ) : (
        <Icon name={PILL_ICON[p.kind]} size={16} />
      )}
      {p.words}
    </span>
  );
}

/** The calls still going: everything that hasn't ended or been blocked. */
const GOING = new Set(["queued", "dialing", "ringing", "live", "voicemail"]);

/** How many places the pill says are being called: the calls still going, or the plan before they're queued. */
export const calling = (view: SessionView | null) =>
  !view ? 0 : view.calls.length ? view.calls.filter((c) => GOING.has(c.state)).length : view.plan.length;

/**
 * The header's two ends: the wordmark, and the pill saying what the call is doing. On a phone the capsule
 * also has "Your calls" as a 44px button (`yourCalls`); in the split layout it sits in the panel's foot.
 * Judge mode passes the voice line's state while it isn't live (`line`), and its intro shows no pill (`idle`).
 */
export function Brand({
  view = null,
  stale,
  unreachable = false,
  idle = false,
  line = null,
  yourCalls = false,
}: {
  view?: SessionView | null;
  stale: boolean;
  unreachable?: boolean;
  idle?: boolean;
  line?: string | null;
  yourCalls?: boolean;
}) {
  const p = pill({ phase: view?.phase ?? null, calls: calling(view), stale, unreachable, line });
  return (
    <>
      <Wordmark />
      <div className="flex min-w-0 items-center gap-0.5">
        {!idle && <PhasePill pill={p} />}
        {yourCalls && (
          <Link href="/me" className="btn btn-quiet btn-icon" aria-label="Your calls">
            <Icon name="user" size={20} />
          </Link>
        )}
      </div>
    </>
  );
}

const PHONE_PATH =
  "M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72c.13.96.36 1.9.7 2.81a2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45c.91.34 1.85.57 2.81.7A2 2 0 0 1 22 16.92z";

/** The plan's still to come: before the renter hangs up, and after it until the calls are scheduled (A-06). */
const planShown = (view: SessionView) =>
  view.plan.length > 0 && (view.phase === "open" || (view.phase === "calling" && !view.calls.length));

/**
 * The calls the renter picked, before they start: "Will call 2 places when you hang up", and each place by
 * its number and address, as the board shows them. Nothing about when or how is promised beyond that.
 */
export function Plan({ view }: { view: SessionView | null }) {
  if (!view || !planShown(view)) return null;
  const n = view.plan.length;
  const places = view.plan.map((id) => view.board.find((c) => c.listing_id === id)).filter((c) => c !== undefined);
  return (
    <section className="results" aria-live="polite">
      <div className="flex items-center gap-3.5">
        <span aria-hidden className="grid size-10 shrink-0 place-items-center rounded-full bg-ink text-white">
          <svg className="i size-5" viewBox="0 0 24 24">
            <path d={PHONE_PATH} />
          </svg>
        </span>
        <p className="t-title">
          Will call {n} {n === 1 ? "place" : "places"}
          {view.phase === "open" && " when you hang up"}
        </p>
      </div>
      {places.length > 0 && (
        <ul className="chips" aria-label="The places Landline will call">
          {places.map((c) => (
            <li key={c.listing_id} className="chip gap-2 pl-1.5" data-s="picked">
              <span className="pin-face size-7! border-0! text-[14px]! shadow-none!">{c.rank}</span>
              {c.address}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/**
 * What the agents are doing right now, counted from the calls and the card statuses the server sent, in the
 * words the cards use ("1 on the phone · 1 ringing"). Nothing here is guessed: no ETAs. With no calls yet it
 * says how many places fit. The pill above already says how many places are being called.
 */
export function Activity({ view, compact = false }: { view: SessionView | null; compact?: boolean }) {
  if (!view) return <div className="h-[18px] w-1/2 rounded bg-press" aria-hidden />;
  if (planShown(view)) return null;   // the plan says it
  const n = count(view.board);
  const done: string[] = [];
  if (n.booked) done.push(`${n.booked} ${n.booked === 1 ? "viewing" : "viewings"} booked`);
  if (n.verified) done.push(`${n.verified} checked by phone`);
  if (n.suspect) done.push(`${n.suspect} flagged`);
  for (const [word, k] of unheardCounts(view.board, view.calls)) done.push(`${k} ${word}`);   // like the badges
  if (n.dead) done.push(`${n.dead} already rented`);
  const by = (states: string[]) => view.calls.filter((c) => states.includes(c.state)).length;
  const going = [
    [by(["live"]), "on the phone"],
    [by(["dialing", "ringing"]), "ringing"],
    [by(["voicemail"]), "leaving a message"],
    [by(["queued"]), "queued"],
  ]
    .filter(([k]) => k)
    .map(([k, word]) => `${k} ${word}`);
  const wrap = compact ? "min-w-0 truncate" : "";

  if (going.length) {
    return (
      <p className="activity tnum text-ink" aria-live="polite">
        <span aria-hidden className="pill-dot text-live" />
        <span className={wrap}>{[...going, ...done].join(" · ")}</span>
      </p>
    );
  }
  // Once the calls are done, What the calls found says it, with its tally.
  if (view.phase === "done" && view.results) return null;
  if (done.length) {
    return (
      <p className="activity text-ink" aria-live="polite">
        <span aria-hidden className="pill-dot text-real" />
        {capitalize(done.join(" · "))}
      </p>
    );
  }
  const shown = view.board.length;
  return (
    <p className={`activity tnum ${compact ? "truncate" : ""}`}>
      {view.matches.length.toLocaleString("en-CA")} {view.matches.length === 1 ? "place fits" : "places fit"}
      {shown > 0 && ` · the best ${shown} ${shown === 1 ? "is" : "are"} on the map`}
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

/** A chip just heard wears an ink ring for 2s, then settles (the canvas's "What Landline heard"). The chips
 *  there when the page opened aren't new. */
function useJustHeard(chips: string[] | null): Set<string> {
  const seen = useRef<Set<string> | null>(null);
  const timers = useRef<number[]>([]);
  const [fresh, setFresh] = useState<Set<string>>(() => new Set());
  const key = chips?.join("\n") ?? null;
  useEffect(() => {
    if (key === null) return;
    const now = key ? key.split("\n") : [];
    if (seen.current === null) {
      seen.current = new Set(now);
      return;
    }
    const added = now.filter((c) => !seen.current?.has(c));
    for (const c of now) seen.current.add(c);
    if (!added.length) return;
    setFresh((f) => new Set([...f, ...added]));
    timers.current.push(
      window.setTimeout(() => setFresh((f) => new Set([...f].filter((c) => !added.includes(c)))), 2000),
    );
  }, [key]);
  useEffect(() => () => timers.current.forEach((t) => window.clearTimeout(t)), []);
  return fresh;
}

/**
 * What Landline heard, as chips, and what it last said: the renter's half of the call, on screen. At the
 * phone sheet's lowest stop (`compact` without `wrap`) it's one line, so the first place shows above the
 * fold; once the sheet is raised the chips show, a size smaller.
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
  const chips = view ? heardLine(view.preferences).map(capitalize) : null;
  const fresh = useJustHeard(chips);
  if (!view || !chips) {
    return (
      <div className="space-y-2" aria-hidden>
        <div className="h-4 w-2/3 rounded bg-press" />
        {!compact && <div className="h-4 w-1/2 rounded bg-press" />}
      </div>
    );
  }

  if (compact && !wrap) {
    return chips.length ? (
      <p className="truncate px-1 text-support text-ink">
        <span className="text-ink-3">Heard </span>
        {chips.join(" · ")}
      </p>
    ) : null;
  }

  return (
    <div className="flex flex-col gap-4">
      {chips.length > 0 && (
        <section className="flex flex-col gap-2.5">
          <p className="label">
            <svg className="i" viewBox="0 0 24 24" aria-hidden>
              <path d="M6 8.5a6.5 6.5 0 1 1 13 0c0 6-6 6-6 10a3.5 3.5 0 1 1-7 0" />
              <path d="M15 8.5a2.5 2.5 0 0 0-5 0v1a2 2 0 1 1 0 4" />
            </svg>
            What I heard
          </p>
          {/* A <p>, read as one line ("Up to $2,200 a month2+ bedroom…") by check-stalled-stream. */}
          <p className="chips">
            {chips.map((c) => (
              <span
                key={c}
                className={`chip${fresh.has(c) ? " is-new" : ""}${compact ? " h-8! px-3! text-[14px]!" : ""}`}
              >
                {c}
              </span>
            ))}
          </p>
        </section>
      )}
      {/* What Landline said during the brief: stale once the calls start. With nothing on the board, its
          stock "Nothing fits yet" is what the list's empty state says already; anything else it said shows. */}
      {!compact && view.agent_says && view.phase === "open" && !saysNothingFits(view) && !opensWithWelcome(view) && (
        <blockquote className="m-0 flex flex-col gap-1.5 border-0 p-0">
          <p className="label">Landline said</p>
          <p className="said">“{view.agent_says}”</p>
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
