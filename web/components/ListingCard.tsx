"use client";

import { Icon } from "./Icon";
import { FeeLine, Price } from "./Money";
import { LiveLines } from "./Captions";
import { cardFacts, newestQuote } from "@/lib/facts";
import { money, photoAt, specLine } from "@/lib/format";
import type { Unheard } from "@/lib/outcome";
import { type Stage, statusOf } from "@/lib/status";
import { formatClock } from "@/lib/time";
import type { CallView, Card } from "@/lib/types";

/** "Up 2", "Down 1" or "New" for a few seconds after a re-rank; null when nothing moved. */
export type Move = number | "new" | null;

// What the card's costs come from when a call heard them, so the fee line can say who.
const COSTS = new Set(["parking", "parking_cost", "fees", "utilities_included", "rent"]);

/**
 * One place on the board (the canvas's Card board): money first, then the place, then what a person said.
 * The rank wears the pin's face, so the card and the map name the same place at a glance. The status line
 * comes from lib/status.ts, shared with the pin and the detail. While its call is on, the call's last two
 * lines show under it.
 */
export function ListingCard({
  card,
  focused,
  hovered,
  moved = null,
  call = null,
  planned = false,
  mark,
  ended,
  onSelect,
  onHover,
}: {
  card: Card;
  focused: boolean;
  hovered: boolean;
  moved?: Move;
  /** The place's latest call, for its status and its live lines. */
  call?: CallView | null;
  /** In the plan the renter picked. */
  planned?: boolean;
  /** LiveBoard's stage for the place, when it worked one out. */
  mark?: Stage;
  /** How a call that learned nothing ended, when LiveBoard worked it out. */
  ended?: Unheard | null;
  onSelect: (id: string) => void;
  onHover: (id: string | null) => void;
}) {
  const s = statusOf(card, { call, planned, stage: mark ?? undefined, ended: ended?.reason ?? null });
  const dead = s.kind === "gone";
  const flag = card.red_flags[0];
  const facts = cardFacts(card.facts);
  const quote = newestQuote(card.facts);
  const heardCost = card.facts.some((f) => COSTS.has(f.field));
  // A place in the calls may still get a fee, so its card keeps the fee line's room until then.
  const inCalls = s.kind !== "ranked";
  const live = s.kind === "on_phone" && call?.state === "live" ? call : null;
  // A flag says itself, in full, below; a flagged place with no flag text still gets its status line.
  const showStatus = !!s.words && !(s.kind === "flagged" && flag);
  const foot = facts.length > 0 || !!flag || showStatus || !!live;
  const moveWord = moved === "new" ? "New" : moved && moved > 0 ? `Up ${moved}` : moved && moved < 0 ? `Down ${-moved}` : null;
  const classes = ["card", focused && "is-focus", hovered && !focused && "is-hover", dead && "is-gone"];
  const said = formatClock(quote?.at);

  return (
    <button
      type="button"
      onClick={() => onSelect(card.listing_id)}
      onMouseEnter={() => onHover(card.listing_id)}
      onMouseLeave={() => onHover(null)}
      aria-current={focused ? "true" : undefined}
      data-listing-id={card.listing_id}
      aria-label={`${card.rank}. ${card.address}, ${money(card.monthly_cost)} a month, ${specLine(card)}, ${s.spoken}`}
      className={classes.filter(Boolean).join(" ")}
    >
      <span className="card-media">
        {card.photo ? (
          // Source photos stay on the source's CDN, attributed in the detail view. The top card's is
          // usually the largest thing on a phone's first screen, so it loads first rather than lazily.
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={photoAt(card.photo, 208)}
            srcSet={`${photoAt(card.photo, 208)} 208w, ${photoAt(card.photo, 312)} 312w`}
            sizes="(min-width: 1600px) 104px, 88px"
            width={104}
            height={104}
            alt=""
            loading={card.rank === 1 ? "eager" : "lazy"}
            fetchPriority={card.rank === 1 ? "high" : "auto"}
            className="card-photo"
            style={dead ? { opacity: 0.6 } : undefined}
          />
        ) : (
          // Said plainly, so an empty square doesn't read as a photo still loading.
          <span className="card-nophoto">No photo</span>
        )}
        {/* The rank wears the pin's face: same number, same shape, same badge as on the map. */}
        <span aria-hidden className="card-rank" data-s={s.pin}>
          <span className="pin-face">
            {card.rank}
            {s.badge && (
              <span className="pin-badge">
                <Icon name={s.badge} />
              </span>
            )}
          </span>
        </span>
      </span>

      <span className="card-body">
        <span className="card-top">
          <Price monthly={card.monthly_cost} />
          {moveWord && (
            <span aria-hidden className="move">
              {typeof moved === "number" && <Icon name={moved > 0 ? "arrow-up" : "arrow-down"} />}
              {moveWord}
            </span>
          )}
        </span>
        <FeeLine
          rent={card.rent}
          monthly={card.monthly_cost}
          source={heardCost ? "agent heard" : "listing says"}
          hold={inCalls}
        />
        {/* Two lines at most on a phone; one on a desktop and the projector. */}
        <span className="addr block max-sm:line-clamp-2 sm:truncate">{card.address}</span>
        <span className="spec block truncate">{specLine(card)}</span>
        {card.reasons.length > 0 && (
          // On a phone, once a call has heard facts, the facts say more than the reasons.
          <span className={`reasons line-clamp-2 ${facts.length ? "max-sm:hidden" : ""}`}>
            {card.reasons.join(" · ")}
          </span>
        )}
        {card.watch_out && <span className="reasons line-clamp-2 text-[var(--color-ink)]">{card.watch_out}</span>}
        {card.reaction && (
          <span className="you block truncate">
            <b>You:</b> {lower(card.reaction)}
          </span>
        )}
      </span>

      {foot && (
        <span className="card-foot">
          {facts.length > 0 && (
            <span className="facts">
              {facts.map((f) => (
                <span key={f.key} className="fact">
                  <Icon name={f.glyph} />
                  <span>
                    {f.label ? (
                      <>
                        <b>{f.label}:</b> {f.text}
                      </>
                    ) : (
                      <b>{f.text}</b>
                    )}
                  </span>
                </span>
              ))}
              {/* Every fact says where it came from (hard rule 3). On the projector, their words verbatim with
                  who said them and when; elsewhere who said them, and every quote is a tap away in the detail. */}
              {quote && (
                <span className="quote hidden min-[1600px]:block">
                  &ldquo;{quote.quote}&rdquo;{" "}
                  <span className="src">
                    {quoteSource(quote.speaker)}
                    {said ? `, ${said}` : ""}
                  </span>
                </span>
              )}
              <span className={`src block ${quote ? "min-[1600px]:hidden" : ""}`}>
                {quoteSource(quote?.speaker ?? card.facts[0]?.speaker ?? "Agent heard")} heard on the call
                {quote ? ", in their words in the detail" : ""}
              </span>
            </span>
          )}
          {flag && (
            // Shown whole: a warning a renter must read in full (#312).
            <span className="flag">
              <Icon name="triangle-alert" />
              <span>Something&rsquo;s off: {lower(flag)}</span>
            </span>
          )}
          {showStatus && (
            <span className={`status is-${s.tone}`}>
              {s.glyph && <Icon name={s.glyph} />}
              <span>
                {s.words}
                {s.sub && <span className="sub block">{s.sub}</span>}
              </span>
            </span>
          )}
          {live && <LiveLines key={live.call_id} call={live} />}
        </span>
      )}
    </button>
  );
}

/** "Agent" for "Agent heard": the quote's own line says it was heard. */
function quoteSource(speaker: string): string {
  return speaker.replace(/\s+heard$/i, "") || "Agent";
}

const lower = (s: string) => s.charAt(0).toLowerCase() + s.slice(1);
