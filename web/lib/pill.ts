import type { SessionView } from "./types";

/** The panel header's pill (the canvas's "Controls and panels" board): what the call is doing, in a word or
 *  three, from the session's phase and its calls. It says whether the stream is up only when it isn't. */
export type Pill = {
  words: string;
  /** Lucent's pill tones: listening is the default accent, the rest add `is-live`, `is-real` or `is-quiet`. */
  tone: "listening" | "live" | "real" | "quiet";
  icon: "wave" | "phone" | "check" | "waiting" | "offline" | "ended";
};

/** Calls that are still going: everything that hasn't ended or been blocked. */
const GOING = new Set(["queued", "dialing", "ringing", "live", "voicemail"]);

export function pill(
  view: SessionView | null,
  { connected, stale, offline = false, line = null }: { connected: boolean; stale: boolean; offline?: boolean; line?: string | null },
): Pill {
  // Judge mode's voice line, while it isn't live, says its own state ("Connecting…", "The line dropped").
  if (line) return { words: line, tone: "quiet", icon: "waiting" };
  if (offline) return { words: "Offline", tone: "quiet", icon: "offline" };
  if (!view || !connected) return { words: "Connecting", tone: "quiet", icon: "waiting" };
  if (stale) return { words: "Reconnecting", tone: "quiet", icon: "waiting" };
  if (view.phase === "done") return { words: "Calls finished", tone: "real", icon: "check" };
  if (view.phase === "closed") return { words: "Call ended", tone: "quiet", icon: "ended" };
  if (view.phase === "calling") {
    // The calls still going; before the first is scheduled, the places planned.
    const going = view.calls.length ? view.calls.filter((c) => GOING.has(c.state)).length : view.plan.length;
    return { words: going ? `Calling ${going} ${going === 1 ? "place" : "places"}` : "Calling", tone: "live", icon: "phone" };
  }
  return { words: "Listening", tone: "listening", icon: "wave" };
}
