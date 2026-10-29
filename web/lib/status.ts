/**
 * One status, said the same way everywhere (the canvas's Status board): a shape on the pin, the same face on
 * the card's rank, a glyph and words on the card. Colour only confirms it. The card, the pin, the detail and
 * the results all read `statusOf`, so they can't disagree. Every word comes from the server's status, its
 * calls and how they ended; nothing says "Dialing…" or gives a time the server didn't send.
 */
import { viewingWhen } from "./time.ts";
import type { CallView, Card, EndReason } from "./types.ts";

/** Where a planned place stands before its call has a result: picked, queued after hang-up, ringing. */
export type Stage = "will_call" | "queued" | "ringing";

export type StatusKind =
  | "ranked"
  | "will_call"
  | "queued"
  | "ringing"
  | "on_phone"
  | "checked"
  | "booked"
  | "confirmed"
  | "declined"
  | "flagged"
  | "unheard"
  | "gone";

/** The status line's colour, as landline.css names it (`.status.is-…`). Red is a problem with the place. */
export type Tone = "ink" | "quiet" | "live" | "real" | "dead";

/** Glyphs on Lucide's geometry, by Lucide's names; `exclamation` is the pin badge's bare "!". */
export type Glyph =
  | "phone"
  | "clock"
  | "audio-lines"
  | "circle-check"
  | "check"
  | "calendar"
  | "calendar-check"
  | "calendar-x"
  | "triangle-alert"
  | "exclamation"
  | "voicemail"
  | "x";

/** The pin's shape (`data-s` on the canvas's `.pin`). */
export type PinShape =
  | "pending"
  | "picked"
  | "queued"
  | "calling"
  | "checked"
  | "booked"
  | "confirmed"
  | "declined"
  | "flagged"
  | "noanswer"
  | "gone";

export type Status = {
  kind: StatusKind;
  /** The status line; "" for a ranked place, where silence is the status. */
  words: string;
  /** A second line under it: who the viewing waits on, or the red flag in full. */
  sub: string | null;
  /** The card's glyph; null with no status line. */
  glyph: Glyph | null;
  tone: Tone;
  pin: PinShape;
  /** The badge on the pin's face (and the card's rank); null for none. */
  badge: Glyph | null;
  /** What a screen reader hears for it, never empty. */
  spoken: string;
};

/** What a call that reached nobody, or learned nothing, says about how it ended: the card's words and the
 * detail's sentence. The server's results text words these the same (orchestrator/narrator.py, #152). */
export const UNHEARD: Record<EndReason, { word: string; line: string }> = {
  no_answer: { word: "No answer", line: "Nobody picked up." },
  busy: { word: "No answer", line: "Nobody picked up." },
  voicemail: { word: "Left a message", line: "Landline left a message for the agent." },
  hangup: { word: "Hung up early", line: "The agent hung up before Landline could ask anything." },
  timeout: { word: "Call dropped", line: "The call dropped before Landline learned anything." },
  failed: { word: "Call dropped", line: "The call dropped before Landline learned anything." },
  blocked: { word: "Couldn’t call", line: "Landline couldn’t place this call." },
  done: { word: "Reached, nothing new", line: "Landline reached the agent, but learned nothing new." },
  refused: { word: "Wouldn’t answer", line: "The agent wouldn’t answer Landline’s questions." },
  wrong_number: { word: "Wrong number", line: "The number Landline had didn’t reach the agent." },
};

/** A planned place's stage from its latest call: none yet means it will be called once the renter hangs up. */
export function stageOf(call: CallView | null | undefined, planned: boolean): Stage | null {
  if (!call) return planned ? "will_call" : null;
  if (call.state === "queued") return "queued";
  if (call.state === "dialing" || call.state === "ringing") return "ringing";
  return null;
}

type Of = {
  /** The place's latest call (a callback is a newer call for the same place). */
  call?: CallView | null;
  /** In the plan the renter picked. */
  planned?: boolean;
  /** The stage when the caller already worked it out (LiveBoard's marks); otherwise it comes from `call`. */
  stage?: Stage | null;
  /** How the call that learned nothing ended, when the caller has it but not the call. */
  ended?: EndReason | null;
};

