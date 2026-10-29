"use client";

import Link from "next/link";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { useEffect, useId, useRef, useState } from "react";
import { Icon, type IconName, Mark } from "./Icon";
import { heardLine } from "@/lib/format";
import { factLine } from "@/lib/heard";
import { EXIT, REDUCED, SPRING_LENS } from "@/lib/motion";
import { LANDLINE_PHONE } from "@/lib/phone";
import { type Pill, type PillKind, pill } from "@/lib/pill";
import { calledPlaces, statuses, tally } from "@/lib/tally";
import type { SessionView } from "@/lib/types";
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

/** Lucent's enter: 700ms on the settle curve (--dur-enter, --ease-settle). */
const ENTER = { duration: 0.7, ease: [0.22, 1, 0.36, 1] } as const;

/** A chip arriving on the lens spring with its one soft overshoot, leaving faster; reduced motion fades. The
 *  chips there when the panel opened don't arrive (AnimatePresence initial={false}). */
function usePop() {
  const reduced = useReducedMotion();
  return reduced
    ? { initial: { opacity: 0 }, animate: { opacity: 1, transition: REDUCED }, exit: { opacity: 0, transition: REDUCED } }
    : {
        layout: true,
        initial: { opacity: 0, scale: 0.85 },
        animate: { opacity: 1, scale: 1, transition: SPRING_LENS },
        exit: { opacity: 0, scale: 0.9, transition: EXIT },
      };
}

/** The plan's still to come: before the renter hangs up, and after it until the calls are scheduled (A-06). */
const planShown = (view: SessionView) =>
  view.plan.length > 0 && (view.phase === "open" || (view.phase === "calling" && !view.calls.length));

/**
 * The calls the renter picked, before they start: "Will call 2 places when you hang up", and each place by
 * its number and address, as the board shows them. Nothing about when or how is promised beyond that.
 */
export function Plan({ view }: { view: SessionView | null }) {
  const pop = usePop();
  if (!view || !planShown(view)) return null;
  const n = view.plan.length;
  const places = view.plan.map((id) => view.board.find((c) => c.listing_id === id)).filter((c) => c !== undefined);
  return (
    <section className="results" aria-live="polite">
      <div className="flex items-center gap-3.5">
        <span aria-hidden className="grid size-10 shrink-0 place-items-center rounded-full bg-ink text-white">
          <Icon name="phone" size={20} />
        </span>
        <p className="t-title">
          Will call {n} {n === 1 ? "place" : "places"}
          {view.phase === "open" && " when you hang up"}
        </p>
      </div>
      {places.length > 0 && (
        <ul className="chips" aria-label="The places Landline will call">
          <AnimatePresence initial={false}>
            {places.map((c) => (
              <motion.li key={c.listing_id} {...pop} className="chip gap-2 pl-1.5" data-s="picked">
                <span className="pin-face size-7! border-0! text-[14px]! shadow-none!">{c.rank}</span>
                {c.address}
              </motion.li>
            ))}
          </AnimatePresence>
        </ul>
      )}
    </section>
  );
}

/**
 * What the agents are doing right now, counted from each card's status in the words the cards use ("1 on
 * the phone · 1 ringing"). Nothing here is guessed: no ETAs. With no calls yet it says how many places fit.
 * The pill above already says how many places are being called.
 */
export function Activity({ view }: { view: SessionView | null }) {
  if (!view) return <div className="h-[18px] w-1/2 rounded bg-press" aria-hidden />;
  if (planShown(view)) return null;   // the plan says it
  if (view.phase === "closed" && !view.plan.length) {
    // The brief ended with nothing picked: say so, and how to start again. A second call starts fresh (#144).
    return (
      <div className="banner" role="status">
        <Icon name="phone" size={20} />
        <div>
          <p className="w6">The call ended before any places were picked</p>
          <p className="c-2">Call again to start a new search.</p>
          <a href={LANDLINE_PHONE.href} className="btn btn-secondary btn-small mt-3">
            <Icon name="phone" size={18} />
            Call Landline
          </a>
        </div>
      </div>
    );
  }
  // Once the calls are done, What the calls found says it, with its tally.
  if (view.phase === "done" && view.results) return null;
  const { going, done } = tally(statuses(view));
  const line = [...going, ...done.map(([, words]) => words)];
  if (line.length) {
    return (
      <p className="activity tnum text-ink" aria-live="polite">
        <span aria-hidden className={`pill-dot ${going.length ? "text-live" : "text-real"}`} />
        <span>{capitalize(line.join(" · "))}</span>
      </p>
    );
  }
  const shown = view.board.length;
  return (
    <p className="activity tnum">
      {view.matches.length.toLocaleString("en-CA")} {view.matches.length === 1 ? "place fits" : "places fit"}
      {shown > 0 && ` · the best ${shown} ${shown === 1 ? "is" : "are"} on the map`}
    </p>
  );
}

