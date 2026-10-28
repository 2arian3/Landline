/** What a screen shows while it loads: the last data it got, and why the last load failed. */
export type Loaded<T> = { data: T | null; error: string | null };

/**
 * Runs a screen's loads and reports each change to `show`. Only the newest load's answer counts: one that
 * started earlier and finishes later is dropped, so a slow first load can't overwrite a pull to refresh.
 * A failed load keeps the data last shown, with `explain`'s words for the error.
 */
export function latestWins<T>(show: (state: Loaded<T>) => void, explain: (e: unknown) => string) {
  let state: Loaded<T> = { data: null, error: null };
  let newest = 0;
  const set = (next: Partial<Loaded<T>>) => {
    state = { ...state, ...next };
    show(state);
  };
  return async (fetch: () => Promise<T>): Promise<void> => {
    const mine = ++newest;
    set({ error: null });
    try {
      const data = await fetch();
      if (mine === newest) set({ data });
    } catch (e) {
      if (mine === newest) set({ error: explain(e) });
    }
  };
}
