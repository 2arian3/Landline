/**
 * The summary's counts and rows, from each card's one status (lib/status.ts), so the activity line, What
 * the calls found and its tally say what the cards say: "1 on the phone · 1 left a message".
 */
import { type Status, statusOf } from "./status.ts";
import type { SessionView } from "./types.ts";

/** A place's latest call: a callback is a newer call for the same place. */
const latestCall = (view: SessionView, id: string) => view.calls.filter((c) => c.listing_id === id).pop() ?? null;

/** Each card's one status (lib/status.ts), as the card, its pin and the detail show it. */
export const statuses = (view: SessionView) =>
  view.board.map((card) => ({
    card,
    status: statusOf(card, { call: latestCall(view, card.listing_id), planned: view.plan.includes(card.listing_id) }),
  }));

/** How many places stand where, in statusOf's own count words, going first: "1 on the phone · 1 left a
 *  message". A place with no status (ranked, not called) counts for nothing. */
export function tally(rows: { status: Status }[]): { going: string[]; done: [Status["pin"], string][] } {
  const counts = new Map<string, { pin: Status["pin"]; n: number; going: boolean }>();
  for (const { status } of rows) {
    if (!status.words || status.kind === "will_call") continue;
    const going = GOING.has(status.kind);
    counts.set(status.tally, { pin: status.pin, n: (counts.get(status.tally)?.n ?? 0) + 1, going });
  }
  const line = (word: string, n: number) => `${n} ${n > 1 ? word.replace(/^viewing /, "viewings ") : word}`;
  const all = [...counts];
  return {
    going: all.filter(([, c]) => c.going).map(([word, c]) => line(word, c.n)),
    done: all.filter(([, c]) => !c.going).map(([word, { pin, n }]) => [pin, line(word, n)]),
  };
}

const GOING = new Set<Status["kind"]>(["on_phone", "ringing", "queued"]);

/** The places the renter's calls were about, a booked viewing first, then in the plan's order, then any
 *  other place a call was to (a callback). */
export function calledPlaces(view: SessionView) {
  const ids = [...view.plan, ...view.calls.map((c) => c.listing_id).filter((id) => !view.plan.includes(id))];
  const booked = (s: Status) => (s.pin === "booked" || s.pin === "confirmed" ? 0 : 1);
  return statuses(view)
    .filter(({ card }) => ids.includes(card.listing_id))
    .sort((a, b) => booked(a.status) - booked(b.status) || ids.indexOf(a.card.listing_id) - ids.indexOf(b.card.listing_id));
}
