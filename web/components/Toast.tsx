"use client";

import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { useCallback, useEffect, useRef, useState } from "react";
import { Icon, type IconName } from "./Icon";
import { EXIT, REDUCED, SPRING_LENS } from "@/lib/motion";

/** One toast: past tense, no exclamation marks (the canvas's Controls board). */
export type Toast = {
  text: string;
  icon: IconName;
  /** `real` for good news (a confirmed viewing); the words say it either way. */
  tone?: "real";
  /** A tap that failed offers Try again, which runs this. */
  retry?: () => void;
};

type Shown = Toast & { id: number };

/** Lucent's --dur-toast. One with Try again stays a little longer, so the renter can reach it. */
const STAY_MS = 2800;
const STAY_RETRY_MS = 6000;

/** The board's toasts, newest last, each gone after its time. */
export function useToasts() {
  const [shown, setShown] = useState<Shown[]>([]);
  const next = useRef(0);
  const timers = useRef(new Map<number, number>());

  const dismiss = useCallback((id: number) => {
    window.clearTimeout(timers.current.get(id));
    timers.current.delete(id);
    setShown((all) => all.filter((t) => t.id !== id));
  }, []);

  const toast = useCallback(
    (t: Toast) => {
      const id = ++next.current;
      setShown((all) => [...all.filter((s) => s.text !== t.text), { ...t, id }]);
      timers.current.set(id, window.setTimeout(() => dismiss(id), t.retry ? STAY_RETRY_MS : STAY_MS));
    },
    [dismiss],
  );

  useEffect(() => {
    const all = timers.current;
    return () => all.forEach((t) => window.clearTimeout(t));
  }, []);

  return { toasts: shown, toast, dismiss };
}

/**
 * Toasts float over the map: top left beside the split panel, under the capsule on a phone. They arrive on
 * the lens spring and leave faster than they came; under reduced motion they only fade. A polite live
 * region, so a screen reader hears each one.
 */
export function Toasts({ toasts, dismiss, desktop }: { toasts: Shown[]; dismiss: (id: number) => void; desktop: boolean }) {
  const reduced = useReducedMotion();
  return (
    <div
      role="status"
      aria-live="polite"
      className={`pointer-events-none fixed z-30 flex flex-col gap-2 ${
        desktop ? "left-4 top-4 items-start" : "inset-x-3 items-center"
      }`}
      style={desktop ? undefined : { top: "calc(max(12px, env(safe-area-inset-top)) + 64px)" }}
    >
      <AnimatePresence initial={false}>
        {toasts.map((t) => (
          <motion.div
            key={t.id}
            layout={!reduced}
            initial={reduced ? { opacity: 0 } : { opacity: 0, y: -8, scale: 0.96 }}
            animate={{ opacity: 1, y: 0, scale: 1, transition: reduced ? REDUCED : SPRING_LENS }}
            exit={{ opacity: 0, y: reduced ? 0 : -8, transition: reduced ? REDUCED : EXIT }}
            className={`toast glass pointer-events-auto ${t.retry ? "pr-2" : ""}`}
          >
            <Icon name={t.icon} size={20} className={t.tone === "real" ? "c-real" : undefined} />
            {t.text}
            {t.retry && (
              <button
                type="button"
                className="btn btn-quiet btn-small"
                onClick={() => {
                  dismiss(t.id);
                  t.retry?.();
                }}
              >
                Try again
              </button>
            )}
          </motion.div>
        ))}
      </AnimatePresence>
    </div>
  );
}
