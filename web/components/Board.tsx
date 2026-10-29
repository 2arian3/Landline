"use client";

import { AnimatePresence, LayoutGroup, motion, useReducedMotion } from "motion/react";
import { useEffect, useRef, useState } from "react";
import { ListingCard, type Move } from "./ListingCard";
import { EXIT, REDUCED, SPRING_RANK } from "@/lib/motion";
import type { Stage } from "@/lib/status";
import type { Unheard } from "@/lib/outcome";
import type { CallView, Card } from "@/lib/types";

const MOVE_MS = 4000;
/** Lucent's enter for a card that's new to the board: 700ms on the settle curve. */
const ENTER = { duration: 0.7, ease: [0.22, 1, 0.36, 1] } as const;

/**
 * The ranked list. `board` arrives ranked from the server and is never sorted
 * here. Cards are keyed by listing so a re-rank animates each card to its new
 * place on the rank spring instead of re-rendering the list; one moving up passes over one moving down,
 * new ones fade up and ones that leave fade out faster. Under reduced motion the order changes at once with
 * a short fade. For a few seconds
 * after a re-rank each card that moved says so ("Up 2", "New"), so the renter
 * can see what their last sentence did.
 */
export function Board({
  board,
  loading,
  focusId,
  hoverId,
  marks = {},
  ended = {},
  calls = [],
  plan = [],
  onSelect,
  onHover,
}: {
  board: Card[];
  loading: boolean;
  focusId: string | null;
  hoverId: string | null;
  marks?: Record<string, Stage>;
  ended?: Record<string, Unheard>;
  /** The session's calls, oldest first: each card gets its place's latest (a callback is a newer call). */
  calls?: CallView[];
  /** The places the renter picked to call. */
  plan?: string[];
  onSelect: (id: string) => void;
  onHover: (id: string | null) => void;
}) {
  const moves = useMoves(board);
  const reduced = useReducedMotion();

  if (loading) {
    return (
      <ol className="m-0 list-none space-y-2 p-0" aria-hidden>
        {[0, 1, 2].map((i) => (
          <li key={i} className="h-[110px] rounded-[var(--radius-md)] bg-surface" style={{ boxShadow: "0 0 0 1px var(--color-hairline)" }} />
        ))}
      </ol>
    );
  }
  if (!board.length) {
    return (
      <p className="rounded-[var(--radius-md)] bg-surface p-4 text-callout text-ink-secondary" style={{ boxShadow: "0 0 0 1px var(--color-hairline)" }}>
        Nothing fits yet. Widening the budget or the area usually finds a few.
      </p>
    );
  }
  return (
    <LayoutGroup>
      {/* No m-0 here: the panel's space-y gap below the list is a margin, and m-0 would override it. */}
      <ol className="list-none space-y-2 p-0" aria-label="Best matches, ranked">
        <AnimatePresence initial={false}>
          {board.map((card) => (
            <motion.li
              key={card.listing_id}
              layout={reduced ? false : "position"}
              initial={reduced ? { opacity: 0 } : { opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0, transition: reduced ? REDUCED : ENTER }}
              exit={{ opacity: 0, transition: reduced ? REDUCED : EXIT }}
              transition={reduced ? REDUCED : SPRING_RANK}
              // A card moving up passes over one moving down.
              style={{ position: "relative", zIndex: rises(moves[card.listing_id]) ? 2 : 1 }}
            >
              <ListingCard
                card={card}
                focused={card.listing_id === focusId}
                hovered={card.listing_id === hoverId}
                moved={moves[card.listing_id] ?? null}
                mark={marks[card.listing_id]}
                ended={ended[card.listing_id]}
                call={calls.filter((c) => c.listing_id === card.listing_id).at(-1) ?? null}
                planned={plan.includes(card.listing_id)}
                onSelect={onSelect}
                onHover={onHover}
              />
            </motion.li>
          ))}
        </AnimatePresence>
      </ol>
    </LayoutGroup>
  );
}

/** A card going up the list, or new to it, passes over the ones going down. */
const rises = (m: Move | undefined) => m === "new" || (typeof m === "number" && m > 0);

/** Rank changes since the previous board, held for MOVE_MS. The first board shows none. */
function useMoves(board: Card[]): Record<string, Move> {
  const prev = useRef<Map<string, number> | null>(null);
  const [moves, setMoves] = useState<Record<string, Move>>({});
  const key = board.map((c) => `${c.listing_id}:${c.rank}`).join(",");

  useEffect(() => {
    const now = new Map(board.map((c) => [c.listing_id, c.rank]));
    const before = prev.current;
    prev.current = now;
    // The first board (or the first after an empty one) has nothing to compare against.
    if (!before || before.size === 0) return;
    const next: Record<string, Move> = {};
    for (const [id, rank] of now) {
      const old = before.get(id);
      if (old == null) next[id] = "new";
      else if (old !== rank) next[id] = old - rank;
    }
    setMoves(next);
    if (!Object.keys(next).length) return;
    const t = setTimeout(() => setMoves({}), MOVE_MS);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  return moves;
}
