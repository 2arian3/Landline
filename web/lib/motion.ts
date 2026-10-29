import type { Transition } from "motion/react";

/**
 * Lucent's springs for motion/react, with the stiffness and damping the design canvas draws as CSS curves
 * (landline.css, --spring-*). Pass one as a `transition`; under reduced motion use REDUCED instead.
 */

/** Cards gliding to a new rank: settles in about 615 ms, no visible overshoot. */
export const SPRING_RANK: Transition = { type: "spring", stiffness: 260, damping: 28 };

/** The phone's sheet settling on a stop: about 490 ms. */
export const SPRING_SHEET: Transition = { type: "spring", stiffness: 400, damping: 38 };

/** The segmented lens, chips and badges arriving: one soft 8% overshoot. */
export const SPRING_LENS: Transition = { type: "spring", stiffness: 260, damping: 20 };

/** The soft press, released: on a 4% scale the overshoot is about 1%. */
export const SPRING_JELLY: Transition = { type: "spring", stiffness: 460, damping: 16 };

/** A card opening into its detail and back: about 580 ms. */
export const SPRING_DETAIL: Transition = { type: "spring", stiffness: 300, damping: 30 };

/** Exits are faster than entrances: Lucent's --dur-exit on --ease-exit. */
export const EXIT: Transition = { duration: 0.18, ease: [0.4, 0, 1, 1] };

/** Under prefers-reduced-motion: a short fade in place of any spring. */
export const REDUCED: Transition = { duration: 0.15, ease: "linear" };
