/** Where an opening value goes once it has changed: nothing the server sends equals it. */
export const CHANGED = Symbol("changed");

/** What the page opened with: undefined until the first view, then that value, then CHANGED for good. */
export type Opened<T> = { current: T | typeof CHANGED | undefined };

/**
 * Whether the page follows a server value (the place Landline focused, the area the renter named): not
 * while it's still what the page opened with, which the board's own framing covers, and always once it
 * has changed since, back to that opening value too.
 */
export function follows<T>(opened: Opened<T>, now: T): boolean {
  if (opened.current === undefined) opened.current = now;
  else if (opened.current !== CHANGED && opened.current !== now) opened.current = CHANGED;
  return opened.current === CHANGED;
}
