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

/** How many places stand where, in the cards' own words, going first: "1 on the phone · 1 left a message". */
export function tally(rows: { status: Status }[]): { going: string[]; done: [Status["pin"], string][] } {
  const counts = new Map<string, { pin: Status["pin"]; n: number }>();
  for (const { status } of rows) {
    const key = TALLY[status.kind] ?? (status.kind === "unheard" ? status.words.toLowerCase() : null);
    if (!key) continue;
    counts.set(key, { pin: status.pin, n: (counts.get(key)?.n ?? 0) + 1 });
  }
  const line = (key: string, n: number) => `${n} ${n > 1 ? key.replace(/^viewing /, "viewings ") : key}`;
  return {
    going: [...GOING].filter((key) => counts.has(key)).map((key) => line(key, counts.get(key)?.n ?? 0)),
    done: [...counts].filter(([key]) => !GOING.has(key)).map(([key, { pin, n }]) => [pin, line(key, n)]),
  };
}

const TALLY: Partial<Record<Status["kind"], string>> = {
  on_phone: "on the phone",
  ringing: "ringing",
  queued: "queued",
  booked: "viewing booked",
  confirmed: "viewing confirmed",
  declined: "viewing declined",
  checked: "checked by phone",
  flagged: "flagged",
  gone: "already rented",
};
const GOING = new Set(["on the phone", "ringing", "queued"]);

/** The places the renter's calls were about, a booked viewing first, then in the plan's order, then any
 *  other place a call was to (a callback). */
export function calledPlaces(view: SessionView) {
  const ids = [...view.plan, ...view.calls.map((c) => c.listing_id).filter((id) => !view.plan.includes(id))];
  const booked = (s: Status) => (s.pin === "booked" || s.pin === "confirmed" ? 0 : 1);
  return statuses(view)
    .filter(({ card }) => ids.includes(card.listing_id))
    .sort((a, b) => booked(a.status) - booked(b.status) || ids.indexOf(a.card.listing_id) - ids.indexOf(b.card.listing_id));
}
