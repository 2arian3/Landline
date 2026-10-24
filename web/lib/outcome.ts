import type { CallView, Card, EndReason } from "./types";

/** How a call that learned nothing ended, in words: the card's word and the detail's sentence. */
export type Unheard = { word: string; line: string };

// The server's results text words these the same way (orchestrator/narrator.py `_unheard`, #152).
const UNHEARD: Record<EndReason, Unheard> = {
  no_answer: { word: "No answer", line: "Nobody picked up." },
  busy: { word: "No answer", line: "Nobody picked up." },
  voicemail: { word: "Left a message", line: "Landline left a message for the agent." },
  hangup: { word: "Hung up early", line: "The agent hung up before Landline could ask anything." },
  timeout: { word: "Call dropped", line: "The call dropped before Landline learned anything." },
  failed: { word: "Call dropped", line: "The call dropped before Landline learned anything." },
  blocked: { word: "Couldn\u2019t call", line: "Landline couldn\u2019t place this call." },
  done: { word: "Reached, nothing new", line: "Landline reached the agent, but learned nothing new." },
  refused: { word: "Wouldn\u2019t answer", line: "The agent wouldn\u2019t answer Landline\u2019s questions." },
  wrong_number: { word: "Wrong number", line: "The number Landline had didn\u2019t reach the agent." },
};

/**
 * The card's status stays `no_answer` for every call that learned nothing (CardStatus is a contract), but an
 * answered call isn't "no answer": its latest outcome says how it ended. Null for any other card, a call with
 * no outcome yet, or one that heard facts (the card shows those instead).
 */
/**
 * The summary's counts for the cards whose calls learned nothing, in the same words as their badges:
 * [["hung up early", 1], ["no answer", 1]], most first. A card with no outcome yet counts as "no answer".
 */
export function unheardCounts(board: Card[], calls: CallView[] = []): [string, number][] {
  const n = new Map<string, number>();
  for (const card of board) {
    if (card.status !== "no_answer") continue;
    const word = (unheard(card, calls)?.word ?? "No answer").toLowerCase();
    n.set(word, (n.get(word) ?? 0) + 1);
  }
  return [...n].sort((a, b) => b[1] - a[1]);
}

export function unheard(card: Card | undefined, calls: CallView[] = []): Unheard | null {
  if (!card || card.status !== "no_answer") return null;
  const outcome = calls.filter((c) => c.listing_id === card.listing_id && c.outcome).at(-1)?.outcome;
  if (!outcome || outcome.facts.length) return null;
  return UNHEARD[outcome.end_reason] ?? null;
}

/** What a finished call settled for one place, beyond its facts: a booked viewing, questions for the renter. */
export type CallNotes = {
  viewing: string | null; // when, in words: "Saturday at 2 pm"
  questions: string[]; // what the listing agent asked that only the renter can answer
};

const NONE: CallNotes = { viewing: null, questions: [] };

/**
 * The one place that reads a place's booked viewing and noted questions. They come from the call's
 * outcome (`CallView.outcome`, the Auditor's CallOutcome for this listing) when the session has one.
 * Otherwise, from the session's results text: the Narrator's ("I booked 30 Carabob Court for Saturday at
 * 2 pm.") or the ?dev=1 demo's ("…, inside your Saturday afternoon."), and only for a place that was
 * called, since many listings share one street address and the text never names the unit. That fallback
 * breaks if the wording changes or the results are in another language. No lookbehind anywhere: Safari
 * before 16.4 can't parse it.
 */
