// follows: the rule for when the board flies to the server's focus or area. Run with `npm test`.
import assert from "node:assert/strict";
import { test } from "node:test";
import { type Opened, follows } from "./follow.ts";

test("what the page opened with isn't followed, a change is, and so is going back to it", () => {
  const opened: Opened<string | null> = { current: undefined };
  assert.equal(follows(opened, "Christie Pits"), false); // the page opened there: the board frames itself
  assert.equal(follows(opened, "Christie Pits"), false);
  assert.equal(follows(opened, "King and Bay"), true);
  assert.equal(follows(opened, "Christie Pits"), true); // back to where the page opened: the map goes too
});

test("opening on nothing, the first value is followed", () => {
  const opened: Opened<string | null> = { current: undefined };
  assert.equal(follows(opened, null), false);
  assert.equal(follows(opened, "t:2"), true);
});
