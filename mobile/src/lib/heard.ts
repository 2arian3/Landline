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
