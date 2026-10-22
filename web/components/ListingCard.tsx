"use client";

import { Money } from "./Money";
import { money, specLine } from "@/lib/format";
import type { Card } from "@/lib/types";

const STATUS: Partial<Record<Card["status"], { word: string; color: string }>> = {
  calling: { word: "On the phone", color: "var(--color-live)" },
  verified: { word: "Checked by phone", color: "var(--color-real)" },
  suspect: { word: "Something's off", color: "var(--color-dead)" },
  dead: { word: "Already rented", color: "var(--color-ink-3)" },
  no_answer: { word: "No answer yet", color: "var(--color-ink-3)" },
  booked: { word: "Viewing booked", color: "var(--color-real)" },
};

export function ListingCard({
  card,
  focused,
  hovered,
  onSelect,
  onHover,
}: {
  card: Card;
  focused: boolean;
  hovered: boolean;
  onSelect: (id: string) => void;
  onHover: (id: string | null) => void;
}) {
  const status = STATUS[card.status];
  const dead = card.status === "dead";
  return (
    <button
      type="button"
      onClick={() => onSelect(card.listing_id)}
      onMouseEnter={() => onHover(card.listing_id)}
      onMouseLeave={() => onHover(null)}
      aria-current={focused ? "true" : undefined}
      aria-label={`${card.rank}. ${card.address}, ${money(card.monthly_cost)} a month, ${specLine(card)}`}
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
        {card.photo && (
          // Source photos stay on the source's CDN, attributed in the detail view.
          // eslint-disable-next-line @next/next/no-img-element
          <img src={card.photo} alt="" loading="lazy" className="h-full w-full object-cover" />
        )}
      </span>

      <span className="min-w-0">
        <Money rent={card.rent} monthly={card.monthly_cost} />
        <span className={`mt-1 block truncate text-fact font-label ${dead ? "line-through" : ""}`}>
          {card.address}
        </span>
        <span className="block truncate text-meta text-ink-3">{specLine(card)}</span>
        {card.reasons.length > 0 && (
          <span className="mt-1 block truncate text-meta text-ink-2">{card.reasons.join(" · ")}</span>
        )}
        {card.watch_out && <span className="block truncate text-meta text-dead">{card.watch_out}</span>}
        {status && (
          <span className="mt-1 flex items-center gap-1.5 text-meta font-label" style={{ color: status.color }}>
            <span aria-hidden className="h-1.5 w-1.5 rounded-full" style={{ background: status.color }} />
            {status.word}
          </span>
        )}
      </span>
    </button>
  );
}
