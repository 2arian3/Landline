import type { CardViewing } from "./types";

/**
 * A card's viewing in words, with its status colour (O-08e): booked and waiting for the listing agent,
 * confirmed by them (O-08c), or turned down.
 */
export function viewingStatus(v: CardViewing): { word: string; color: string } {
  const when = viewingTime(v);
  if (v.status === "confirmed") return { word: `Viewing ${when} · confirmed`, color: "var(--color-real)" };
  if (v.status === "declined") return { word: `The agent can't do ${when}`, color: "var(--color-dead)" };
  return { word: `Viewing ${when} · waiting for the agent`, color: "var(--color-ink-2)" };
}

/** "Sat 2:00 PM" in Toronto time; the agent's own words ("Saturday at 2 pm") when the time doesn't parse. */
export function viewingTime(v: CardViewing): string {
  const t = new Date(v.starts_at);
  if (Number.isNaN(t.getTime())) return v.slot_text;
  return t.toLocaleString("en-US", { weekday: "short", hour: "numeric", minute: "2-digit", timeZone: "America/Toronto" });
}
