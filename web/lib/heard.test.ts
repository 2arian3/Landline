// factLine: each fact a call heard reads in plain words on /me (U-12c). Run with `npm test`.
import assert from "node:assert/strict";
import { test } from "node:test";
import { factLine, viewingLine } from "./heard.ts";

test("facts read as a label and the Auditor's words", () => {
  assert.equal(factLine("parking_cost", "$180 a month per spot"), "parking: $180 a month per spot");
  assert.equal(factLine("pets", "pets allowed, $300 pet fee, one time."), "pets: pets allowed, $300 pet fee, one time");
  assert.equal(factLine("utilities_included", "heat, water included"), "utilities: heat, water included");
});

test("availability and no fees read as themselves", () => {
  assert.equal(factLine("available", "still available"), "still available");
  assert.equal(factLine("available_from", "Sunday, October 25"), "free from Sunday, October 25");
  assert.equal(factLine("available_from", "now"), "available now");
  assert.equal(factLine("fees", "no other monthly fees"), "no other monthly fees");
});

test("an unknown field still has a label", () => {
  assert.equal(factLine("move_in_bonus", "one month free"), "move in bonus: one month free");
});

// The Mac mini's U-12c check saw "Viewing booked: Thu 29 Oct, 6:30 p.m. (Thu, Oct 29, 6:30 p.m.)".
const AT_630 = "2026-10-29T22:30:00Z";   // 6:30 p.m. in Toronto (EDT)

test("a viewing shows its time once: the code's, from starts_at, even when the agent's words came too", () => {
  const both = viewingLine({ starts_at: AT_630, slot_text: "Thu 29 Oct, 6:30 p.m.", status: "booked" });
  assert.match(both, /^Viewing booked: Thu, Oct 29, 6:30\sp\.m\.$/);
  assert.match(viewingLine({ starts_at: AT_630, slot_text: "", status: "confirmed" }), /^Viewing confirmed: Thu, Oct 29, 6:30\sp\.m\.$/);
});

test("with no time to show, the agent's own words; with neither, just the status", () => {
  assert.equal(viewingLine({ starts_at: null, slot_text: "Saturday at 2 pm", status: "booked" }), "Viewing booked: Saturday at 2 pm");
  assert.equal(viewingLine({ starts_at: "soon", slot_text: "Saturday at 2 pm", status: "declined" }), "Viewing declined: Saturday at 2 pm");
  assert.equal(viewingLine({ slot_text: " ", status: "booked" }), "Viewing booked");
  assert.equal(viewingLine({ starts_at: null, slot_text: "Sunday", status: "moved" }), "Viewing moved: Sunday");
});