/**
 * What the calls found, once they're all done, built from the cards: each place with its number, its one
 * status and what its agent said, then the tally. The text Landline sent the renter (written by code from
 * the same outcomes) is a tap away under "Read the text".
 */
export function Results({ view, compact = false }: { view: SessionView | null; compact?: boolean }) {
  const [open, setOpen] = useState(false);
  const textId = useId();
  const reduced = useReducedMotion();
  if (view?.phase !== "done" || !view.results) return null;
  // The calls finish: the block slides in at the top and its rows arrive 60ms apart; reduced motion fades.
  const enter = (i: number) =>
    reduced
      ? { initial: { opacity: 0 }, animate: { opacity: 1 }, transition: REDUCED }
      : { initial: { opacity: 0, y: 8 }, animate: { opacity: 1, y: 0 }, transition: { ...ENTER, delay: i * 0.06 } };
  const rows = calledPlaces(view);
  const { done } = tally(rows);
  return (
    <motion.section
      className={`results ${compact ? "gap-2.5! p-3.5!" : ""}`}
      aria-label="What the calls found"
      {...enter(0)}
    >
      <div className="flex items-center justify-between gap-2">
        {/* On a phone: a one-line title and the button, nothing more (M7). */}
        {compact ? (
          <p className="t-headline">What the calls found</p>
        ) : (
          <div>
            <p className="t-title">What the calls found</p>
            <p className="t-caption c-2">The same words Landline texted you</p>
          </div>
        )}
        <button
          type="button"
          className={`btn btn-quiet btn-small ${compact ? "-mr-2.5 px-2.5! text-[15px]!" : "-mr-2"}`}
          aria-expanded={open}
          aria-controls={textId}
          onClick={() => setOpen(!open)}
        >
          Read the text
          {!compact && (
            <Icon
              name="chevron-right"
              size={18}
              className={`transition-transform motion-reduce:transition-none ${open ? "rotate-90" : ""}`}
            />
          )}
        </button>
      </div>
      {/* In the page even while folded away, so it can be found and checked against the session's. */}
      <p id={textId} hidden={!open} className="t-callout whitespace-pre-line">
        {view.results}
      </p>
      {rows.map(({ card, status }, i) => (
        <motion.div key={card.listing_id} className="result" data-s={status.pin} {...enter(i + 1)}>
          <span className="pin-face">{card.rank}</span>
          <div className="min-w-0">
            <p className={`t-headline ${compact ? "text-[16px]!" : ""}`}>{card.address}</p>
            {status.words && (
              <p className={`status is-${status.tone} mt-0.5`}>
                {status.glyph && <Icon name={status.glyph} size={18} />}
                <span>
                  {status.words}
                  {status.sub && <span className="sub block">{status.sub}</span>}
                </span>
              </p>
            )}
            {!compact && card.facts.length > 0 && (
              <p className="t-callout c-2 mt-1">
                {card.facts.slice(0, 3).map((f) => factLine(f.field, f.value)).join(" · ")}
              </p>
            )}
          </div>
        </motion.div>
      ))}
      {!compact && done.length > 0 && (
        <p className="tally">
          {done.map(([pin, words]) => (
            <span key={words}>
              <i className="mini" data-s={pin} aria-hidden />
              {words}
            </span>
          ))}
        </p>
      )}
    </motion.section>
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
  const pop = usePop();
  // The plan, and later What the calls found, take its place (P3, P6).
  if (view && (planShown(view) || (view.phase === "done" && view.results))) return null;
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
            <Icon name="ear" />
            What I heard
          </p>
          {/* A <p>, read as one line ("Up to $2,200 a month2+ bedroom…") by check-stalled-stream. */}
          <p className="chips">
            <AnimatePresence initial={false}>
              {chips.map((c) => (
                <motion.span
                  key={c}
                  {...pop}
                  className={`chip${fresh.has(c) ? " is-new" : ""}${compact ? " h-8! px-3! text-[14px]!" : ""}`}
                >
                  {c}
                </motion.span>
              ))}
            </AnimatePresence>
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

const capitalize = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);
