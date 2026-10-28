// gettingAround: the walk to the subway, the commute and the shops nearby, each with its source (D-10d).
// Run with `npm test`.
import assert from "node:assert/strict";
import { test } from "node:test";
import { gettingAround, sourceOf, type WithTransit } from "./around.ts";
import type { Card } from "./types.ts";

const GOOGLE = { source: "derived", at: "2026-10-28T14:00:00Z", version: "Google Maps walking time" };

function listing(over: Partial<WithTransit> = {}): WithTransit {
  return { listing_id: "t:1", provenance: {}, ...over } as unknown as WithTransit;
}

const FULL = listing({
  transit_station: "Bathurst",
  transit_walk_min: 7.6,
  pharmacy_name: "Shoppers Drug Mart",
  pharmacy_walk_min: 4.2,
  grocery_name: "No Frills",
  grocery_walk_min: 6,
  provenance: { transit_walk_min: GOOGLE, pharmacy_walk_min: GOOGLE, grocery_walk_min: GOOGLE },
});

test("a listing with everything reads the subway, then the shops, each from Google Maps", () => {
  assert.deepEqual(
    gettingAround(FULL).map((l) => [l.text, l.source]),
    [
      ["8 min walk to Bathurst station", "Google Maps, 28 Oct"],
      ["Shoppers Drug Mart, 4 min walk", "Google Maps, 28 Oct"],
      ["No Frills, 6 min walk", "Google Maps, 28 Oct"],
    ],
  );
});

test("a listing with nothing shows nothing, and a name without minutes is left out", () => {
  assert.deepEqual(gettingAround(listing()), []);
  assert.deepEqual(gettingAround(null), []);
  assert.deepEqual(gettingAround(listing({ grocery_name: "No Frills", grocery_walk_min: null })), []);
});

test("the commute comes from the card's TTC reason, after the subway", () => {
  const card = { reasons: ["24 min by TTC to King and Bay", "$310 under budget"] } as unknown as Card;
  assert.deepEqual(
    gettingAround(listing({ transit_station: "Union", transit_walk_min: 0.4 }), card).map((l) => l.text),
    ["Next to Union station", "24 min by TTC to King and Bay"],
  );
});

test("today's straight-line minutes say they're estimated; an unlabelled record says nothing", () => {
  assert.equal(sourceOf({ source: "derived", at: "2026-10-28T14:00:00Z", version: "geo-3" }), "Estimated");
  assert.equal(sourceOf({ source: "scraped" }), null);
  assert.equal(sourceOf(undefined), null);
});

test("production's record, dated with no time zone, reads the day it says wherever the browser is", () => {
  // As the API serves it (D-10a): {"at": "2026-10-28T00:00:00", "source": "derived", "version": "google-maps-v1"}.
  assert.equal(sourceOf({ source: "derived", at: "2026-10-28T00:00:00", version: "google-maps-v1" }), "Google Maps, 28 Oct");
  assert.equal(sourceOf({ source: "derived", at: "not a date", version: "google-maps-v1" }), "Google Maps");
});

test("a walk of an hour or more reads in hours, as a third of listings are that far from the subway", () => {
  const far = (m: number) => gettingAround(listing({ transit_station: "Kipling", transit_walk_min: m }))[0].text;
  assert.equal(far(139.8), "2 h 20 min walk to Kipling station");
  assert.equal(far(59.6), "1 h walk to Kipling station");
  assert.equal(far(59.4), "59 min walk to Kipling station");
});

test("long store names are kept whole, and one store can show without the other", () => {
  const lines = gettingAround(
    listing({ pharmacy_name: "Shoppers Drug Mart Bloor & Spadina Pharmacy", pharmacy_walk_min: 3.4, grocery_name: null }),
  );
  assert.deepEqual(lines.map((l) => l.text), ["Shoppers Drug Mart Bloor & Spadina Pharmacy, 3 min walk"]);
});
