// factLine: each fact a call heard reads in plain words on /me (U-12c). Run with `npm test`.
import assert from "node:assert/strict";
import { test } from "node:test";
import { factLine } from "./heard.ts";

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
