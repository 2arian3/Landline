/**
 * What the card shows of what a call heard (the canvas's Card board): facts named by their subject
 * ("Parking: $150 a month", not "Agent heard: yes"), the newest one per subject, three at most; the rest are
 * in the detail. Each keeps its quote there; the newest quote shows on the card on the projector.
 */
import type { Fact } from "./types.ts";

/** Icon names (components/Icon.tsx) for a fact's subject. */
export type FactGlyph =
  | "circle-check"
  | "calendar"
  | "square-parking"
  | "paw-print"
  | "receipt"
  | "plug"
  | "washing-machine"
  | "message-circle";

export type CardFact = {
  key: string;
  glyph: FactGlyph;
  /** "Parking", shown bold before the value; null when the value says it all ("Still available"). */
  label: string | null;
  text: string;
};

// Subjects in the order a renter asks about them; parking's cost and whether there is any are one subject.
const SUBJECTS: Record<string, { key: string; glyph: FactGlyph; label: string | null }> = {
  available: { key: "available", glyph: "circle-check", label: null },
  available_from: { key: "available", glyph: "calendar", label: null },
  parking: { key: "parking", glyph: "square-parking", label: "Parking" },
  parking_cost: { key: "parking", glyph: "square-parking", label: "Parking" },
  fees: { key: "fees", glyph: "receipt", label: "Fees" },
  pets: { key: "pets", glyph: "paw-print", label: "Pets" },
  utilities_included: { key: "utilities", glyph: "plug", label: "Utilities" },
  laundry: { key: "laundry", glyph: "washing-machine", label: "Laundry" },
};
const ORDER = ["available", "parking", "fees", "pets", "utilities", "laundry"];

/** The facts for the card: the newest per subject, in the renter's order, `most` at most. */
export function cardFacts(facts: Fact[], most = 3): CardFact[] {
  const newest = new Map<string, Fact>();
  for (const f of byTime(facts)) newest.set(subject(f).key, f);   // later ones replace earlier ones
  const keys = [...newest.keys()].sort((a, b) => rank(a) - rank(b));
  return keys.slice(0, most).map((k) => {
    const f = newest.get(k)!;
    const s = subject(f);
    const v = f.value.trim().replace(/\.$/, "");
    if (f.field === "available") return { key: k, glyph: s.glyph, label: null, text: sentence(v) };
    if (f.field === "available_from") {
      return { key: k, glyph: s.glyph, label: null, text: v.toLowerCase() === "now" ? "Available now" : `Free from ${v}` };
    }
    if (f.field === "fees" && /^no other/i.test(v)) return { key: k, glyph: s.glyph, label: null, text: sentence(v) };
    // "Parking is $180 a month" already names its subject: no "Parking:" before it.
    if (!s.label || v.toLowerCase().startsWith(s.label.toLowerCase())) {
      return { key: k, glyph: s.glyph, label: null, text: sentence(v) };
    }
    return { key: k, glyph: s.glyph, label: s.label, text: v };
  });
}

/** The fact whose quote was said last, for the card's "their words, verbatim"; null with none. */
export function newestQuote(facts: Fact[]): Fact | null {
  return byTime(facts.filter((f) => f.quote.trim())).at(-1) ?? null;
}

function subject(f: Fact): { key: string; glyph: FactGlyph; label: string | null } {
  return SUBJECTS[f.field] ?? { key: `${f.field}:${f.value}`, glyph: "message-circle", label: null };
}

function rank(key: string): number {
  const i = ORDER.indexOf(key);
  return i < 0 ? ORDER.length : i;
}

/** Oldest first, by when each was said; facts with no time keep their order, before the timed ones. */
function byTime(facts: Fact[]): Fact[] {
  const t = (f: Fact) => (f.at ? Date.parse(f.at) : Number.NaN);
  return facts
    .map((f, i) => ({ f, i, at: t(f) }))
    .sort((a, b) => (Number.isNaN(a.at) ? -Infinity : a.at) - (Number.isNaN(b.at) ? -Infinity : b.at) || a.i - b.i)
    .map((x) => x.f);
}

function sentence(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1);
}
