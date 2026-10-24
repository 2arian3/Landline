import type { Card } from "./types";

/** What a finished call settled for one place, beyond its facts: a booked viewing, questions for the renter. */
export type CallNotes = {
  viewing: string | null; // when, in words: "Saturday at 2 pm"
  questions: string[]; // what the listing agent asked that only the renter can answer
};

/**
 * The one place that reads a place's booked viewing and noted questions. The contract doesn't carry them
 * per card yet (#38 adds CallView.outcome), so for now they come from the session's results text: the
 * Narrator's ("I booked 30 Carabob Court for Saturday at 2 pm.") or the ?dev=1 demo's ("…, inside your
 * Saturday afternoon."). No lookbehind anywhere: Safari before 16.4 can't parse it.
 */
export function callNotes(card: Card | undefined, results: string | null | undefined): CallNotes {
  const said = card && results ? sentencesAbout(results, card.address) : [];
  return {
    viewing: said.map((s) => BOOKED.exec(s)?.[1]).find(Boolean) ?? null,
    questions: said.flatMap((s) => (s.includes(" asked, ") ? (/“([^”]+)”/.exec(s)?.slice(1) ?? []) : [])),
  };
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
