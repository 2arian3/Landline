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
 *  "3 places called", "1 viewing confirmed". A place called twice counts once, by its newest call. */
export function tally(listings: MyListing[]): { kind: "called" | "confirmed" | "booked"; words: string }[] {
  const newest = new Map<string, MyListing>();
  for (const x of listings) if (!newest.has(x.listing_id)) newest.set(x.listing_id, x);
  const places = newest.size;
  const viewings = (status: "confirmed" | "booked") => {
    const n = [...newest.values()].filter((x) => x.viewing?.status === status).length;
    return n ? [{ kind: status, words: `${n} ${n === 1 ? "viewing" : "viewings"} ${status}` }] : [];
  };
  return [
    { kind: "called" as const, words: `${places} ${places === 1 ? "place" : "places"} called` },
    ...viewings("confirmed"),
    ...viewings("booked"),
  ];
}
