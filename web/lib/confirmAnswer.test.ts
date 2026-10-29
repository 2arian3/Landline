// The confirm page's reading of the API's answers (O-08d). Run with `npm test`.
import assert from "node:assert/strict";
import { test } from "node:test";
import { answered, isEmail } from "./confirmAnswer.ts";

test("an email is checked as the API checks it", () => {
  assert.equal(isEmail(" agent@example.com "), true);
  for (const bad of ["agent@", "agent@example", "not an email", "", "a b@example.com"]) assert.equal(isEmail(bad), false, bad);
});

test("each answer reads as what happened", () => {
  assert.deepEqual(answered(200, { status: "confirmed", invite_sent: true }), {
    kind: "done",
    status: "confirmed",
    inviteSent: true,
  });
  assert.deepEqual(answered(200, { status: "declined" }), { kind: "done", status: "declined", inviteSent: false });
  assert.deepEqual(answered(409, { detail: { message: "already decided", status: "declined" } }), {
    kind: "already",
    status: "declined",
  });
  assert.deepEqual(answered(422, { detail: "not an email address" }), { kind: "bad_email" });
  assert.deepEqual(answered(404, { detail: "unknown link" }), { kind: "unknown" });
  assert.deepEqual(answered(502, null), { kind: "unreachable" });
});
