import type { MyListing } from "./api";

/** The latest results: the listings Landline called in the renter's most recent session. The API sends the
 *  newest call first, so that session is the first listing's. */
export function latest(listings: MyListing[]): MyListing[] {
  const session = listings[0]?.session_id;
  return listings.filter((x) => x.session_id === session);
}
