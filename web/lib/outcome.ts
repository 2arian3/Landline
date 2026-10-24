import type { CallView, Card } from "./types";

/** What a finished call settled for one place, beyond its facts: a booked viewing, questions for the renter. */
export type CallNotes = {
  viewing: string | null; // when, in words: "Saturday at 2 pm"
  questions: string[]; // what the listing agent asked that only the renter can answer
};

/**
 * The one place that reads a place's booked viewing and noted questions. They come from the call's
 * outcome (`CallView.outcome`, the Auditor's CallOutcome for this listing) when the session has one.
 * Otherwise, from the session's results text: the Narrator's ("I booked 30 Carabob Court for Saturday at
 * 2 pm.") or the ?dev=1 demo's ("…, inside your Saturday afternoon."). That fallback breaks if the wording
 * changes or the results are in another language, so it's only a fallback. No lookbehind anywhere: Safari
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
  const said = card && results ? sentencesAbout(results, card.address) : [];
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
const BOOKED = /^I booked .+ for (.+?)(?:, inside [^.]*)?\.$/;
// A street address: "701 Don Mills Rd", "190-200 Exbury Road", "12B Main St". Times ("2 pm") and amounts
// ("$180 a month", "12-month") have no capital after the number.
const PLACE = /\b\d+[A-Za-z]?(?:-\d+)?\s+[A-Z]/;

/**
 * The sentences about this place: from one that names it up to one that names another place. The ones in
 * between name no place and belong to it, like the Narrator's "Its agent asked, …" or "It moves up to first."
 */
function sentencesAbout(text: string, address: string): string[] {
  let mine = false;
  return sentences(text).filter((s) => {
    if (s.includes(address)) mine = true;
    else if (PLACE.test(s)) mine = false;
    return mine;
  });
}

/**
 * Splits after . ! or ? followed by a space and a capital, a digit or an opening quote. Not inside a quoted
 * question (“…?” is followed by a quote mark, not a space), and not after the "St." of a street number's
 * Saint ("64 St. Clair"), while "4 Main St. Nobody…" still splits.
 */
function sentences(text: string): string[] {
  const out: string[] = [];
  let start = 0;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if ((c !== "." && c !== "!" && c !== "?") || !/\s/.test(text[i + 1] ?? "")) continue;
    if (!/[A-Z0-9“"]/.test(text.slice(i + 1).trimStart()[0] ?? "")) continue;
    if (c === "." && /\b\d+[A-Za-z]?\s+St$/.test(text.slice(start, i))) continue;
    out.push(text.slice(start, i + 1).trim());
    start = i + 1;
  }
  const rest = text.slice(start).trim();
  if (rest) out.push(rest);
  return out;
}
