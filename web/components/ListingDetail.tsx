"use client";

import { motion, useReducedMotion } from "motion/react";
import { type UIEvent, useCallback, useEffect, useRef, useState } from "react";
import { Captions } from "./Captions";
import { Feedback, type Reaction } from "./Feedback";
import { Icon, type IconName } from "./Icon";
import { type WithTransit, gettingAround } from "@/lib/around";
import { bathsLabel, bedsLabel, money, sizeLabel, sourceName, unitLabel } from "@/lib/format";
import { factLine } from "@/lib/heard";
import { api } from "@/lib/live";
import { type Unheard, callNotes, unheard } from "@/lib/outcome";
import { type Stage, statusOf } from "@/lib/status";
import { SPRING_DETAIL } from "@/lib/motion";
import { formatWhen } from "@/lib/time";
import type { CallState, CallView, Card, Fact, ListingDetail as Detail, Provenance } from "@/lib/types";

export type { Reaction };

/** How each provenance source reads to a renter. Plain listing data needs no label: the section says so. */
const SOURCE_LABEL: Record<string, string | null> = {
  scraped: null,
  llm: "From the description",
  city: "City record",
  call: "Agent heard",
  derived: "Worked out",
  synthetic: "Estimated",
};

/** The glyph beside a fact a call heard, by what it's about. */
const FACT_ICON: Record<string, IconName> = {
  available: "circle-check",
  available_from: "calendar",
  rent: "receipt",
  fees: "receipt",
  parking: "square-parking",
  parking_cost: "square-parking",
  pets: "paw-print",
  laundry: "washing-machine",
  utilities_included: "plug",
  sqft: "scan",
};

const ON_THE_LINE: CallState[] = ["dialing", "ringing", "live"];
const STILL_THERE = /^(yes|yep|yeah|true|still available|available)$/i;
const TORONTO = "America/Toronto";

/**
 * One listing in full (the canvas's P7, and M5 and M6 on a phone). Order is by what the renter must not
 * miss: the price and what a call changed in it, the viewing they act on next, anything off, a call that's
 * on the line now, then what a person said on the phone, each fact with their own words and when, then
 * getting around and what the listing claims, each with where it came from.
 *
 * Until the listing arrives it paints from the card, holding the room the listing's own lines will take,
 * so nothing jumps when it lands; a failed load keeps all of that and offers Try again. Escape goes back,
 * and focus lands on the card that opened it.
 */
