"use client";

import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import type { Ref } from "react";
import type { PinShape } from "@/lib/status";
import { REDUCED, SPRING_LENS } from "@/lib/motion";
import { Icon } from "./Icon";

/** Each pin shape, in the status board's order, with the words the legend gives it. */
const KEY: [PinShape, string][] = [
  ["pending", "Best fits, ranked"],
  ["picked", "Will call"],
  ["queued", "Queued"],
  ["calling", "On the phone now"],
  ["checked", "Checked by phone"],
  ["booked", "Viewing booked"],
  ["confirmed", "Viewing confirmed"],
  ["declined", "Agent can’t make it"],
  ["flagged", "Something’s off"],
  ["noanswer", "Didn’t get through"],
  ["gone", "Already rented"],
];

/**
 * The map's key, bottom left beside the map (the canvas's legend). It names only the shapes on the map now,
 * so the back of the room can read the pins without the panel; with no places ranked yet, it says what the
 * dots are. Split layout only: on a phone the sheet covers the map's bottom and the cards say the same.
 */
export function Legend({ shapes, named, ref }: { shapes: Set<PinShape>; named: boolean; ref?: Ref<HTMLDivElement> }) {
  const reduce = useReducedMotion();
  const items = [
    ...(shapes.size ? KEY.filter(([shape]) => shapes.has(shape)) : [["dots", "Every dot is a place for rent"] as const]),
    ...(named ? [["named", "The place you named"] as const] : []),
  ];
  return (
    <div
      ref={ref}
      role="list"
      aria-label="Map key"
      className="legend glass-clear proj pointer-events-none absolute bottom-4 left-4 z-10 hidden h-auto min-h-10 flex-wrap gap-y-1.5 py-2 split:inline-flex"
      // Short of the attribution, bottom right (about 410px with its margins), and of a panel floating over the
      // map (--map-chrome-right): with many statuses on the map, the key wraps instead of covering either.
      style={{ maxWidth: "max(240px, calc(100% - var(--map-chrome-right, 0px) - 460px))" }}
    >
      <AnimatePresence initial={false}>
        {items.map(([shape, words]) => (
          <motion.span
            key={shape}
            role="listitem"
            layout="position"
            initial={{ opacity: 0, scale: 0.9 }}
            animate={{ opacity: 1, scale: 1 }}
            exit={{ opacity: 0 }}
            transition={reduce ? REDUCED : SPRING_LENS}
          >
            <Mark shape={shape} />
            {words}
          </motion.span>
        ))}
      </AnimatePresence>
    </div>
  );
}

/** The key's mark for a shape: a mini pin, or the glyph the pin's badge carries where the face alone is ink. */
function Mark({ shape }: { shape: PinShape | "dots" | "named" }) {
  if (shape === "dots") return <i className="cdot static m-0 h-2 w-2" aria-hidden />;
  if (shape === "named") return <i className="mini is-anchor" aria-hidden />;
  if (shape === "picked") return <Icon name="phone" size={16} />;
  if (shape === "queued") return <Icon name="clock" size={16} />;
  return <i className="mini" data-s={shape === "pending" ? undefined : shape} aria-hidden />;
}
