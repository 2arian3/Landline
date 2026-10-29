"use client";

import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import type { Ref } from "react";
import type { PinShape } from "@/lib/status";
import { REDUCED, SPRING_LENS } from "@/lib/motion";
import { Icon, type IconName } from "./Icon";

/** Each pin shape, in the status board's order: the legend's words and the badge its mark carries (v2 boards). */
const KEY: [PinShape, string, IconName | null][] = [
  ["pending", "Ranked", null],
  ["picked", "Will call", "phone"],
  ["queued", "Queued", null],
  ["calling", "Calling", null],
  ["checked", "Checked", "check"],
  ["booked", "Booked", "calendar"],
  ["confirmed", "Confirmed", "check"],
  ["declined", "Can’t make it", "x"],
  ["flagged", "Flagged", "exclamation"],
  ["noanswer", "No answer", null],
  ["gone", "Already rented", null],
];

/**
 * The map's key, bottom left beside the map (the canvas's legend). It names only the shapes on the map now,
 * so the back of the room can read the pins without the panel; with no places ranked yet, it says what the
 * dots are, which the phone shows too (above its sheet); otherwise the key is the split layout's alone.
 */
export function Legend({ shapes, named, ref }: { shapes: Set<PinShape>; named: boolean; ref?: Ref<HTMLDivElement> }) {
  const reduce = useReducedMotion();
  type Item = [string, string, IconName | null];
  const empty = shapes.size === 0;
  const items: Item[] = [
    ...(empty ? [["dots", "Every dot is a place for rent", null] as Item] : KEY.filter(([shape]) => shapes.has(shape))),
    ...(named ? [["named", "The place you named", "map-pin"] as Item] : []),
  ];
  return (
    <div
      ref={ref}
      role="list"
      aria-label="Map key"
      // On the phone it shows only before anything is ranked (M1), just above the sheet; the cards say the rest.
      className={`legend glass-clear proj pointer-events-none absolute z-10 h-auto min-h-10 flex-wrap gap-y-1.5 py-2 split:bottom-4 split:left-4 split:inline-flex ${
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
