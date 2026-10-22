"use client";

import { useSyncExternalStore } from "react";

/**
 * Subscribes to a media query. Returns null until the browser has answered
 * (server render and hydration), so callers can avoid mounting a layout, and
 * a map with it, only to tear it down a frame later.
 */
export function useMedia(query: string): boolean | null {
  return useSyncExternalStore<boolean | null>(
    (onChange) => {
      const mq = window.matchMedia(query);
      mq.addEventListener("change", onChange);
      return () => mq.removeEventListener("change", onChange);
    },
    () => window.matchMedia(query).matches,
    () => null,
  );
}