const make = (s: Omit<Status, "spoken"> & { spoken?: string }): Status => ({
  ...s,
  spoken: s.spoken ?? (s.sub ? `${s.words}. ${s.sub}` : s.words),
});

/** The one status for a card, from the card, its latest call and its viewing. */
export function statusOf(card: Card, { call = null, planned = false, stage, ended }: Of = {}): Status {
  const step = stage === undefined ? stageOf(call, planned) : stage;
  if (step === "will_call") {
    return make({ kind: "will_call", words: "Will call when you hang up", sub: null, glyph: "phone", tone: "ink",
      pin: "picked", badge: "phone" });
  }
  if (step === "queued") {
    return make({ kind: "queued", words: "Queued", sub: null, glyph: "clock", tone: "quiet", pin: "queued",
      badge: "phone" });
  }
  if (step === "ringing") {
    return make({ kind: "ringing", words: "Ringing", sub: null, glyph: "phone", tone: "live", pin: "calling",
      badge: "phone" });
  }
  if (call?.state === "live" || card.status === "calling") {
    return make({ kind: "on_phone", words: "On the phone", sub: null, glyph: "audio-lines", tone: "live",
      pin: "calling", badge: "audio-lines" });
  }
  const viewing = card.viewing;
  if (viewing?.status === "confirmed") {
    return make({ kind: "confirmed", words: `Viewing confirmed · ${viewingWhen(viewing)}`, sub: null,
      glyph: "calendar-check", tone: "real", pin: "confirmed", badge: "check" });
  }
  if (viewing?.status === "declined") {
    return make({ kind: "declined", words: `The agent can’t do ${viewingWhen(viewing)}`, sub: null,
      glyph: "calendar-x", tone: "dead", pin: "declined", badge: "x" });
  }
  // Booked: ink, not green, until the agent confirms. The card's viewing has the time; a booking the
  // confirmations don't know yet has it on the call's outcome; with neither, no time is made up.
  const booked = viewing ?? call?.outcome?.viewing ?? null;
  if (viewing?.status === "booked" || card.status === "booked") {
    return make({ kind: "booked", words: booked ? `Viewing ${viewingWhen(booked)}` : "Viewing booked",
      sub: "Waiting for the agent to confirm", glyph: "calendar", tone: "ink", pin: "booked", badge: "calendar" });
  }
  if (card.status === "suspect") {
    return make({ kind: "flagged", words: "Something’s off", sub: card.red_flags[0] ?? null,
      glyph: "triangle-alert", tone: "dead", pin: "flagged", badge: "exclamation" });
  }
  if (card.status === "verified") {
    return make({ kind: "checked", words: "Checked by phone", sub: null, glyph: "circle-check", tone: "real",
      pin: "checked", badge: "check" });
  }
  if (card.status === "dead") {
    return make({ kind: "gone", words: "Already rented", sub: null, glyph: "x", tone: "quiet", pin: "gone",
      badge: null });
  }
  if (card.status === "no_answer") {
    // Worded from how the call ended; a call that heard facts isn't "unheard" (the card shows them instead).
    const reason = ended ?? (call?.outcome && !call.outcome.facts.length ? call.outcome.end_reason : null);
    const word = (reason && UNHEARD[reason]?.word) || "No answer";
    return make({ kind: "unheard", words: word, sub: null, glyph: "voicemail", tone: "quiet", pin: "noanswer",
      badge: null });
  }
  return make({ kind: "ranked", words: "", sub: null, glyph: null, tone: "quiet", pin: "pending", badge: null,
    spoken: "Not called yet" });
}

/** The CSS colour for a tone, until every caller styles by `.status.is-…` (the Lucent tokens, PR1). */
export function toneColor(tone: Tone): string {
  return {
    ink: "var(--color-ink)",
    quiet: "var(--color-ink-3)",
    live: "var(--color-live)",
    real: "var(--color-real)",
    dead: "var(--color-dead)",
  }[tone];
}
