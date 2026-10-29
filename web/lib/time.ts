/**
 * The one way the board writes a time: "Sat, Oct 31 · 2:00 PM", in Toronto, from the server's `starts_at`.
 * Never relative ("in 2 days"): the page can't back that up once it's been open a while. The card, the pin,
 * the detail and the results all use it, so a viewing reads the same everywhere.
 */

const TORONTO = "America/Toronto";
const NBSP = " ";

const PARTS = new Intl.DateTimeFormat("en-US", {
  timeZone: TORONTO,
  weekday: "short",
  month: "short",
  day: "numeric",
  hour: "numeric",
  minute: "2-digit",
  hourCycle: "h23",
});

/**
 * "Sat, Oct 31 · 2:00 PM" for an ISO time, or null when it doesn't parse. The date and the time each hold
 * together (no-break spaces), so a line can only wrap after the "·".
 */
export function formatWhen(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const t = new Date(iso);
  if (Number.isNaN(t.getTime())) return null;
  const parts = PARTS.formatToParts(t);
  const get = (type: Intl.DateTimeFormatPartTypes) => parts.find((p) => p.type === type)?.value ?? "";
  const hour = Number(get("hour"));
  const clock = `${hour % 12 || 12}:${get("minute")}${NBSP}${hour >= 12 ? "PM" : "AM"}`;
  return `${get("weekday")},${NBSP}${get("month")}${NBSP}${get("day")}${NBSP}· ${clock}`;
}

/** A viewing's time in the board's words; the agent's own words ("Saturday at 2 pm") when it doesn't parse. */
export function viewingWhen(v: { starts_at: string; slot_text: string }): string {
  return formatWhen(v.starts_at) ?? v.slot_text;
}

/** "6:43 PM" in Toronto, for when a quote was said; null when the time doesn't parse. */
export function formatClock(iso: string | null | undefined): string | null {
  const when = formatWhen(iso);
  return when ? when.slice(when.indexOf("· ") + 2) : null;
}
