// callNotes against both results wordings: the Narrator's (orchestrator/narrator.py, its test's own text)
// and the ?dev=1 demo's (api/demo.py). Run with `npm test` (Node's own runner, no dependencies).
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { callNotes } from "./outcome.ts";
import type { CallView, Card } from "./types.ts";

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

test("a call's outcome wins over the results text", () => {
  const place = { listing_id: "t:5", address: "5 Main St" } as Card;
  const call = {
    call_id: "c_1", listing_id: "t:5", state: "ended",
    outcome: { call_id: "c_1", listing_id: "t:5", end_reason: "done", facts: [], conflicts: [], red_flags: [],
      open_questions: ["Is a cat okay?"], viewing: { starts_at: "2026-10-24T18:00:00Z", slot_text: "Saturday at two works." } },
  } as CallView;
  // in Farsi, say: the text says nothing the parser can read, and the outcome still answers
  assert.deepEqual(callNotes(place, "نتایج تماس‌ها", [call]), { viewing: "Saturday at 2 pm", questions: ["Is a cat okay?"] });
  assert.deepEqual(callNotes(place, NARRATOR, [{ ...call, outcome: null }]), { viewing: "Saturday at 2 pm", questions: [LEASE] });
});

test("no card or no results: nothing", () => {
  assert.deepEqual(callNotes(undefined, NARRATOR), { viewing: null, questions: [] });
  assert.deepEqual(callNotes(card("5 Main St"), null), { viewing: null, questions: [] });
});

test("no regex lookbehind, which Safari before 16.4 can't parse", () => {
  const source = readFileSync(new URL("./outcome.ts", import.meta.url), "utf8");
  assert.equal(/\(\?<[=!]/.test(source), false);
});

const called = (address: string, status: Card["status"] = "verified") => ({ address, status }) as Card;

test("only a place that was called: a booking isn't shown on every unit sharing the address", () => {
  const text = "I booked 30 Carabob Court for Saturday at 2 pm.";
  assert.deepEqual(callNotes(called("30 Carabob Court", "booked"), text), { viewing: "Saturday at 2 pm", questions: [] });
  assert.deepEqual(callNotes(called("30 Carabob Court", "pending"), text), { viewing: null, questions: [] });
});

test("a number and a capital in a question doesn't end the place's run", () => {
  const text = "I booked 5 Main St for Saturday at 2 pm. Its agent asked, “Can you move in by 1 November?” " +
    "I said I'd check with you. Its agent asked, “Is the 2 Bedroom okay?” I said I'd check with you.";
  assert.deepEqual(callNotes(called("5 Main St"), text).questions, ["Can you move in by 1 November?", "Is the 2 Bedroom okay?"]);
});

test("an abbreviation inside the address doesn't split it", () => {
  for (const address of ["100 Queen St. W", "12-26 Rosseau Rd."]) {
    const text = `I booked ${address} for Saturday at 2 pm. Its agent asked, “Is a cat okay?” I said I'd check with you.`;
    assert.deepEqual(callNotes(called(address), text), { viewing: "Saturday at 2 pm", questions: ["Is a cat okay?"] }, address);
  }
});

test("an address is named only as a whole", () => {
  const text = "I booked 112 King St W for Saturday at 2 pm. Its agent asked, “Is a cat okay?” I said I'd check with you.";
  assert.deepEqual(callNotes(called("12 King St W"), text), { viewing: null, questions: [] });
  assert.deepEqual(callNotes(called("112 King St W"), text), { viewing: "Saturday at 2 pm", questions: ["Is a cat okay?"] });
});

test("a question with two sentences in it stays whole", () => {
  const text = "At 5 Main St, pets: dogs ok. Its agent asked, “Do you have pets? What kind?” I said I'd check with you.";
  assert.deepEqual(callNotes(called("5 Main St"), text).questions, ["Do you have pets? What kind?"]);
});

test("a place the Narrator couldn't name ends the run before its agent's question", () => {
  const text = "I booked 5 Main St for Saturday at 2 pm. I couldn't reach that place in time. " +
    "Its agent asked, “Is a cat okay?” I said I'd check with you.";
  assert.deepEqual(callNotes(called("5 Main St"), text), { viewing: "Saturday at 2 pm", questions: [] });
});

test("a window clause with a period in it", () => {
  const text = "I booked 5 Main St for Saturday at 2 pm, inside your Sat. afternoon.";
  assert.equal(callNotes(called("5 Main St"), text).viewing, "Saturday at 2 pm");
});
