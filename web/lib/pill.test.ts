// The board's header pill and its edge states (PR9). Run with `npm test`.
import assert from "node:assert/strict";
import { test } from "node:test";
import { OFFLINE, pill, unsaved } from "./pill.ts";

const quiet = { stale: false, unreachable: false };

test("before the first snapshot it's connecting, and after 9 s with none, offline in one place", () => {
  assert.deepEqual(pill({ phase: null, ...quiet }), { kind: "connecting", words: "Connecting", tone: "quiet", waiting: true });
  const off = pill({ phase: null, stale: true, unreachable: true });
  assert.equal(off.kind, "offline");
  assert.equal(off.words, "Offline"); // not "Connecting" while the board says it can't reach Landline
  assert.match(OFFLINE.title, /Can.t reach Landline/);
});

test("a board quiet for 9 s turns from reconnecting to offline, whatever its phase", () => {
  for (const phase of ["open", "calling", "done", "closed"] as const) {
    assert.equal(pill({ phase, stale: true, unreachable: false }).words, "Reconnecting", phase); // 6.5 s
    assert.deepEqual(pill({ phase, stale: true, unreachable: true }), { kind: "offline", words: "Offline", tone: "plain", waiting: false }, phase);
  }
});

test("a board that's gone quiet says reconnecting, whatever its phase, and keeps the board", () => {
  for (const phase of ["open", "calling", "done", "closed"] as const) {
    assert.equal(pill({ phase, stale: true, unreachable: false }).kind, "reconnecting", phase);
  }
});

test("each phase says what's happening in words, so it never rests on colour", () => {
  assert.equal(pill({ phase: "open", ...quiet }).words, "Listening");
  assert.equal(pill({ phase: "calling", calls: 2, ...quiet }).words, "Calling 2 places");
  assert.equal(pill({ phase: "calling", calls: 1, ...quiet }).words, "Calling 1 place");
  assert.equal(pill({ phase: "calling", ...quiet }).words, "Calling");
  assert.deepEqual(pill({ phase: "done", ...quiet }), { kind: "finished", words: "Calls finished", tone: "real", waiting: false });
  // A finished brief with nothing picked: not the green "Live" that used to stay on a finished board.
  assert.deepEqual(pill({ phase: "closed", ...quiet }), { kind: "ended", words: "Call ended", tone: "quiet", waiting: false });
});

test("judge mode's dropped line wins over the board's phase", () => {
  assert.equal(pill({ phase: "open", ...quiet, line: "The line dropped" }).words, "The line dropped");
});

test("a tap that didn't save says which, in the renter's words", () => {
  assert.equal(unsaved("too dark"), "Couldn’t save “too dark”");
});
