"use client";

import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import type { Ref } from "react";
import type { PinShape, Status } from "@/lib/status";
import { REDUCED, SPRING_LENS } from "@/lib/motion";
import { Icon, type IconName } from "./Icon";

/** The pin shapes in the status board's order: the legend lists them this way. */
const ORDER: PinShape[] = ["pending", "picked", "queued", "calling", "checked", "booked", "confirmed", "declined",
  "flagged", "noanswer", "gone"];

/**
 * The map's key, bottom left beside the map (the canvas's legend). It names only the shapes on the map now,
 * in statusOf's own short words (lib/status.ts), so the pin, the key and the card can't disagree, and the back of
 * the room can read the pins without the panel; with no places ranked yet, it says what the
 * dots are, which the phone shows too (above its sheet); otherwise the key is the split layout's alone.
 */
export function Legend({ statuses, named, ref }: { statuses: Status[]; named: boolean; ref?: Ref<HTMLDivElement> }) {
  const reduce = useReducedMotion();
  type Item = [string, string, IconName | null];
  // One entry per shape on the map, in the board's order; a ringing call and a live one are both "Calling".
  const byShape = new Map<PinShape, Status>();
  for (const st of statuses) if (!byShape.has(st.pin)) byShape.set(st.pin, st);
  const empty = byShape.size === 0;
  const items: Item[] = [
    ...(empty
      ? [["dots", "Every dot is a place for rent", null] as Item]
      : ORDER.filter((shape) => byShape.has(shape)).map((shape): Item => {
          const st = byShape.get(shape)!;
          // The mini carries the pin's badge, but a call's ring is its own sign.
          return [shape, st.legend, shape === "calling" ? null : st.badge];
        })),
    ...(named ? [["named", "The place you named", "map-pin"] as Item] : []),
  ];
  return (
    <div
      ref={ref}
      role="list"
      aria-label="Map key"
      // On the phone it shows only before anything is ranked (M1), just above the sheet; the cards say the rest.
      className={`legend glass-clear proj pointer-events-none absolute z-10 h-auto min-h-10 flex-wrap min-[1600px]:min-h-12 gap-y-1.5 py-2 split:bottom-4 split:left-4 split:inline-flex ${
        empty ? "bottom-[calc(var(--map-chrome-bottom,0px)+16px)] left-3 inline-flex" : "hidden"
      }`}
      // Short of the attribution, bottom right (about 410px with its margins), and of a panel floating over the
      // map (--map-chrome-right): with many statuses on the map, the key wraps instead of covering either.
      style={{ maxWidth: "max(240px, calc(100% - var(--map-chrome-right, 0px) - 460px))" }}
    >
      <AnimatePresence initial={false}>
        {items.map(([shape, words, glyph]) => (
          <motion.span
            key={shape}
            role="listitem"
            layout="position"
            initial={{ opacity: 0, scale: 0.9 }}
            animate={{ opacity: 1, scale: 1 }}
            exit={{ opacity: 0 }}
            transition={reduce ? REDUCED : SPRING_LENS}
          >
            <Mark shape={shape} glyph={glyph} />
            {words}
          </motion.span>
        ))}
      </AnimatePresence>
    </div>
  );
}

/** The key's mark: a mini pin with the pin's own shape and badge, the dots' dot, or the anchor's pin. */
function Mark({ shape, glyph }: { shape: string; glyph: IconName | null }) {
  if (shape === "dots") return <i className="cdot static m-0" aria-hidden />;
  const cls = shape === "named" ? "mini is-anchor" : "mini";
  const s = shape === "named" || shape === "pending" || shape === "picked" ? undefined : shape;
  return (
    <i className={cls} data-s={s} aria-hidden>
      {glyph && <Icon name={glyph} />}
    </i>
  );
}
