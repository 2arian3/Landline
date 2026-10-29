// What a listing call heard, in the words the renter reads on the web's /me (web/lib/heard.ts): keep the two
// in step, so the app and the page say the same thing.

const LABEL: Record<string, string> = {
  rent: "rent", fees: "fees", parking: "parking", parking_cost: "parking", pets: "pets", laundry: "laundry",
  ac: "AC", utilities_included: "utilities", furnished: "furnished", lease_months: "lease", sqft: "size",
  floor: "floor", light: "light", elevator: "elevator", step_free: "step-free", outdoor_space: "outdoor space",
  other: "also",
};

/** A heard fact under a plain label, in sentence case: "Parking: $150 a month", "Free from November 1". Only
 *  the first letter changes, so the Auditor's "Dec 1" stays. */
export function factLine(field: string, value: string): string {
  const v = value.trim().replace(/\.$/, "");
  if (field === "available") return sentence(v);                           // "Still available"
  if (field === "available_from") return v.toLowerCase() === "now" ? "Available now" : `Free from ${v}`;
  if (field === "fees" && v.startsWith("no other")) return sentence(v);   // "No other monthly fees"
  return `${sentence(LABEL[field] ?? field.replaceAll("_", " "))}: ${v}`;
}

function sentence(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/** How a call ended, in a few words. */
export const WENT: Record<string, string> = {
  talked: "Talked to the agent",
  voicemail: "Left a message",
  no_answer: "No answer",
};

/** A viewing's status, as a word. */
export const VIEWING: Record<string, string> = {
  booked: "Viewing booked",
  confirmed: "Viewing confirmed",
  declined: "Viewing declined",
};

/** A viewing in one line, with its time once: "Viewing booked: Thu, Oct 29, 6:30 p.m.". The time is the code's
 *  (starts_at, in Toronto time); the agent's own words (slot_text) only when there's no time to show. */
export function viewingLine(v: { starts_at?: string | null; slot_text?: string | null; status: string }): string {
  const word = VIEWING[v.status] ?? `Viewing ${v.status}`;
  const at = (v.starts_at && when(v.starts_at)) || v.slot_text?.trim();
  return at ? `${word}: ${at}` : word;
}

/** A moment in Toronto time, e.g. "Sat, Oct 24, 1:12 a.m.", as the web's /me writes it (web/lib/admin.ts). */
export function when(iso: string): string {
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return "";
  const options: Intl.DateTimeFormatOptions = {
    weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit",
  };
  try {
    return new Intl.DateTimeFormat("en-CA", { ...options, timeZone: "America/Toronto" }).format(at);
  } catch {
    return new Intl.DateTimeFormat("en-CA", options).format(at);   // a phone without time zone data
  }
}

/** +14165550123 as +1 416 555 0123, as the web writes numbers (web/lib/admin.ts). */
export function phoneLabel(e164: string): string {
  const m = /^\+1(\d{3})(\d{3})(\d{4})$/.exec(e164);
  return m ? `+1 ${m[1]} ${m[2]} ${m[3]}` : e164;
}

const NBSP = " ";

/**
 * The board's one way to write a time, "Sat, Oct 31 · 2:00 PM" in Toronto (web/lib/time.ts's formatWhen: keep
 * the two in step). Null when it doesn't parse; a phone whose JS engine can't split a date into parts gets
 * `when`'s wording instead.
 */
export function formatWhen(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const t = new Date(iso);
  if (Number.isNaN(t.getTime())) return null;
  try {
    const parts = new Intl.DateTimeFormat("en-US", {
      timeZone: "America/Toronto", weekday: "short", month: "short", day: "numeric",
      hour: "numeric", minute: "2-digit", hourCycle: "h23",
    }).formatToParts(t);
    const get = (type: Intl.DateTimeFormatPartTypes) => parts.find((p) => p.type === type)?.value ?? "";
    const hour = Number(get("hour"));
    const clock = `${hour % 12 || 12}:${get("minute")}${NBSP}${hour >= 12 ? "PM" : "AM"}`;
    return `${get("weekday")},${NBSP}${get("month")}${NBSP}${get("day")}${NBSP}· ${clock}`;
  } catch {
    return when(iso) || null;
  }
}

/** A viewing as the board's statusOf says it (web/lib/status.ts): booked in ink, waiting on the agent;
 *  confirmed in green; declined in red. The time is the code's; the agent's words only when there's none. */
export function viewingStatus(v: { starts_at?: string | null; slot_text?: string | null; status: string }): {
  words: string;
  sub: string | null;
  tone: "ink" | "real" | "dead";
} {
  const at = formatWhen(v.starts_at) ?? (v.slot_text?.trim() || null);
  if (v.status === "confirmed") return { words: at ? `Viewing confirmed · ${at}` : "Viewing confirmed", sub: null, tone: "real" };
  if (v.status === "declined") {
    return { words: at ? `The agent can’t do ${at}` : "The agent can’t make the viewing", sub: null, tone: "dead" };
  }
  return { words: at ? `Viewing ${at}` : "Viewing booked", sub: "Waiting for the agent to confirm", tone: "ink" };
}
