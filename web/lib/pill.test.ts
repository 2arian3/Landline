// The panel header's pill: what the call is doing, from the phase and the calls. Run with `npm test`.
import assert from "node:assert/strict";
import { test } from "node:test";
import { pill } from "./pill.ts";
import type { CallView, SessionView } from "./types.ts";

const view = (phase: SessionView["phase"], calls: Partial<CallView>[] = [], plan: string[] = []) =>
  ({ phase, calls, plan, board: [], matches: [] }) as unknown as SessionView;
const up = { connected: true, stale: false };

test("the pill follows the phase", () => {
  assert.deepEqual(pill(view("open"), up), { words: "Listening", tone: "listening", icon: "wave" });
  assert.deepEqual(pill(view("done"), up), { words: "Calls finished", tone: "real", icon: "check" });
  assert.deepEqual(pill(view("closed"), up), { words: "Call ended", tone: "quiet", icon: "ended" });
});

test("calling counts the calls still going, and the plan before the first is scheduled", () => {
  assert.equal(pill(view("calling", [], ["a", "b"]), up).words, "Calling 2 places");
  const calls = [{ state: "live" }, { state: "ringing" }] as Partial<CallView>[];
  assert.equal(pill(view("calling", calls, ["a", "b"]), up).words, "Calling 2 places");
  assert.equal(pill(view("calling", [{ state: "live" }, { state: "ended" }], ["a", "b"]), up).words, "Calling 1 place");
  assert.equal(pill(view("calling", [{ state: "ended" }, { state: "blocked" }], ["a", "b"]), up).words, "Calling");
  assert.equal(pill(view("calling", [{ state: "queued" }], ["a"]), up).tone, "live");
});

test("the stream is mentioned only when it isn't up, and judge mode's line wins", () => {
  assert.equal(pill(null, up).words, "Connecting");
  assert.equal(pill(view("open"), { connected: false, stale: false }).words, "Connecting");
  assert.deepEqual(pill(view("calling"), { connected: true, stale: true }), { words: "Reconnecting", tone: "quiet", icon: "waiting" });
  assert.equal(pill(view("done"), { ...up, offline: true }).words, "Offline");
  assert.equal(pill(view("open"), { ...up, line: "The line dropped" }).words, "The line dropped");
});