export function ListingDetail({
  id,
  card,
  mark,
  call,
  results,
  places,
  onBack,
  onReact,
}: {
  id: string;
  card?: Card;
  /** Where a planned place stands before its call has a result (LiveBoard's marks). */
  mark?: Stage | null;
  /** The place's latest call, whose captions show while it runs. */
  call?: CallView;
  /** The session's results text once its calls are done; this place's sentences show here. */
  results?: string | null;
  /** How many places are on the board, for "1 of 8". */
  places?: number;
  onBack: () => void;
  onReact?: (reaction: Reaction, reason: string | null) => void;
}) {
  const [detail, setDetail] = useState<Detail | null>(null);
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const backButton = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    const ctl = new AbortController();
    setDetail(null);
    setFailed(false);
    fetch(api(`/api/listings/${encodeURIComponent(id)}`), { signal: ctl.signal })
      .then((r) => (r.ok ? r.json() : Promise.reject()))
      .then(setDetail)
      .catch(() => !ctl.signal.aborted && setFailed(true));
    return () => ctl.abort();
  }, [id, attempt]);

  const back = useCallback(() => {
    onBack();
    returnFocus(id, card);
  }, [id, card, onBack]);

  // The detail takes focus as it opens, so the keyboard carries on here, and Escape goes back. A field the
  // renter is typing in (a reason) keeps its own Escape. In the capture phase: on a phone the sheet (vaul,
  // on Radix's dismissable layer) takes Escape on the document first, and it's this detail's to close.
  useEffect(() => {
    backButton.current?.focus({ preventScroll: true });
  }, []);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape" || e.defaultPrevented) return;
      if ((e.target as HTMLElement | null)?.closest("input, textarea, select, [contenteditable='true']")) return;
      e.preventDefault();
      e.stopPropagation();
      back();
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [back]);

  const now = new Date();
  const src = detail ? sourceName(detail.source) : card ? sourceName(card.source) : "the listing";
  const loading = !detail && !failed;
  const photos = detail?.photos.length ? detail.photos : card?.photo ? [card.photo] : [];
  // The card is the session's truth: it carries what calls changed (+$150 parking).
  const price = card ?? detail;
  // The card's photo, price and address travel here (layoutId), unless motion is reduced.
  const reduced = useReducedMotion();
  const travel = (part: string) => (reduced ? undefined : `${part}-${id}`);
  const address = detail?.address ?? card?.address;
  const unit = card?.unit_label;
  const place = [detail?.building_name, card?.neighbourhood].find((p) => p && p !== address) ?? null;
  const spec = price
    ? [place, bedsLabel(price.beds), bathsLabel(price.baths), sizeLabel(price.sqft)].filter(Boolean).join(" · ")
    : null;

  const status = card ? statusOf(card, { call, stage: mark === undefined ? undefined : mark }) : null;
  const viewingKind = status && ["booked", "confirmed", "declined"].includes(status.kind) ? status : null;
  // A finished call carries what it settled (CallView.outcome); the results text is the fallback.
  const notes = callNotes(card, results, call ? [call] : []);
  const viewingWords = viewingKind?.words ?? (notes.viewing ? `Viewing ${notes.viewing}` : null);
  const flags = card?.red_flags ?? [];
  const facts = card?.facts ?? [];
  const asked = notes.questions.length > 0;
  const onLine = !!call && ON_THE_LINE.includes(call.state);
  const around = gettingAround(detail as WithTransit | null, card);
  const knew = facts.length > 0 || !!viewingWords || flags.length > 0;

  const rows: [field: string, label: string, value: string | null][] = detail
    ? [
        ["beds", "Bedrooms", bedsLabel(detail.beds)],
        ["baths", "Bathrooms", bathsLabel(detail.baths)],
        ["sqft", "Size", sizeLabel(detail.sqft)],
        ["unit_type", "Type", unitLabel(detail.unit_type)],
        ["floor", "Floor", detail.floor != null ? String(detail.floor) : null],
        ["furnished", "Furnished", detail.furnished == null ? null : detail.furnished ? "Yes" : "No"],
        ["utilities_included", "Utilities", detail.utilities_included.length ? "Included" : null],
        ["parking", "Parking", known(detail.parking)],
        ["pets", "Pets", known(detail.pets)],
        ["laundry", "Laundry", known(detail.laundry)],
      ]
    : [];
  const shown = rows.filter(([, , v]) => v);

  return (
    <article className="flex min-h-full flex-col gap-6 pb-8" aria-label={address ?? "A place"}>
      <nav className="flex items-center justify-between gap-3 pl-2 pr-4 pt-2" aria-label="Place">
        <button ref={backButton} type="button" onClick={back} className="back hover:bg-[var(--color-accent-tint)]">
          <Icon name="chevron-left" size={20} />
          All places
        </button>
        {/* Keyboard and mouse only: a phone has no Esc. */}
        <p className="t-caption c-2 hidden [@media(hover:hover)_and_(pointer:fine)]:block">
          {card && places ? `${card.rank} of ${places} · ` : null}Esc to go back
        </p>
      </nav>

      <div className="flex flex-col gap-3 px-4">
        {photos.length > 0 && <Hero photos={photos} address={address ?? "this place"} travel={travel("photo")} />}

        <header>
          {price ? (
            <div className="flex items-baseline justify-between gap-3">
              <motion.p layoutId={travel("price")} transition={SPRING_DETAIL} className="t-price-lead">
                <span aria-hidden>
                  {money(price.monthly_cost)} <span className="per">/mo</span>
                </span>
                <span className="sr-only">{money(price.monthly_cost)} a month</span>
              </motion.p>
              {photos.length > 0 && <p className="t-caption c-2 shrink-0">Photos: {src}</p>}
            </div>
          ) : (
            <div aria-hidden className="h-[var(--text-price-lead--line-height)]" />
          )}
          {price && price.monthly_cost !== price.rent && (
            <p className="fee mt-0.5">
              Listed <s className="tnum">{money(price.rent)}</s>
              <b className="tnum">
                {price.monthly_cost > price.rent ? "+" : "−"}
                {money(Math.abs(price.monthly_cost - price.rent))}
              </b>
              a month
            </p>
          )}
          <motion.h2 layoutId={travel("addr")} transition={SPRING_DETAIL} className="t-title mt-2">
            {address ?? " "}
            {unit ? <span className="font-normal c-2">, unit {unit}</span> : null}
          </motion.h2>
          {/* Painted from the card, so it's there before the listing lands and its line never moves. */}
          <p className="t-callout c-2 mt-0.5 min-h-[var(--text-callout--line-height)]">{spec}</p>
          {card?.watch_out && <p className="t-callout mt-1 text-dead">{card.watch_out}</p>}
        </header>

        {failed && (
          <div className="banner" role="status">
            <Icon name="circle-alert" size={20} />
            <div className="min-w-0">
              <p className="w6">Couldn&rsquo;t load this place&rsquo;s details</p>
              <p className="c-2">{knew ? "What the calls found is still here." : "Try again in a moment."}</p>
              <button
                type="button"
                onClick={() => setAttempt((n) => n + 1)}
                className="btn btn-secondary btn-small mt-2"
              >
                <Icon name="refresh-cw" size={18} />
                Try again
              </button>
            </div>
          </div>
        )}
      </div>

      {/* A booked viewing is what the renter acts on next, so it leads. */}
      {viewingWords && (
        <section className="px-4" aria-label="Viewing" aria-live="polite">
          <div className={`viewing ${TONE_CLASS[viewingKind?.tone ?? "ink"]}`}>
            <Icon
              name={
                viewingKind?.kind === "confirmed"
                  ? "calendar-check"
                  : viewingKind?.kind === "declined"
                    ? "calendar-x"
                    : "calendar"
              }
              size={20}
            />
            <div className="min-w-0">
              <p className="t-headline">{viewingWords}</p>
              <p className="t-callout c-2">
                Booked on the call, inside the times you gave.
                {viewingKind?.kind === "confirmed"
                  ? " The agent confirmed it."
                  : viewingKind?.kind === "declined"
                    ? " The agent can’t make that time."
                    : " Waiting for the agent to confirm."}
              </p>
            </div>
          </div>
        </section>
      )}

      {flags.length > 0 && (
        <section className="section px-4" aria-labelledby="flags-heading">
          <h3 id="flags-heading" className="t-headline text-dead">
            {flags.length === 1 ? "Something’s off" : `${flags.length} things are off`}
          </h3>
          <div>
            {flags.map((f) => (
              <Row key={f} icon="triangle-alert" tone="dead" value={f} />
            ))}
          </div>
          <p className="t-caption c-2">Landline won&rsquo;t book a viewing here.</p>
        </section>
      )}

      {/* The call on the line now: its lines as they're said (M5). Once it ends they fold under the facts. */}
      {onLine && call && (
        <section className="px-4" aria-label="The call now">
          <Captions key={call.call_id} call={call} />
        </section>
      )}

      {/* With only the booking to say, the section above already said it. */}
      {(facts.length || asked || !viewingWords || call) && (
        <section className="section px-4" aria-labelledby="phone-heading">
          <h3 id="phone-heading" className="t-headline">
            Checked by phone
          </h3>
          {facts.length > 0 && (
            <div aria-live="polite">
              {facts.map((f) => (
                <Row
                  key={f.field + f.quote}
                  icon={FACT_ICON[f.field] ?? "message-square"}
                  value={heardValue(f)}
                  quote={f.quote}
                  source={[f.speaker, f.at ? whenHeard(f.at, now) : null].filter(Boolean).join(" · ")}
                />
              ))}
            </div>
          )}
          {notes.questions.length > 0 && (
            <div>
              {notes.questions.map((q) => (
                <Row
                  key={q}
                  icon="message-circle"
                  value={`The agent asked, “${q}”`}
                  source="Landline said it would check with you"
                />
              ))}
            </div>
          )}
          {!facts.length && !asked && !viewingWords && (
            <p className="t-callout c-2">{noFacts(card?.status, mark, unheard(card, call ? [call] : []))}</p>
          )}
          {/* The call, line by line, once it's over: Captions folds it. */}
          {call && !onLine && <Captions key={call.call_id} call={call} />}
        </section>
      )}

      {onReact && (
        <section className="section px-4" aria-labelledby="take-heading">
          <h3 id="take-heading" className="t-headline">
            Your take
          </h3>
          <Feedback current={card?.reaction} onReact={onReact} />
        </section>
      )}

      {/* Before the listing's own fields, so it's on a projector's first screen. While the listing loads, a
          row is held for its subway walk, which comes first. */}
      {(around.length > 0 || loading) && (
        <section className="section px-4" aria-labelledby="around-heading">
          <h3 id="around-heading" className="t-headline">
            Getting around
          </h3>
          <div>
            {loading && !around.length && (
              <div aria-hidden className="h-[var(--text-body--line-height)] rounded-[8px] bg-fill-muted" />
            )}
            {around.map((line) => (
              <Row key={line.key} icon="map-pin" value={line.text} source={line.source} />
            ))}
          </div>
        </section>
      )}

      <section className="section px-4" aria-labelledby="listing-heading">
        <h3 id="listing-heading" className="t-headline">
          What the listing says
        </h3>
        {loading && (
          <div className="grid grid-cols-2 gap-x-4 gap-y-3" aria-hidden>
            {[0, 1, 2, 3].map((i) => (
              <div key={i} className="h-10 rounded-[8px] bg-fill-muted" />
            ))}
          </div>
        )}
        {shown.length > 0 && (
          <dl className="grid grid-cols-2 gap-x-4 gap-y-3">
            {shown.map(([field, label, value]) => (
              <div key={field} className="min-w-0">
                <dt className="t-caption c-2">{label}</dt>
                <dd className="t-body m-0 truncate">{value}</dd>
                <Source p={detail?.provenance?.[field]} />
              </div>
            ))}
          </dl>
        )}
        {detail && detail.amenities.length > 0 && (
          <p className="t-callout c-2">Building: {detail.amenities.slice(0, 8).join(", ")}</p>
        )}
        {detail && (
          <div>
            <p className="t-caption c-2">
              From {src}, last seen {dayOf(detail.last_seen)}.
            </p>
            {/* On its own line, so the whole 44 px row is the tap target. */}
            <a
              href={detail.url}
              target="_blank"
              rel="noreferrer"
              className="t-callout inline-flex min-h-11 items-center gap-1 underline underline-offset-2"
            >
              See the listing on {src}
              <Icon name="arrow-up-right" size={16} />
            </a>
          </div>
        )}
      </section>
    </article>
  );
}

