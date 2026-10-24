import type { Preferences, SessionView } from "./types";

const CAD = new Intl.NumberFormat("en-CA", {
  style: "currency",
  currency: "CAD",
  currencyDisplay: "narrowSymbol",
  maximumFractionDigits: 0,
});

export const money = (n: number) => CAD.format(n);

export function bedsLabel(beds: number): string {
  if (beds === 0) return "Studio";
  const n = Number.isInteger(beds) ? beds : beds.toFixed(1);
  return `${n} bed`;
}

export function bathsLabel(baths?: number | null): string | null {
  if (baths == null) return null;
  return `${Number.isInteger(baths) ? baths : baths.toFixed(1)} bath`;
}

export function sizeLabel(sqft?: number | null): string | null {
  return sqft ? `${sqft.toLocaleString("en-CA")} sq ft` : null;
}

export function specLine(x: { beds: number; baths?: number | null; sqft?: number | null }): string {
  return [bedsLabel(x.beds), bathsLabel(x.baths), sizeLabel(x.sqft)].filter(Boolean).join(" · ");
}

const UNIT: Record<string, string> = {
  condo: "Condo",
  apartment: "Apartment",
  house: "House",
  townhouse: "Townhouse",
  basement: "Basement",
  room: "Room",
  other: "Rental",
};
export const unitLabel = (t?: string | null) => (t ? UNIT[t] ?? t : null);

/** What Landline heard, as one plain sentence fragment per preference. */
export function heardLine(p: Preferences): string[] {
  const out: string[] = [];
  if (p.max_monthly) out.push(`up to ${money(p.max_monthly)} a month`);
  if (p.min_beds != null) out.push(p.min_beds === 0 ? "studio or bigger" : `${p.min_beds}+ bedroom`);
  if (p.areas.length) out.push(`around ${p.areas.join(" or ")}`);
  if (p.pets.length) out.push(`with a ${p.pets.join(" and a ")}`);
  if (p.must_have.length) out.push(`must have ${p.must_have.join(", ")}`);
  if (p.priorities.length) out.push(`${p.priorities.join(", then ")} first`);
  return out;
}

/**
 * Nothing heard yet: a session that opened and that the renter hasn't said anything to. Until the first
 * preference lands the page shows no match framing at all (no "places fit", no numbered pins or cards),
 * just the map and Landline's opening sentence.
 */
export function nothingHeard(view: SessionView): boolean {
  const p = view.preferences;
  const said = p.max_monthly != null || p.min_beds != null || p.move_in || p.occupants != null ||
    [p.areas, p.anchors, p.pets, p.must_have, p.priorities].some((list) => list?.length);
  return !said && view.phase === "open" && !view.plan.length && !view.board.some((c) => c.reaction);
}

export const sourceName = (s: string) => (s === "livrent" ? "liv.rent" : s);

/**
 * A liv.rent photo cropped to `px` square. Their CDN resizes on the fly (cdn.liv.rent/<w>x<h>/…),
 * so an 84 px thumbnail doesn't download the 800 px original. Other hosts come back unchanged.
 */
export function photoAt(url: string, px: number): string {
  return url.replace(/^(https:\/\/cdn\.liv\.rent\/)\d+x\d*\//, `$1${px}x${px}/`);
}
