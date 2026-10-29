import { createContext, type ReactNode, useContext } from "react";

import { getListings, type MyListing } from "./api";
import { useLoad } from "./useLoad";

type Listings = ReturnType<typeof useLoad<MyListing[]>>;

const ListingsContext = createContext<Listings | null>(null);

/** The renter's listings, loaded once for every tab under it: Home and My listings show the same copy, and a
 *  return to the app fetches /api/me/listings once, not once per tab. */
export function ListingsProvider({ children }: { children: ReactNode }) {
  return <ListingsContext.Provider value={useLoad(getListings)}>{children}</ListingsContext.Provider>;
}

/** The shared listings load (ListingsProvider, in the tabs' layout). */
export function useListings(): Listings {
  const listings = useContext(ListingsContext);
  if (!listings) throw new Error("useListings needs a ListingsProvider above it");
  return listings;
}
