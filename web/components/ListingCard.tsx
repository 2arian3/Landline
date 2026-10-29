"use client";

import { Money } from "./Money";
import { money, photoAt, specLine } from "@/lib/format";
import type { Unheard } from "@/lib/outcome";
import { type Stage, statusOf, toneColor } from "@/lib/status";
import type { Card } from "@/lib/types";

/** Where a planned place stands before its call has a result (lib/status.ts `Stage`). */
export type Mark = Stage;

// Shims for the detail's header line until it reads statusOf itself (Lucent PR7): the same words and
// colours, built from lib/status.ts so they can't drift from the card's.
type Said = { word: string; color: string };
const said = (card: Partial<Card>, stage: Stage | null = null): Said => {
  const s = statusOf({ red_flags: [], ...card } as Card, { stage });
  return { word: s.words, color: toneColor(s.tone) };
};
export const STATUS: Partial<Record<Card["status"], Said>> = Object.fromEntries(
  (["calling", "verified", "suspect", "dead", "no_answer", "booked"] as const).map((k) => [k, said({ status: k })]),
);
export const MARK: Record<Mark, Said> = {
  will_call: said({ status: "pending" }, "will_call"),
  queued: said({ status: "pending" }, "queued"),
  ringing: said({ status: "pending" }, "ringing"),
};

/** "Up 2", "Down 1" or "New" for a few seconds after a re-rank; null when nothing moved. */
export type Move = number | "new" | null;

export function ListingCard({
  card,
  focused,
  hovered,
  moved = null,
  mark,
  ended,
  onSelect,
  onHover,
}: {
  card: Card;
  focused: boolean;
  hovered: boolean;
  moved?: Move;
  mark?: Mark;
  /** How a call that learned nothing ended, in place of "No answer yet". */
  ended?: Unheard | null;
  onSelect: (id: string) => void;
  onHover: (id: string | null) => void;
}) {
  // One status from lib/status.ts, as the pin and the detail say it. Until Board hands the card its call
  // (Lucent PR5), LiveBoard's mark and the end reason it worked out stand in for it.
  const s = statusOf(card, { stage: mark ?? null, ended: ended?.reason ?? null });
  // The red flag has its own line below, so its status line is the words alone.
  const sub = s.kind === "flagged" ? null : s.sub;
  const status = s.words ? { word: sub ? `${s.words} · ${sub}` : s.words, color: toneColor(s.tone) } : null;
  const dead = card.status === "dead";
  const flag = card.red_flags[0];
  // What a person said on the phone. A red flag outranks it here; the detail shows both.
  const heard = flag ? undefined : card.facts[0];
  const moveWord = moved === "new" ? "New" : moved && moved > 0 ? `Up ${moved}` : moved && moved < 0 ? `Down ${-moved}` : null;
  return (
    <button
      type="button"
      onClick={() => onSelect(card.listing_id)}
      onMouseEnter={() => onHover(card.listing_id)}
      onMouseLeave={() => onHover(null)}
      aria-current={focused ? "true" : undefined}
      aria-label={`${card.rank}. ${card.address}, ${money(card.monthly_cost)} a month, ${specLine(card)}${
        status ? `, ${status.word}` : ""
      }`}
      className="group grid w-full grid-cols-[28px_84px_1fr] items-start gap-3 rounded-[var(--radius-card)] bg-surface p-3 text-left transition-[box-shadow,background-color] duration-200"
      style={{
        boxShadow: focused
          ? "0 0 0 1.5px var(--color-ink)"
          : hovered
            ? "0 0 0 1px var(--color-line), 0 4px 14px rgba(26,24,21,.06)"
            : "0 0 0 1px var(--color-hair)",
        opacity: dead ? 0.62 : 1,
      }}
    >
      <span
        aria-hidden
        className="mt-0.5 grid h-7 w-7 place-items-center rounded-full bg-ink text-meta font-strong text-white tnum"
      >
        {card.rank}
      </span>

      <span className="relative block h-[84px] w-[84px] overflow-hidden rounded-[var(--radius-photo)] bg-press">
        {card.photo ? (
          // Source photos stay on the source's CDN, attributed in the detail view. The top card's is
          // usually the largest thing on a phone's first screen, so it loads first rather than lazily.
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={photoAt(card.photo, 168)}
            srcSet={`${photoAt(card.photo, 168)} 168w, ${photoAt(card.photo, 252)} 252w`}
            sizes="84px"
            width={84}
            height={84}
            alt=""
            loading={card.rank === 1 ? "eager" : "lazy"}
            fetchPriority={card.rank === 1 ? "high" : "auto"}
            className="h-full w-full object-cover"
          />
        ) : (
          // Said plainly, so an empty square doesn't read as a photo still loading.
          <span className="flex h-full w-full items-center justify-center text-meta text-ink-3">No photo</span>
        )}
      </span>

      <span className="min-w-0">
        <span className="flex items-start justify-between gap-2">
          <span className="min-w-0">
            <Money rent={card.rent} monthly={card.monthly_cost} />
            {/* Room for the "Listed $2,200 +$180" line until a call finds a fee, the same height as that line,
                so a card never grows when one lands. */}
            {card.monthly_cost === card.rent && (
              <span aria-hidden className="mt-0.5 block h-[var(--text-meta--line-height)]" />
            )}
          </span>
          {moveWord && (
            <span className="mt-1 shrink-0 text-meta font-label text-ink-2 tnum" aria-hidden>
              {moveWord}
            </span>
          )}
        </span>
        {/* On a phone a long address wraps to a second line; wider, the card has room for it on one. */}
        <span className={`mt-1 block text-fact font-label max-sm:line-clamp-2 sm:truncate ${dead ? "line-through" : ""}`}>
          {card.address}
        </span>
        <span className="block truncate text-meta text-ink-3">{specLine(card)}</span>
        {/* The evidence wraps to a second line rather than ending in "…"; a red flag is shown whole. */}
        {card.reasons.length > 0 && (
          <span className="mt-1 line-clamp-2 text-meta text-ink-2">{card.reasons.join(" · ")}</span>
        )}
        {flag && <span className="block text-meta text-dead">Red flag: {lower(flag)}</span>}
        {heard && (
          <span className="line-clamp-2 text-meta text-ink-2">
            <span className="text-ink-3">{heard.speaker}:</span> {lower(heard.value)}
          </span>
        )}
        {card.watch_out && <span className="line-clamp-2 text-meta text-dead">{card.watch_out}</span>}
        {card.reaction && (
          <span className="block truncate text-meta text-ink">
            <span className="font-label">You:</span> {lower(card.reaction)}
          </span>
        )}
        {status && (
          <span
            className="mt-1 flex items-start gap-1.5 text-meta font-label transition-colors duration-300 motion-reduce:transition-none"
            style={{ color: status.color }}
          >
            {/* Beside the first line when a viewing wraps to two. */}
            <span
              aria-hidden
              className={`mt-[6px] h-1.5 w-1.5 shrink-0 rounded-full ${s.tone === "live" ? "live-dot" : ""}`}
              style={{ background: status.color }}
            />
            {status.word}
          </span>
        )}
      </span>
    </button>
  );
}

const lower = (s: string) => s.charAt(0).toLowerCase() + s.slice(1);