// The viewing block's colour, by the same classes as the card's `.status is-…` lines, so the two can't drift.
const TONE_CLASS: Record<string, string> = { ink: "", quiet: "c-2", live: "c-live", real: "c-real", dead: "c-dead" };

/** The photos, one at a time, swiped or scrolled, with a dot for each (the canvas's hero). */
function Hero({ photos, address, travel }: { photos: string[]; address: string; travel?: string }) {
  const [at, setAt] = useState(0);
  const onScroll = (e: UIEvent<HTMLDivElement>) => {
    const el = e.currentTarget;
    setAt(Math.round(el.scrollLeft / Math.max(el.clientWidth, 1)));
  };
  return (
    <figure className="relative m-0 h-[196px] max-h-[38svh] overflow-hidden rounded-[16px] bg-surface shadow-[0_0_0_1px_var(--color-hairline)] min-[769px]:h-[240px] min-[1600px]:h-[300px]">
      <div
        className="flex h-full snap-x snap-mandatory overflow-x-auto [scrollbar-width:none]"
        onScroll={onScroll}
        tabIndex={photos.length > 1 ? 0 : undefined}
        role={photos.length > 1 ? "region" : undefined}
        aria-label={photos.length > 1 ? `Photos, ${at + 1} of ${photos.length}` : undefined}
      >
        {photos.map((p, i) => (
          // eslint-disable-next-line @next/next/no-img-element
          <motion.img
            key={p}
            layoutId={i === 0 ? travel : undefined}
            transition={SPRING_DETAIL}
            src={p}
            alt={i === 0 ? `Photo of ${address}` : ""}
            loading={i < 2 ? "eager" : "lazy"}
            fetchPriority={i === 0 ? "high" : "auto"}
            className="h-full w-full shrink-0 snap-start object-cover text-[0px]"
          />
        ))}
      </div>
      {photos.length > 1 && (
        <div className="pager glass-clear absolute bottom-3 left-1/2 -translate-x-1/2 rounded-full px-2.5 py-[7px]" aria-hidden>
          {photos.length <= 8 ? (
            photos.map((p, i) => <i key={p} className={i === at ? "is-on" : undefined} />)
          ) : (
            <span className="t-caption tnum">
              {at + 1} / {photos.length}
            </span>
          )}
        </div>
      )}
    </figure>
  );
}

