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
