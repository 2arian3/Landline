import { useAuth } from "@clerk/expo";
import { useCallback, useEffect, useState } from "react";

import { ApiError } from "./api";

/**
 * What a screen shows, fetched with the renter's Clerk token once they're signed in, and again on `refresh`
 * (pull to refresh). `fetcher` must be a module-level function, so it doesn't refetch on every render.
 * A failed reload keeps what was last shown, with the error in plain words.
 */
export function useLoad<T>(fetcher: (token: string | null) => Promise<T>) {
  const { isSignedIn, getToken } = useAuth();
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    setError(null);
    try {
      setData(await fetcher(await getToken()));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Something went wrong. Try again.");
    }
  }, [fetcher, getToken]);

  useEffect(() => {
    if (isSignedIn) void load();
  }, [isSignedIn, load]);

  const refresh = useCallback(async () => {
    setRefreshing(true);
    await load();
    setRefreshing(false);
  }, [load]);

  return { data, error, load, refresh, refreshing };
}