/** One line in a section: a glyph, what it says, the person's own words, and where and when it came from. */
function Row({
  icon,
  value,
  quote,
  source,
  tone,
}: {
  icon: IconName;
  value: string;
  quote?: string;
  source?: string | null;
  tone?: "dead";
}) {
  return (
    <div className="grid grid-cols-[20px_minmax(0,1fr)] gap-x-3 border-t border-hairline py-3 first:border-t-0 first:pt-1">
      <Icon name={icon} size={20} className={`mt-[3px] ${tone === "dead" ? "text-dead" : "text-ink-secondary"}`} />
      <div className="min-w-0">
        <p className="t-body w6">{value}</p>
        {/* Their own words are the proof, so they read in ink, at the size of the text around them. */}
        {quote && <p className="t-callout mt-0.5">&ldquo;{quote}&rdquo;</p>}
        {source && <p className="t-caption c-2 mt-0.5">{source}</p>}
      </div>
    </div>
  );
}

/** A field's source when it isn't the listing itself, with the quote it came from on tap. */
function Source({ p }: { p?: Provenance }) {
  const label = p ? SOURCE_LABEL[p.source] : null;
  if (!p || !label) return null;
  const year = p.source === "city" && p.at ? ` (${new Date(p.at).getFullYear()})` : "";
  if (!p.quote) return <p className="t-caption c-2">{label + year}</p>;
  return (
    <details className="t-caption c-2">
      <summary className="cursor-pointer list-none underline decoration-dotted underline-offset-2">
        {label + year}
      </summary>
      <p className="mt-1">&ldquo;{p.quote}&rdquo;</p>
    </details>
  );
}

