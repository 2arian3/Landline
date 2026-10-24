import type { CallView, Card } from "./types";

/** What a finished call settled for one place, beyond its facts: a booked viewing, questions for the renter. */
export type CallNotes = {
  viewing: string | null; // when, in words: "Saturday at 2 pm"
  questions: string[]; // what the listing agent asked that only the renter can answer
};

/**
 * The one place that reads a place's booked viewing and noted questions. They come from the call's
 * outcome (`CallView.outcome`, the Auditor's CallOutcome for this listing) when the session has one.
 * Otherwise, from the sentences of the session's results text that name the place: that fallback breaks
 * if the Narrator's wording changes or the results are in another language, so it's only a fallback.
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
    viewing: said.map((s) => /^I booked .+ for (.+?), inside /.exec(s)?.[1]).find(Boolean) ?? null,
    questions: said.flatMap((s) => (s.includes(" asked, ") ? (/“([^”]+)”/.exec(s)?.slice(1) ?? []) : [])),
  };
}

/** "Saturday at 2 pm", Toronto time: the words the Narrator uses for a booked viewing (demo.py `_when`). */
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

/**
 * The sentences of a results text that name this place. Splits after . ! or ? but not after "St." (64 St.
 * Clair), and not inside a quoted question, so each sentence and each address stays whole.
 */
function sentencesAbout(text: string, address: string): string[] {
  return text.split(/(?<=[.!?])(?<!\bSt\.)\s+(?=[A-Z0-9“"])/).filter((s) => s.includes(address));
}
