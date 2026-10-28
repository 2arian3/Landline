import type { Card, ListingDetail, Provenance } from "./types";

/** The subway fields the API's listing already carries (server contracts/listing.py), not yet in types.ts. */
export type WithTransit = ListingDetail & { transit_station?: string | null; transit_walk_min?: number | null };

/** One "Getting around" line and where its number came from. */
export type AroundLine = { key: string; text: string; source: string | null };

const COMMUTE = /\bmin by TTC\b/i;   // D-10c's card reasons: "24 min by TTC to King and Bay"

/**
 * What's around a listing, as the renter asks it: the subway, the commute, then the shops (D-10d). A value
 * that isn't there leaves its line out; nothing is guessed.
 */
export function gettingAround(d: WithTransit | null, card?: Card): AroundLine[] {
  const lines: AroundLine[] = [];
  if (d?.transit_station && d.transit_walk_min != null) {
    const text =
      d.transit_walk_min < 1
        ? `Next to ${d.transit_station} station`
        : `${walk(d.transit_walk_min)} to ${d.transit_station} station`;
    lines.push({ key: "transit", text, source: sourceOf(d.provenance?.transit_walk_min) });
  }
  for (const reason of card?.reasons ?? []) {
    if (COMMUTE.test(reason)) lines.push({ key: `commute:${reason}`, text: upper(reason), source: "Google Maps" });
  }
  if (d?.pharmacy_name && d.pharmacy_walk_min != null) {
    lines.push({
      key: "pharmacy",
      text: `${d.pharmacy_name}, ${walk(d.pharmacy_walk_min)}`,
      source: sourceOf(d.provenance?.pharmacy_walk_min ?? d.provenance?.pharmacy_name),
    });
  }
  if (d?.grocery_name && d.grocery_walk_min != null) {
    lines.push({
      key: "grocery",
      text: `${d.grocery_name}, ${walk(d.grocery_walk_min)}`,
      source: sourceOf(d.provenance?.grocery_walk_min ?? d.provenance?.grocery_name),
    });
  }
  return lines;
}

/**
 * Where a getting-around number came from, in the renter's words: "Google Maps, 28 Oct" for Google's
 * times (D-10a, D-10b), "Estimated" for a number worked out from the straight-line distance, and nothing
 * when the record doesn't say.
 */
export function sourceOf(p?: Provenance | null): string | null {
  if (!p) return null;
  if (/google/i.test(JSON.stringify(p))) {
    const day = p.at ? fetchDay(p.at) : null;
    return day ? `Google Maps, ${day}` : "Google Maps";
  }
  return p.source === "derived" || p.source === "synthetic" ? "Estimated" : null;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "28 Oct" from the record's own date. The pipeline writes it with no time zone ("2026-10-28T00:00:00"),
 * so it is read as written: parsed as a time, a browser east of Toronto would show the day before. */
function fetchDay(at: string): string | null {
  const m = /^\d{4}-(\d{2})-(\d{2})/.exec(at);
  return m ? `${Number(m[2])} ${MONTHS[Number(m[1]) - 1]}` : null;
}

/** "6 min walk"; an hour or more in hours ("2 h 20 min walk"), since a third of listings are that far from
 * the subway and "140 min" is hard to take in. */
function walk(minutes: number): string {
  const total = Math.max(1, Math.round(minutes));
  if (total < 60) return `${total} min walk`;
  const rest = total % 60;
  return `${Math.floor(total / 60)} h${rest ? ` ${rest} min` : ""} walk`;
}

function upper(s: string): string {
  return s.charAt(0).toUpperCase() + s.slice(1);
}
