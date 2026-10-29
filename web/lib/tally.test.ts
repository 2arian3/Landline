// The summary's counts and rows say what the cards say (lib/status.ts). Run with `npm test`.
import assert from "node:assert/strict";
import { test } from "node:test";
import { calledPlaces, statuses, tally } from "./tally.ts";
import type { CallView, Card, SessionView } from "./types.ts";

const card = (id: string, status: Card["status"], more: Partial<Card> = {}) =>
  ({ listing_id: id, address: `${id} Main St`, rank: Number(id), status, facts: [], red_flags: [], ...more }) as Card;
const call = (id: string, state: CallView["state"], end_reason?: string) =>
  ({ call_id: `c${id}`, listing_id: id, state, outcome: end_reason ? { end_reason, facts: [] } : null }) as CallView;
const view = (board: Card[], calls: CallView[], plan: string[]) =>
  ({ board, calls, plan, phase: "calling", matches: [] }) as unknown as SessionView;

test("calls going come first, in the cards' words", () => {
  const v = view(
    [card("1", "calling"), card("2", "pending"), card("3", "no_answer")],
    [call("1", "live"), call("2", "ringing"), call("3", "ended", "voicemail")],
    ["1", "2", "3"],
  );
  const { going, done } = tally(statuses(v));
  assert.deepEqual(going, ["1 on the phone", "1 ringing"]);
  assert.deepEqual(done, [["noanswer", "1 left a message"]]);
});

test("the done tally counts viewings in the plural and keeps each kind's pin", () => {
  const v = view(
    [card("1", "booked"), card("2", "booked"), card("3", "verified"), card("4", "dead")],
    [call("1", "ended"), call("2", "ended"), call("3", "ended"), call("4", "ended")],
    ["1", "2", "3", "4"],
  );
  assert.deepEqual(tally(statuses(v)).done, [
    ["booked", "2 viewings booked"],
    ["checked", "1 checked by phone"],
    ["gone", "1 already rented"],
  ]);
});

test("a ranked place no one called counts for nothing", () => {
  assert.deepEqual(tally(statuses(view([card("5", "pending")], [], []))), { going: [], done: [] });
});

test("what the calls found: a booking first, then the plan's order, then a callback's place", () => {
  const v = view(
    [card("1", "no_answer"), card("2", "booked"), card("3", "verified"), card("9", "pending")],
    [call("1", "ended", "no_answer"), call("2", "ended"), call("3", "ended"), call("3", "ended")],
    ["1", "2"],
  );
  assert.deepEqual(calledPlaces(v).map(({ card: c }) => c.listing_id), ["2", "1", "3"]);
});
