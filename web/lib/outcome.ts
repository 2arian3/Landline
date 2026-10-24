import type { Card } from "./types";

/** What a finished call settled for one place, beyond its facts: a booked viewing, questions for the renter. */
export type CallNotes = {
  viewing: string | null; // when, in words: "Saturday at 2 pm"
  questions: string[]; // what the listing agent asked that only the renter can answer
};

/**
 * The one place that reads a place's booked viewing and noted questions. The contract doesn't carry them
 * per card yet (PR #13 asks for Card.viewing and Card.open_questions, or facts named viewing and
 * question), so for now they come from the sentences of the session's results text that name the place.
 * When the fields land, only this function changes.
 */
export function callNotes(card: Card | undefined, results: string | null | undefined): CallNotes {
  const said = card && results ? sentencesAbout(results, card.address) : [];
  return {
    viewing: said.map((s) => /^I booked .+ for (.+?), inside /.exec(s)?.[1]).find(Boolean) ?? null,
    questions: said.flatMap((s) => (s.includes(" asked, ") ? (/“([^”]+)”/.exec(s)?.slice(1) ?? []) : [])),
  };
}

/**
 * The sentences of a results text that name this place. Splits after . ! or ? but not after "St." (64 St.
 * Clair), and not inside a quoted question, so each sentence and each address stays whole.
 */
function sentencesAbout(text: string, address: string): string[] {
  return text.split(/(?<=[.!?])(?<!\bSt\.)\s+(?=[A-Z0-9“"])/).filter((s) => s.includes(address));
}
