// nothingHeard: until the renter says something, the page shows no match framing. Run with `npm test`.
import assert from "node:assert/strict";
import { test } from "node:test";
import { nothingHeard } from "./format.ts";
import type { SessionView } from "./types.ts";

const EMPTY = { areas: [], anchors: [], pets: [], must_have: [], priorities: [] };

function view(over: Record<string, unknown> = {}, prefs: Record<string, unknown> = {}): SessionView {
  return {
    session_id: "s_1",
    version: 1,
    phase: "open",
    preferences: { ...EMPTY, ...prefs },
    matches: ["t:1", "t:2"],
    board: [{ listing_id: "t:1", rank: 1 }],
    plan: [],
    calls: [],
    agent_says: "Tell me what you're looking for, and the map will follow.",
    ...over,
  } as unknown as SessionView;
}

test("a fresh session has heard nothing", () => {
  assert.equal(nothingHeard(view()), true);
  assert.equal(nothingHeard(view({}, { max_monthly: null, min_beds: null })), true);
});

test("any preference, a plan, a reaction or calls end the blank start", () => {
  assert.equal(nothingHeard(view({}, { max_monthly: 2800 })), false);
  assert.equal(nothingHeard(view({}, { min_beds: 0 })), false); // a studio is something said
  assert.equal(nothingHeard(view({}, { anchors: [{ label: "Christie Pits" }] })), false);
  assert.equal(nothingHeard(view({}, { priorities: ["light"] })), false);
  assert.equal(nothingHeard(view({ plan: ["t:1"] })), false);
  assert.equal(nothingHeard(view({ board: [{ listing_id: "t:1", rank: 1, reaction: "too dark" }] })), false);
  assert.equal(nothingHeard(view({ phase: "done" })), false);
});
