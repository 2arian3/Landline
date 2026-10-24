// resultsByPlace against both results wordings: the Narrator's (orchestrator/narrator.py) and the ?dev=1
// demo's (api/demo.py), and the cases a review raised. Run with `npm test` (Node's own runner).
import assert from "node:assert/strict";
import { test } from "node:test";
import { resultsByPlace } from "./results.ts";

const LEASE = "Would they sign a 12-month lease?";

const NARRATOR =
  "I booked 5 Main St for Saturday at 2 pm. It moves up to first. " +
  `Its agent asked, “${LEASE}” I said I'd check with you. ` +
  "At 1 Main St, parking: $180 a month; pets: dogs ok. " +
  "The listing says parking included, but the agent said $180 a month. It drops to second. " +
  "2 Main St: wants a deposit by e-transfer before anyone sees the unit, so I'd skip it. " +
  "Nobody picked up at 4 Main St.";

const DEMO =
  "30 Carabob Court is really $2,179 a month with parking. " +
  `The agent at 30 Carabob Court asked, “${LEASE}” I said I'd check with you. ` +
  "I booked 650 Briar Hill Avenue for Saturday at 2 pm, inside your Saturday afternoon. " +
  "Nobody picked up at 64 St. Clair Avenue West.";

test("the Narrator's results: one line per place, with what follows a place kept on its line", () => {
  assert.deepEqual(resultsByPlace(NARRATOR, ["5 Main St", "1 Main St", "2 Main St", "4 Main St"]), [
    `I booked 5 Main St for Saturday at 2 pm. It moves up to first. Its agent asked, “${LEASE}” I said I'd check with you.`,
    "At 1 Main St, parking: $180 a month; pets: dogs ok. " +
      "The listing says parking included, but the agent said $180 a month. It drops to second.",
    "2 Main St: wants a deposit by e-transfer before anyone sees the unit, so I'd skip it.",
    "Nobody picked up at 4 Main St.",
  ]);
});

test("the demo's results: the booking leads, and \"St.\" doesn't end a sentence", () => {
  const lines = resultsByPlace(DEMO, ["650 Briar Hill Avenue", "30 Carabob Court", "64 St. Clair Avenue West"]);
  assert.equal(lines.length, 3);
  assert.ok(lines[0].startsWith("I booked 650 Briar Hill Avenue"));
  assert.equal(lines[2], "Nobody picked up at 64 St. Clair Avenue West.");
});

test("a place is named whole: 112 King St W isn't put with 12 King St W", () => {
  const text = "Nobody picked up at 12 King St W. 112 King St W is really $2,000 a month.";
  assert.deepEqual(resultsByPlace(text, ["12 King St W", "112 King St W"]), [
    "Nobody picked up at 12 King St W.",
    "112 King St W is really $2,000 a month.",
  ]);
});

test("only the booking itself leads, not a line that says \"booked\"", () => {
  const text = "12 King St W is fully booked this week. I booked 9 Bay St for Saturday at 2 pm.";
  assert.equal(resultsByPlace(text, ["12 King St W", "9 Bay St"])[0], "I booked 9 Bay St for Saturday at 2 pm.");
});
