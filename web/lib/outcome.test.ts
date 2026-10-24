// callNotes against both results wordings: the Narrator's (orchestrator/narrator.py, its test's own text)
// and the ?dev=1 demo's (api/demo.py). Run with `npm test` (Node's own runner, no dependencies).
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { callNotes } from "./outcome.ts";
import type { Card } from "./types.ts";

const card = (address: string) => ({ address }) as Card;
const LEASE = "Would they sign a 12-month lease?";

const NARRATOR =
  "I booked 5 Main St for Saturday at 2 pm. It moves up to first. " +
  `Its agent asked, “${LEASE}” I said I'd check with you. ` +
  "At 1 Main St, parking: $180 a month; pets: dogs ok. " +
  "The listing says parking included, but the agent said $180 a month. It drops to second. " +
  "2 Main St: wants a deposit by e-transfer before anyone sees the unit, so I'd skip it. " +
  "3 Main St is already rented. " +
  "Nobody picked up at 4 Main St.";

const DEMO =
  "I booked 650 Briar Hill Avenue for Saturday at 2 pm, inside your Saturday afternoon. " +
  "30 Carabob Court is really $2,179 a month with parking. " +
  `The agent at 30 Carabob Court asked, “${LEASE}” I said I'd check with you. ` +
  "701 Don Mills Rd wants a deposit by e-transfer before anyone sees the unit, so I'd skip it. " +
  "Nobody picked up at 1915 Martin Grove Rd.";

test("the Narrator's booking and the question its agent asked, which names no place", () => {
  assert.deepEqual(callNotes(card("5 Main St"), NARRATOR), { viewing: "Saturday at 2 pm", questions: [LEASE] });
});

test("the Narrator's other places get neither", () => {
  for (const address of ["1 Main St", "2 Main St", "3 Main St", "4 Main St"]) {
    assert.deepEqual(callNotes(card(address), NARRATOR), { viewing: null, questions: [] }, address);
  }
});

test("the demo's wording: the booking with its window, and a question that names the place", () => {
  assert.deepEqual(callNotes(card("650 Briar Hill Avenue"), DEMO), { viewing: "Saturday at 2 pm", questions: [] });
  assert.deepEqual(callNotes(card("30 Carabob Court"), DEMO), { viewing: null, questions: [LEASE] });
  assert.deepEqual(callNotes(card("1915 Martin Grove Rd"), DEMO), { viewing: null, questions: [] });
});

test("a Saint stays inside its address, and a street's St. still ends a sentence", () => {
  const text = "Nobody picked up at 4 Main St. I booked 64 St. Clair Avenue West for Sunday at 11:30 am. " +
    "Its agent asked, “Is a cat okay?” I said I'd check with you.";
  assert.deepEqual(callNotes(card("64 St. Clair Avenue West"), text), { viewing: "Sunday at 11:30 am", questions: ["Is a cat okay?"] });
  assert.deepEqual(callNotes(card("4 Main St"), text), { viewing: null, questions: [] });
});

test("no card or no results: nothing", () => {
  assert.deepEqual(callNotes(undefined, NARRATOR), { viewing: null, questions: [] });
  assert.deepEqual(callNotes(card("5 Main St"), null), { viewing: null, questions: [] });
});

test("no regex lookbehind, which Safari before 16.4 can't parse", () => {
  const source = readFileSync(new URL("./outcome.ts", import.meta.url), "utf8");
  assert.equal(/\(\?<[=!]/.test(source), false);
});
