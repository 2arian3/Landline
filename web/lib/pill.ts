/**
 * The board's header pill, and the one offline message (PR9, the States board). Pure, so the card, the
 * header and judge mode can't word the same state two ways. The kind picks the glyph; the words always say
 * the state, so it never rests on colour alone.
 *
 *   no snapshot yet          Connecting       quiet, dots
 *   none for UNREACHABLE_MS  Offline          plain, with OFFLINE below (the header stops saying Connecting)
 *   a board, quiet 6.5 s     Reconnecting     quiet, dots: the last board stays exactly as it was
 *   a board, quiet 9 s       Offline          plain, with OFFLINE: the board comes back with the next answer
 *   phase open               Listening        plain
 *   phase calling            Calling N places live
 *   phase done               Calls finished   real
 *   phase closed             Call ended       quiet: the brief ended with nothing picked
 */
import type { Phase } from "./types";

export type PillKind =
  | "connecting"
  | "offline"
  | "reconnecting"
  | "listening"
  | "calling"
  | "finished"
  | "ended"
  | "line";
export type PillTone = "quiet" | "plain" | "live" | "real";
export type Pill = { kind: PillKind; words: string; tone: PillTone; waiting: boolean };

/** What the board knows: its last snapshot's phase and calls (null before the first), and the connection. */
export type PillInput = {
  phase: Phase | null;
  calls?: number; // calls in the plan's run, or the plan's length before they're queued
  stale: boolean; // no word from the server for 6.5 s, with a board on screen
  unreachable: boolean; // no word for 9 s (and no snapshot yet, or a board gone quiet)
  line?: string | null; // judge mode's voice line, while it isn't live: it wins, so the pill never says Listening over a dropped call
};

export function pill({ phase, calls = 0, stale, unreachable, line = null }: PillInput): Pill {
  if (line) return { kind: "line", words: line, tone: "quiet", waiting: false };
  if (phase === null) {
    return unreachable
      ? { kind: "offline", words: "Offline", tone: "plain", waiting: false }
      : { kind: "connecting", words: "Connecting", tone: "quiet", waiting: true };
  }
  if (unreachable) return { kind: "offline", words: "Offline", tone: "plain", waiting: false };
  if (stale) return { kind: "reconnecting", words: "Reconnecting", tone: "quiet", waiting: true };
  switch (phase) {
    case "open":
      return { kind: "listening", words: "Listening", tone: "plain", waiting: false };
    case "calling":
      return {
        kind: "calling",
        words: calls > 0 ? `Calling ${calls} ${calls === 1 ? "place" : "places"}` : "Calling",
        tone: "live",
        waiting: false,
      };
    case "done":
      return { kind: "finished", words: "Calls finished", tone: "real", waiting: false };
    case "closed":
      return { kind: "ended", words: "Call ended", tone: "quiet", waiting: false };
  }
}

/** The one offline message: where the pill says Offline, the board says this, once. */
export const OFFLINE = {
  title: "Can’t reach Landline right now",
  body: "Trying again every few seconds. Your calls and the results text carry on without this page.",
} as const;

/** A tap the server turned down or never got: the card goes back, and a toast says this, with Try again. */
export function unsaved(said: string): string {
  return `Couldn’t save “${said}”`;
}
