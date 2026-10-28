import { useAuth } from "@clerk/expo";
import { useCallback, useEffect, useRef, useState } from "react";
import { AppState } from "react-native";

import { ApiError } from "./api";
import { latestWins, type Loaded } from "./loads";

/**
 * What a screen shows, fetched with the renter's Clerk token once they're signed in, again whenever the app
 * comes back to the front (from the dialer, or from the text saying the calls are done), and on `refresh`
 * (pull to refresh). `fetcher` must be a module-level function, so it doesn't refetch on every render.
 */
export function useLoad<T>(fetcher: (token: string | null) => Promise<T>) {
  const { isSignedIn, userId, getToken } = useAuth();
  const [{ data, error }, setState] = useState<Loaded<T>>({ data: null, error: null });
  const [run] = useState(() => latestWins<T>(setState, (e) =>
    e instanceof ApiError ? e.message : "Something went wrong. Try again."));
  const [refreshing, setRefreshing] = useState(false);

  // @clerk/expo's useAuth returns a new getToken on every render, and every load sets state, so a load that
  // depended on getToken would re-render, reload and loop. Keep the latest one in a ref, and key the loads
  // on who is signed in instead.
  const token = useRef(getToken);
  useEffect(() => {
    token.current = getToken;
  });

  const load = useCallback(() => run(async () => fetcher(await token.current())), [run, fetcher]);

  useEffect(() => {
    if (!isSignedIn) return;
    void load();
    const back = AppState.addEventListener("change", (now) => {
      if (now === "active") void load();
    });
    return () => back.remove();
  }, [isSignedIn, userId, load]);

  const refresh = useCallback(async () => {
    setRefreshing(true);
    await load();
    setRefreshing(false);
  }, [load]);

  return { data, error, load, refresh, refreshing };
}
