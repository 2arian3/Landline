/**
 * Whether the board still owes itself a snapshot. The stream announces each new version; a fetch that was
 * already on its way when one arrived can bring back the version before it. Without asking again the board
 * would sit on that one for good: the stream's pings keep it "heard", so nothing falls back to polling.
 */

/** The newest version the stream has announced, given one more announcement. */
export function announced(wanted: number, version: number): number {
  return Number.isFinite(version) ? Math.max(wanted, version) : wanted;
}

/** Whether a fetch is still owed: the stream announced a version newer than the one on screen. */
export function owed(wanted: number, shown: number | null | undefined): boolean {
  return wanted > (shown ?? 0);
}
