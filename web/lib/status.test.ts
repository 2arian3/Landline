// statusOf: every status on the canvas's Status board, from a card, its latest call and its viewing, and
// which one wins when several apply. Run with `npm test`.
import assert from "node:assert/strict";
import { test } from "node:test";
import { stageOf, statusOf, UNHEARD } from "./status.ts";
import type { CallView, Card, CardViewing, EndReason, Fact } from "./types.ts";

const SAT_2PM = "2026-10-31T18:00:00Z";
const card = (over: Partial<Card> = {}) => ({ listing_id: "t:1", status: "pending", red_flags: [], ...over }) as Card;
const call = (state: CallView["state"], over: Partial<CallView> = {}) =>
  ({ call_id: "c_1", listing_id: "t:1", state, ...over }) as CallView;
const ended = (end_reason: EndReason, facts: Partial<Fact>[] = []) =>
  call("ended", { outcome: { call_id: "c_1", listing_id: "t:1", end_reason, facts } as CallView["outcome"] });
const viewing = (status: CardViewing["status"]): CardViewing => ({ starts_at: SAT_2PM, slot_text: "Saturday at 2", status });
const plain = (s: string | null) => s?.replace(/ /g, " ") ?? null;
const said = (s: ReturnType<typeof statusOf>) => [s.kind, plain(s.words), s.sub, s.glyph, s.tone, s.pin, s.badge];

test("ranked: no status line, silence is the status, and a screen reader hears 'not called yet'", () => {
  const s = statusOf(card());
  assert.deepEqual(said(s), ["ranked", "", null, null, "quiet", "pending", null]);
  assert.equal(s.spoken, "Not called yet");
});

test("will call, queued and ringing, from the plan and the call's state", () => {
  assert.deepEqual(said(statusOf(card(), { planned: true })),
    ["will_call", "Will call when you hang up", null, "phone", "ink", "picked", "phone"]);
  assert.deepEqual(said(statusOf(card(), { planned: true, call: call("queued") })),
    ["queued", "Queued", null, "clock", "quiet", "queued", "phone"]);
  for (const state of ["dialing", "ringing"] as const) {
    assert.deepEqual(said(statusOf(card(), { planned: true, call: call(state) })),
      ["ringing", "Ringing", null, "phone", "live", "calling", "phone"], state);
  }
});

test("on the phone, from a live call or the card's own status", () => {
  const live = ["on_phone", "On the phone", null, "audio-lines", "live", "calling", "audio-lines"];
  assert.deepEqual(said(statusOf(card(), { planned: true, call: call("live") })), live);
  assert.deepEqual(said(statusOf(card({ status: "calling" }))), live);
});

test("checked by phone: green with a check badge, so it never reads like ranked", () => {
  assert.deepEqual(said(statusOf(card({ status: "verified" }))),
    ["checked", "Checked by phone", null, "circle-check", "real", "checked", "check"]);
});

test("a booked viewing is ink until the agent confirms, then green; a declined one is red", () => {
  assert.deepEqual(said(statusOf(card({ status: "booked", viewing: viewing("booked") }))),
    ["booked", "Viewing Sat, Oct 31 · 2:00 PM", "Waiting for the agent to confirm", "calendar", "ink", "booked",
      "calendar"]);
  assert.deepEqual(said(statusOf(card({ status: "booked", viewing: viewing("confirmed") }))),
    ["confirmed", "Viewing confirmed · Sat, Oct 31 · 2:00 PM", null, "calendar-check", "real", "confirmed", "check"]);
  assert.deepEqual(said(statusOf(card({ status: "booked", viewing: viewing("declined") }))),
    ["declined", "The agent can’t do Sat, Oct 31 · 2:00 PM", null, "calendar-x", "dead", "declined", "x"]);
});

test("a booking with no confirmation row yet takes its time from the call, and never makes one up", () => {
  const withTime = call("ended", {
    outcome: { call_id: "c_1", listing_id: "t:1", end_reason: "done", facts: [],
      viewing: { starts_at: SAT_2PM, slot_text: "Saturday at 2" } } as unknown as CallView["outcome"],
  });
  assert.equal(plain(statusOf(card({ status: "booked" }), { call: withTime }).words), "Viewing Sat, Oct 31 · 2:00 PM");
  assert.equal(statusOf(card({ status: "booked" })).words, "Viewing booked");
});

test("something's off: red, with the flag in full under it", () => {
  const s = statusOf(card({ status: "suspect", red_flags: ["Asked for first and last by e-transfer"] }));
  assert.deepEqual(said(s),
    ["flagged", "Something’s off", "Asked for first and last by e-transfer", "triangle-alert", "dead", "flagged",
      "exclamation"]);
  assert.equal(s.spoken, "Something’s off. Asked for first and last by e-transfer");
});

test("didn't get through: worded from how the call ended", () => {
  for (const [reason, word] of [["no_answer", "No answer"], ["busy", "No answer"], ["voicemail", "Left a message"],
    ["hangup", "Hung up early"], ["timeout", "Call dropped"], ["wrong_number", "Wrong number"]] as const) {
    assert.deepEqual(said(statusOf(card({ status: "no_answer" }), { call: ended(reason) })),
      ["unheard", word, null, reason === "voicemail" ? "voicemail" : "x", "quiet", "noanswer", null], reason);
    assert.equal(statusOf(card({ status: "no_answer" }), { ended: reason }).words, word, reason);
  }
  assert.equal(statusOf(card({ status: "no_answer" })).words, "No answer");   // no outcome yet
  assert.equal(statusOf(card({ status: "no_answer" }), { call: ended("hangup", [{ field: "pets" }]) }).words,
    "No answer");   // a call that heard facts isn't worded as unheard
});

test("already rented: quiet, hollow pin", () => {
  assert.deepEqual(said(statusOf(card({ status: "dead" }))),
    ["gone", "Already rented", null, "x", "quiet", "gone", null]);
});

test("the call's stage wins while it runs; once live or ended the card's own status speaks", () => {
  const flagged = card({ status: "suspect", red_flags: ["x"] });
  assert.equal(statusOf(flagged, { planned: true, call: call("ringing") }).kind, "ringing");
  assert.equal(statusOf(card({ status: "verified" }), { planned: true, call: ended("done") }).kind, "checked");
  assert.equal(statusOf(card({ status: "booked", viewing: viewing("confirmed") }), { call: call("live") }).kind,
    "on_phone");   // a callback on the line about a confirmed place
  assert.equal(statusOf(card({ status: "verified" }), { stage: "queued" }).kind, "queued");   // LiveBoard's mark
  assert.equal(statusOf(card({ status: "verified" }), { stage: null, planned: true }).kind, "checked");
});

test("stageOf: a planned place with no call will be called; a finished or live call has no stage", () => {
  assert.equal(stageOf(undefined, true), "will_call");
  assert.equal(stageOf(undefined, false), null);
  assert.equal(stageOf(call("queued"), true), "queued");
  assert.equal(stageOf(call("live"), true), null);
  assert.equal(stageOf(call("ended"), true), null);
});

test("every end reason has a word and a sentence", () => {
  const reasons: EndReason[] = ["done", "refused", "wrong_number", "voicemail", "hangup", "no_answer", "busy",
    "failed", "blocked", "timeout"];
  for (const r of reasons) assert.ok(UNHEARD[r].word && UNHEARD[r].line, r);
});
