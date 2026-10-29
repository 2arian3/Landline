// The board's "is a newer version still owed?" check (useLiveSession in live.ts). Run with `npm test`.
import assert from "node:assert/strict";
import { test } from "node:test";
import { announced, owed } from "./owed.ts";

test("a version announced while a fetch was in flight is still owed when that fetch brings the older one", () => {
  let wanted = 0;
  wanted = announced(wanted, 10);    // the stream says 10: a fetch starts
  wanted = announced(wanted, 11);    // the calls finish while it's on its way: 11, and that refresh is skipped
  const fetched = 10;                // the fetch that started before 11 was saved
  assert.equal(owed(wanted, fetched), true);   // so the board asks again, and gets the results
  assert.equal(owed(wanted, 11), false);       // once 11 is on screen, nothing more is owed
});

test("nothing is owed with nothing announced, or when the screen is ahead of the stream", () => {
  assert.equal(owed(0, null), false);
  assert.equal(owed(0, undefined), false);
  assert.equal(owed(5, 7), false);
  assert.equal(owed(3, null), true);   // announced before the first snapshot landed
});

test("announcements only move forward, and a bad one changes nothing", () => {
  assert.equal(announced(11, 10), 11);
  assert.equal(announced(11, Number.NaN), 11);
});