/** What a fact says, in the renter's words: "Still available", "Parking: $150 a month". */
function heardValue(f: Fact): string {
  if (f.field === "available" && STILL_THERE.test(f.value.trim())) return "Still available";
  return factLine(f.field, f.value);
}

/** When a fact was heard: the clock alone on the day it was heard, the board's full time on any other. */
function whenHeard(iso: string, now: Date): string | null {
  const full = formatWhen(iso);
  if (!full) return null;
  return dayOf(iso) === dayOf(now.toISOString()) ? full.slice(full.indexOf("·") + 2) : full;
}

/** "Oct 28", the day in Toronto. */
function dayOf(iso: string): string {
  return new Date(iso).toLocaleDateString("en-US", { timeZone: TORONTO, month: "short", day: "numeric" });
}

function noFacts(status?: Card["status"], mark?: Stage | null, ended?: Unheard | null): string {
  if (mark === "will_call") return "Landline calls them once you hang up. What they say lands here, with their words.";
  if (mark === "queued") return "Waiting for its turn on the phone.";
  if (mark === "ringing") return "Ringing now.";
  if (status === "calling") return "What the agent says lands here when the call ends, with their own words.";
  if (ended) return ended.line; // how the call that learned nothing ended
  if (status === "no_answer") return "Nobody picked up yet.";
  if (status === "booked") return "A viewing was booked on the call. Nothing else came up.";
  return "Nobody has called about this one yet. Say “call this one” and what the listing agent says lands here, with their words.";
}

function known(v: string): string | null {
  if (!v || v === "unknown") return null;
  return v.replaceAll("_", " ").replace(/^./, (c) => c.toUpperCase());
}

/**
 * After the list is back (it mounts once the detail's exit ends), focus the card that opened the detail: by
 * its listing id once the card carries `data-listing-id`, else by the card's own label ("3. 15 Mercer
 * Street, …"). Gives up after a second and a half.
 */
function returnFocus(id: string, card?: Card) {
  const until = performance.now() + 1500;
  const look = () => {
    const el =
      document.querySelector<HTMLElement>(`[data-listing-id="${CSS.escape(id)}"]`) ??
      (card
        ? document.querySelector<HTMLElement>(`button[aria-label^=${JSON.stringify(`${card.rank}. ${card.address},`)}]`)
        : null);
    if (el) el.focus();
    else if (performance.now() < until) requestAnimationFrame(look);
  };
  requestAnimationFrame(look);
}
