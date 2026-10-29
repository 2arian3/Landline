// cardFacts and newestQuote: what the card shows of what a call heard. Run with `npm test`.
import assert from "node:assert/strict";
import { test } from "node:test";
import { cardFacts, newestQuote } from "./facts.ts";
import type { Fact } from "./types.ts";

const fact = (field: string, value: string, at?: string, quote = `They said ${value}.`): Fact =>
  ({ field, value, quote, speaker: "Agent heard", at: at ?? null });

test("facts name their subject, in the order a renter asks, three at most", () => {
  const shown = cardFacts([
    fact("pets", "dogs are fine"),
    fact("parking_cost", "$150 a month, on top of rent"),
    fact("available", "still available"),
    fact("laundry", "in the unit"),
  ]);
  assert.deepEqual(shown.map((f) => [f.label, f.text, f.glyph]), [
    [null, "Still available", "circle-check"],
    ["Parking", "$150 a month, on top of rent", "square-parking"],
    ["Pets", "dogs are fine", "paw-print"],
  ]);
});

test("one line per subject: the newest wins, and parking's two fields are one subject", () => {
  const shown = cardFacts([
    fact("parking", "yes, one spot", "2026-10-28T22:40:00Z"),
    fact("parking_cost", "$150 a month", "2026-10-28T22:43:00Z"),
    fact("pets", "cats only", "2026-10-28T22:41:00Z"),
    fact("pets", "dogs are fine too", "2026-10-28T22:44:00Z"),
  ]);
  assert.deepEqual(shown.map((f) => `${f.label}: ${f.text}`), ["Parking: $150 a month", "Pets: dogs are fine too"]);
});

test("words that say it all stand alone; a trailing period goes", () => {
  assert.deepEqual(cardFacts([fact("available_from", "now")]).map((f) => f.text), ["Available now"]);
  assert.deepEqual(cardFacts([fact("available_from", "Dec 1.")]).map((f) => f.text), ["Free from Dec 1"]);
  assert.deepEqual(cardFacts([fact("fees", "no other monthly fees")]).map((f) => [f.label, f.text]),
    [[null, "No other monthly fees"]]);
  assert.deepEqual(cardFacts([fact("other", "the lease is 12 months")]).map((f) => [f.label, f.text, f.glyph]),
    [[null, "The lease is 12 months", "message-circle"]]);
});

test("the newest quote is the one said last; with no times, the last in the list", () => {
  const early = fact("pets", "dogs are fine", "2026-10-28T22:41:00Z", "Dogs are fine.");
  const late = fact("parking", "$150", "2026-10-28T22:43:00Z", "Parking is a hundred fifty.");
  assert.equal(newestQuote([late, early])?.quote, "Parking is a hundred fifty.");
  assert.equal(newestQuote([fact("pets", "a", undefined, "First."), fact("pets", "b", undefined, "Second.")])?.quote,
    "Second.");
  assert.equal(newestQuote([fact("pets", "a", undefined, " ")]), null);
  assert.equal(newestQuote([]), null);
});

test("a value that already names its subject isn't labelled twice", () => {
  assert.deepEqual(cardFacts([fact("parking_cost", "Parking is $180 a month, required")]).map((f) => [f.label, f.text]),
    [[null, "Parking is $180 a month, required"]]);
  assert.deepEqual(cardFacts([fact("pets", "pets are welcome")]).map((f) => [f.label, f.text]),
    [[null, "Pets are welcome"]]);
});
