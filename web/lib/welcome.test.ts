// splitWelcome on the Narrator's opening lines (orchestrator/narrator.py) and the cases a review raised.
// Run with `npm test` (Node's own runner).
import assert from "node:assert/strict";
import { test } from "node:test";
import { splitWelcome } from "./welcome.ts";

const RESULTS = "I booked 30 Carabob Court for Saturday at 2 pm. Nobody picked up at 1915 Martin Grove Rd.";

test("the greeting, then the last results", () => {
  assert.deepEqual(splitWelcome(`Welcome back, Sara. ${RESULTS}`, "Sara"), {
    greeting: "Welcome back, Sara.",
    news: RESULTS,
  });
});

test("no name on file: the Narrator's plain welcome", () => {
  assert.deepEqual(splitWelcome("Welcome back. Here is your search from last time.", null), {
    greeting: "Welcome back.",
    news: "Here is your search from last time.",
  });
});

test("a period inside the name doesn't end the greeting", () => {
  assert.deepEqual(splitWelcome(`Welcome back, Dr. Sara. ${RESULTS}`, "Dr. Sara"), {
    greeting: "Welcome back, Dr. Sara.",
    news: RESULTS,
  });
});

test("no greeting to match: no news, rather than a sentence cut in two", () => {
  assert.deepEqual(splitWelcome("Welcome back Sara I booked 412 Markham", "Sara"), {
    greeting: "Welcome back, Sara.",
    news: "",
  });
  assert.deepEqual(splitWelcome("", "Sara"), { greeting: "Welcome back, Sara.", news: "" });
});
