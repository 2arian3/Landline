// viewingStatus: a card's viewing, booked, confirmed or turned down, in Toronto time (O-08e). Run with `npm test`.
import assert from "node:assert/strict";
import { test } from "node:test";
import type { CardViewing } from "./types.ts";
import { viewingStatus, viewingTime } from "./viewing.ts";

const SAT_2PM: CardViewing = { starts_at: "2026-10-31T18:00:00Z", slot_text: "Saturday at 2 pm", status: "booked" };

test("each status reads as the card says it, at the time in Toronto", () => {
  assert.equal(viewingStatus(SAT_2PM).word, "Viewing Sat 2:00 PM · waiting for the agent");
  assert.equal(viewingStatus({ ...SAT_2PM, status: "confirmed" }).word, "Viewing Sat 2:00 PM · confirmed");
  assert.equal(viewingStatus({ ...SAT_2PM, status: "declined" }).word, "The agent can't do Sat 2:00 PM");
});

test("confirmed is the checked green, declined the something's-off red, waiting stays quiet", () => {
  assert.equal(viewingStatus({ ...SAT_2PM, status: "confirmed" }).color, "var(--color-real)");
  assert.equal(viewingStatus({ ...SAT_2PM, status: "declined" }).color, "var(--color-dead)");
  assert.equal(viewingStatus(SAT_2PM).color, "var(--color-ink-2)");
});

test("a time that doesn't parse falls back to the agent's own words", () => {
  assert.equal(viewingTime({ ...SAT_2PM, starts_at: "soon" }), "Saturday at 2 pm");
});
