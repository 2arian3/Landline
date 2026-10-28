// refusal: what /api/me's answer means for /me, signed in with Clerk (GET) or with the typed number (POST).
// Run with `npm test`.
import assert from "node:assert/strict";
import { test } from "node:test";
import { refusal } from "./meRefusal.ts";

test("a history comes back as itself", () => {
  assert.equal(refusal(200, null), null);
});

test("a missing or ended Clerk session asks to sign in again", () => {
  assert.equal(refusal(401, null), "signin");
  assert.equal(refusal(403, null), "signin");
});

test("a number with no profile, history switched off, a bad number and a down server read apart", () => {
  assert.equal(refusal(404, { detail: "unknown number" }), "unknown");
  assert.equal(refusal(404, { detail: "history is off" }), "off");
  assert.equal(refusal(404, null), "off");
  assert.equal(refusal(422, null), "phone");
  assert.equal(refusal(502, null), "unreachable");
});
