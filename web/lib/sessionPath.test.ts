// sessionPath: only a ?dev=1 page asks for the dev demo's scripted brief. Run with `npm test`.
import assert from "node:assert/strict";
import { test } from "node:test";
import { sessionPath } from "./sessionPath.ts";

test("a page without ?dev=1 asks for the plain session, which opens with nothing heard", () => {
  assert.equal(sessionPath("demo-1a2b3c4d", ""), "/api/sessions/demo-1a2b3c4d");
  assert.equal(sessionPath("s_abc", "?x=1"), "/api/sessions/s_abc");
  assert.equal(sessionPath("s_abc", "?dev=0"), "/api/sessions/s_abc");
});

test("the ?dev=1 demo asks for its brief", () => {
  assert.equal(sessionPath("smoke-375", "?dev=1"), "/api/sessions/smoke-375?dev=1");
  assert.equal(sessionPath("a b", "?x=2&dev=1"), "/api/sessions/a%20b?dev=1");
});
