import type { MyListing } from "./api";

/** The latest results: the listings Landline called in the renter's most recent session. The API sends the
 *  newest call first, so that session is the first listing's. */
export function latest(listings: MyListing[]): MyListing[] {
  const session = listings[0]?.session_id;
  return listings.filter((x) => x.session_id === session);
}

/** Dollars as the web writes them (web/lib/format.ts's money): "$2,450". Without Intl's currency style, which
 *  the phone's JS engine may not have. */
export function money(n: number): string {
  return `$${Math.round(n).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ",")}`;
}

/** Home's tally, counted from the same listings and viewing statuses the cards show, so the two can't disagree:
 *  "3 places called", "1 viewing confirmed". A place called twice counts once, with the newest viewing any of
 *  its calls has (a later call that reached nobody doesn't lose it). */
export function tally(listings: MyListing[]): { kind: "called" | "confirmed" | "booked"; words: string }[] {
  const places = new Set<string>();
  const viewing = new Map<string, string>();
  for (const x of listings) {   // newest call first
    places.add(x.listing_id);
    if (x.viewing && !viewing.has(x.listing_id)) viewing.set(x.listing_id, x.viewing.status);
  }
  const viewings = (status: "confirmed" | "booked") => {
    const n = [...viewing.values()].filter((s) => s === status).length;
    return n ? [{ kind: status, words: `${n} ${n === 1 ? "viewing" : "viewings"} ${status}` }] : [];
  };
  return [
    { kind: "called" as const, words: `${places.size} ${places.size === 1 ? "place" : "places"} called` },
    ...viewings("confirmed"),
    ...viewings("booked"),
  ];
}
