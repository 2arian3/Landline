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