export function callNotes(
  card: Card | undefined,
  results: string | null | undefined,
  calls: CallView[] = [],
): CallNotes {
  // The latest call to this place that has settled something.
  const outcome = card ? calls.filter((c) => c.listing_id === card.listing_id && c.outcome).at(-1)?.outcome : null;
  if (outcome) {
    return {
      viewing: outcome.viewing ? when(outcome.viewing.starts_at) : null,
      questions: outcome.open_questions,
    };
  }
  if (!card || !results || card.status === "pending" || card.status === "calling") return NONE;
  const said = sentencesAbout(results, card.address);
  return {
    viewing: said.map((s) => BOOKED.exec(s)?.[1]).find(Boolean) ?? null,
    questions: said.flatMap((s) => (s.includes(" asked, ") ? (/“([^”]+)”/.exec(s)?.slice(1) ?? []) : [])),
  };
}

/** "Saturday at 2 pm", Toronto time: the words the Narrator uses for a booked viewing (narrator.py `_when`). */
function when(iso: string): string {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "America/Toronto",
    weekday: "long",
    hour: "numeric",
    minute: "2-digit",
    hourCycle: "h23",
  }).formatToParts(new Date(iso));
  const get = (type: string) => parts.find((p) => p.type === type)?.value ?? "";
  const hour = Number(get("hour"));
  const minute = get("minute");
  return `${get("weekday")} at ${hour % 12 || 12}${minute === "00" ? "" : `:${minute}`} ${hour >= 12 ? "pm" : "am"}`;
}

// "I booked <place> for <when>." with the demo's ", inside your Saturday afternoon" after <when>, or not.
const BOOKED = /^I booked .+ for (.+?)(?:, inside .*)?\.$/;
// What the Narrator says after a place's first sentence without naming it again: where it moved, a
// conflict with the listing or the City record, the questions its agent asked (orchestrator/narrator.py).
const FOLLOW_ON = /^(?:It moves |It drops |Its agent asked|The listing says |The City record says )/;

/**
 * The sentences about this place: one that names it, and the Narrator's follow-ons right after it. Any
 * other sentence (another place, or "I couldn't reach that place in time.") ends the run.
 */
function sentencesAbout(text: string, address: string): string[] {
  let mine = false;
  return sentences(text, address).filter((s) => (mine = names(s, address) || (mine && FOLLOW_ON.test(s))));
}

/** Whether a sentence names this address as a whole: "12 King St W" isn't named by "112 King St W". */
function names(sentence: string, address: string): boolean {
  for (let i = sentence.indexOf(address); i >= 0; i = sentence.indexOf(address, i + 1)) {
    const before = sentence[i - 1] ?? " ";
    const after = sentence[i + address.length] ?? " ";
    if (!/[A-Za-z0-9-]/.test(before) && !/[A-Za-z0-9]/.test(after)) return true;
  }
  return false;
}

/**
 * Splits after . ! or ? followed by a space and then a capital, a digit or an opening quote. Never inside a
 * quote (a question may hold two sentences: “Do you have pets? What kind?”), inside the place's own address
 * ("100 Queen St. W", "12-26 Rosseau Rd."), or after a street number's Saint ("64 St. Clair").
 */
function sentences(text: string, address: string): string[] {
  const own: [number, number][] = [];
  for (let i = text.indexOf(address); i >= 0; i = text.indexOf(address, i + 1)) own.push([i, i + address.length]);
  const out: string[] = [];
  let start = 0;
  let quoted = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (c === "“") quoted = true;
    else if (c === "”") quoted = false;
    if (quoted || (c !== "." && c !== "!" && c !== "?") || !/\s/.test(text[i + 1] ?? "")) continue;
    let j = i + 1;
    while (j < text.length && /\s/.test(text[j])) j++;
    if (!/[A-Z0-9“"]/.test(text[j] ?? "")) continue;
    if (own.some(([a, b]) => i >= a && i < b - 1)) continue;
    if (c === "." && /\b\d+[A-Za-z]?\s+St$/.test(text.slice(Math.max(start, i - 12), i))) continue;
    out.push(text.slice(start, i + 1).trim());
    start = i + 1;
  }
  const rest = text.slice(start).trim();
  if (rest) out.push(rest);
  return out;
}
