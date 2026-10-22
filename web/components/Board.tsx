"use client";

import { AnimatePresence, LayoutGroup, motion } from "motion/react";
import { ListingCard } from "./ListingCard";
import type { Card } from "@/lib/types";

/**
 * The ranked list. `board` arrives ranked from the server and is never sorted
 * here. Cards are keyed by listing so a re-rank animates each card to its new
 * place (layout animation) instead of re-rendering the list.
 */
export function Board({
  board,
  loading,
  focusId,
  hoverId,
  onSelect,
  onHover,
}: {
  board: Card[];
  loading: boolean;
  focusId: string | null;
  hoverId: string | null;
  onSelect: (id: string) => void;
  onHover: (id: string | null) => void;
}) {
  if (loading) {
    return (
      <ol className="m-0 list-none space-y-2 p-0" aria-hidden>
        {[0, 1, 2].map((i) => (
          <li key={i} className="h-[110px] rounded-[var(--radius-card)] bg-surface" style={{ boxShadow: "0 0 0 1px var(--color-hair)" }} />
        ))}
      </ol>
    );
  }
  if (!board.length) {
    return (
      <p className="rounded-[var(--radius-card)] bg-surface p-4 text-support text-ink-2" style={{ boxShadow: "0 0 0 1px var(--color-hair)" }}>
        Nothing fits yet. Widening the budget or the area usually finds a few.
      </p>
    );
  }
  return (
    <LayoutGroup>
      <ol className="m-0 list-none space-y-2 p-0" aria-label="Best matches, ranked">
        <AnimatePresence initial={false}>
          {board.map((card) => (
            <motion.li
              key={card.listing_id}
              layout="position"
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0 }}
              transition={{ type: "tween", ease: [0.23, 1, 0.32, 1], duration: 0.42 }}
            >
              <ListingCard
                card={card}
                focused={card.listing_id === focusId}
                hovered={card.listing_id === hoverId}
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
