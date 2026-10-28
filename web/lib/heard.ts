/** A fact a listing call heard, as the renter reads it on /me: the Auditor's words, under a plain label. */
const LABEL: Record<string, string> = {
  rent: "rent", fees: "fees", parking: "parking", parking_cost: "parking", pets: "pets", laundry: "laundry",
  ac: "AC", utilities_included: "utilities", furnished: "furnished", lease_months: "lease", sqft: "size",
  floor: "floor", light: "light", elevator: "elevator", step_free: "step-free", outdoor_space: "outdoor space",
  other: "also",
};

export function factLine(field: string, value: string): string {
  const v = value.trim().replace(/\.$/, "");
  if (field === "available") return v;                                     // "still available"
  if (field === "available_from") return v.toLowerCase() === "now" ? "available now" : `free from ${v}`;
  if (field === "fees" && v.startsWith("no other")) return v;             // "no other monthly fees"
  return `${LABEL[field] ?? field.replaceAll("_", " ")}: ${v}`;
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
  const at = (v.starts_at && torontoTime(v.starts_at)) || v.slot_text?.trim();
  return at ? `${word}: ${at}` : word;
}

const AT = new Intl.DateTimeFormat("en-CA", {
  timeZone: "America/Toronto",
  weekday: "short",
  day: "numeric",
  month: "short",
  hour: "numeric",
  minute: "2-digit",
});

/** As admin.ts's when() writes it, or "" for a time that doesn't parse. Kept here, not imported, so this file
 *  loads on its own in the app's tests (mobile/tests/lib.test.mjs). */
function torontoTime(iso: string): string {
  const at = new Date(iso);
  return Number.isNaN(at.getTime()) ? "" : AT.format(at);
}
