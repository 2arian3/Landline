// The app's pure helpers, run by Node itself (it strips the types), so no test runner to install:
// npm test.
import assert from "node:assert/strict";
import { test } from "node:test";

import * as web from "../../web/lib/heard.ts";
import { factLine, phoneLabel, VIEWING, viewingLine, WENT, when } from "../src/lib/heard.ts";
import { latest } from "../src/lib/listings.ts";
import { latestWins } from "../src/lib/loads.ts";
import { toE164 } from "../src/lib/phone.ts";

test("a heard fact reads as it does on the web's /me", () => {
  const facts = [
    ["available", "still available."], ["available_from", "now"], ["available_from", "November 1"],
    ["fees", "no other monthly fees"], ["fees", "amenity $40 a month, required"], ["parking_cost", "$150 a month"],
    ["pets", "cats only"], ["ac", "central"], ["move_in_fee", "$200"],
  ];
  for (const [field, value] of facts) assert.equal(factLine(field, value), web.factLine(field, value), field);
  assert.equal(factLine("available_from", "November 1"), "free from November 1");
  assert.equal(factLine("parking_cost", "$150 a month."), "parking: $150 a month");
  assert.equal(factLine("move_in_fee", "$200"), "move in fee: $200");
  assert.deepEqual(WENT, web.WENT);
  assert.deepEqual(VIEWING, web.VIEWING);
});

test("a viewing shows its time once, the code's, and reads as it does on the web's /me", () => {
  const at = "2026-10-29T22:30:00Z";   // 6:30 p.m. in Toronto (EDT)
  assert.match(viewingLine({ starts_at: at, slot_text: "Thu 29 Oct, 6:30 p.m.", status: "booked" }),
    /^Viewing booked: Thu, Oct 29, 6:30\sp\.m\.$/);
  assert.match(viewingLine({ starts_at: at, slot_text: "", status: "confirmed" }), /^Viewing confirmed: Thu, Oct 29, 6:30\sp\.m\.$/);
  assert.equal(viewingLine({ starts_at: null, slot_text: "Saturday at 2 pm", status: "booked" }), "Viewing booked: Saturday at 2 pm");
  const cases = [
    { starts_at: at, slot_text: "Thu 29 Oct, 6:30 p.m.", status: "booked" }, { starts_at: at, slot_text: "", status: "declined" },
    { starts_at: null, slot_text: "Saturday at 2 pm", status: "confirmed" }, { starts_at: "soon", slot_text: "Sunday", status: "booked" },
    { slot_text: " ", status: "booked" },
  ];
  for (const v of cases) assert.equal(viewingLine(v), web.viewingLine(v), JSON.stringify(v));
});

test("times are Toronto's", () => {
  const said = when("2026-10-24T05:12:00Z");   // 1:12 a.m. in Toronto (EDT)
  assert.match(said, /^Sat, Oct 24, 1:12\sa\.m\.$/);
  assert.equal(when("not a time"), "");
});

test("numbers read as the web writes them", () => {
  assert.equal(phoneLabel("+12895122338"), "+1 289 512 2338");
  assert.equal(phoneLabel("+442071234567"), "+442071234567");
});

test("the latest results are the newest call's session", () => {
  const call = (session_id, call_id) => ({ session_id, call_id });
  assert.deepEqual(latest([call("s2", "c3"), call("s2", "c2"), call("s1", "c1")]).map((x) => x.call_id), ["c3", "c2"]);
  assert.deepEqual(latest([]), []);
});

test("a typed number becomes E.164", () => {
  assert.equal(toE164("(416) 555-0100"), "+14165550100");
  assert.equal(toE164("1 416 555 0100"), "+14165550100");
  assert.equal(toE164("+44 20 7123 4567"), "+442071234567");
  assert.equal(toE164("555-0100"), null);
});

// A load the test settles by hand, to finish loads out of order.
function pending() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { fetch: () => promise, resolve, reject };
}

test("a slow older load can't overwrite a newer one", async () => {
  const shown = [];
  const run = latestWins((s) => shown.push(s), (e) => e.message);
  const first = pending(), refresh = pending();
  const a = run(first.fetch), b = run(refresh.fetch);
  refresh.resolve(["new"]);
  await b;
  first.resolve(["old"]);
  await a;
  assert.deepEqual(shown.at(-1), { data: ["new"], error: null });
});

test("a slow older failure can't put an error over newer data", async () => {
  const shown = [];
  const run = latestWins((s) => shown.push(s), (e) => e.message);
  const first = pending(), refresh = pending();
  const a = run(first.fetch), b = run(refresh.fetch);
  refresh.resolve(["new"]);
  await b;
  first.reject(new Error("Can't reach Landline"));
  await a;
  assert.deepEqual(shown.at(-1), { data: ["new"], error: null });
});

test("a failed load keeps what was shown and says why; the next load clears it", async () => {
  const shown = [];
  const run = latestWins((s) => shown.push(s), (e) => e.message);
  await run(async () => ["mine"]);
  await run(async () => { throw new Error("Can't reach Landline"); });
  assert.deepEqual(shown.at(-1), { data: ["mine"], error: "Can't reach Landline" });
  await run(async () => ["again"]);
  assert.deepEqual(shown.at(-1), { data: ["again"], error: null });
});
