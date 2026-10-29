// The app's pure helpers, run by Node itself (it strips the types), so no test runner to install:
// npm test.
import assert from "node:assert/strict";
import { test } from "node:test";

import { money as webMoney } from "../../web/lib/format.ts";
import * as web from "../../web/lib/heard.ts";
import { statusOf } from "../../web/lib/status.ts";
import { formatWhen as webFormatWhen } from "../../web/lib/time.ts";
import { ApiError, NO_CALLS_YET, notFound, UnknownNumber } from "../src/lib/errors.ts";
import { factLine, formatWhen, phoneLabel, VIEWING, viewingLine, viewingStatus, WENT, when } from "../src/lib/heard.ts";
import { latest, money, tally } from "../src/lib/listings.ts";
import { latestWins } from "../src/lib/loads.ts";
import { grouped, toE164 } from "../src/lib/phone.ts";

test("a heard fact reads as it does on the web's /me", () => {
  const facts = [
    ["available", "still available."], ["available_from", "now"], ["available_from", "November 1"],
    ["fees", "no other monthly fees"], ["fees", "amenity $40 a month, required"], ["parking_cost", "$150 a month"],
    ["pets", "cats only"], ["pets", "Cats yes, dogs no"], ["ac", "central"], ["move_in_fee", "$200"],
  ];
  for (const [field, value] of facts) assert.equal(factLine(field, value), web.factLine(field, value), field);
  assert.equal(factLine("available_from", "November 1"), "Free from November 1");
  assert.equal(factLine("parking_cost", "$150 a month."), "Parking: $150 a month");
  assert.equal(factLine("move_in_fee", "$200"), "Move in fee: $200");
  assert.equal(factLine("available_from", "Dec 1"), "Free from Dec 1");   // only the first letter changes
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

test("money reads as the web writes it", () => {
  for (const n of [0, 950, 2450, 2450.6, 12345, 1234567]) assert.equal(money(n), webMoney(n), String(n));
  assert.equal(money(2450), "$2,450");
});

test("a number Landline doesn't know yet is its own error, which says how to start", () => {
  const unknown = notFound("unknown number");
  assert.ok(unknown instanceof UnknownNumber && unknown instanceof ApiError);
  assert.equal(unknown.message, NO_CALLS_YET);
  for (const detail of ["history is off", "sign-in is off", ""]) {
    const off = notFound(detail);
    assert.ok(off instanceof ApiError && !(off instanceof UnknownNumber), detail);
    assert.notEqual(off.message, NO_CALLS_YET);
  }
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

test("Home's tally counts the same listings and viewings the cards show, a place called twice once", () => {
  const call = (listing_id, call_id, viewing) => ({ listing_id, call_id, session_id: "s", viewing });
  const listings = [
    call("a", "c3", { status: "confirmed" }),   // newest first: a's latest call has the confirmed viewing
    call("b", "c2", null),
    call("a", "c1", { status: "booked" }),      // an older call to a: not counted again
    call("c", "c0", { status: "booked" }),
    call("d", "c5", null),                      // d's newest call reached nobody…
    call("d", "c4", { status: "booked" }),      // …but an earlier one booked a viewing, which still counts
  ];
  assert.deepEqual(tally(listings), [
    { kind: "called", words: "4 places called" },
    { kind: "confirmed", words: "1 viewing confirmed" },
    { kind: "booked", words: "2 viewings booked" },
  ]);
  assert.deepEqual(tally([call("a", "c1", null)]), [{ kind: "called", words: "1 place called" }]);
});

test("a typed number is grouped as the renter types it, and still reads as E.164", () => {
  assert.equal(grouped("41655501"), "416 555 01");
  assert.equal(grouped("(416) 555-0100"), "416 555 0100");
  assert.equal(grouped("4165550100999"), "416 555 0100");
  assert.equal(grouped("14165550100"), "1 416 555 0100");
  assert.equal(grouped("+44 20 7123 4567"), "+44 20 7123 4567");
  assert.equal(toE164(grouped("4165550100")), "+14165550100");
  assert.equal(toE164(grouped("14165550100")), "+14165550100");
  // A leading 1 is the country code from the first digit, so one digit short stays short and can't be sent.
  assert.equal(grouped("1416555010"), "1 416 555 010");
  assert.equal(toE164("1416555010"), null);
  assert.equal(toE164("0416555010"), null);
  // An international prefix is left as typed, never cut to ten digits that look North American.
  assert.equal(grouped("00442071234567"), "00442071234567");
  assert.equal(toE164("00442071234567"), null);
});

test("a time reads as the board writes it, and a viewing as the board's statusOf says it", () => {
  for (const iso of ["2026-10-31T18:00:00Z", "2026-11-02T05:05:00Z", "2026-12-25T00:30:00Z"]) {
    assert.equal(formatWhen(iso), webFormatWhen(iso), iso);
  }
  assert.equal(formatWhen("not a time"), null);
  // Hermes tags formatToParts' hour and minute as "literal" ("18", ":", "30"), which once read "12: AM" on
  // the phone. Fake that, and the app's times must still match the web's.
  const want = ["2026-10-29T22:30:00Z", "2026-10-31T18:00:00Z"].map((iso) => [iso, webFormatWhen(iso)]);
  const real = Intl.DateTimeFormat.prototype.formatToParts;
  Intl.DateTimeFormat.prototype.formatToParts = function (d) {
    return real.call(this, d).map((p) => (p.type === "hour" || p.type === "minute" ? { ...p, type: "literal" } : p));
  };
  try {
    for (const [iso, web] of want) assert.equal(formatWhen(iso), web, `${iso} with Hermes-tagged parts`);
  } finally {
    Intl.DateTimeFormat.prototype.formatToParts = real;
  }
  assert.equal(formatWhen("2026-10-29T22:30:00Z"), "Thu,\u00a0Oct\u00a029\u00a0· 6:30\u00a0PM");
  for (const status of ["booked", "confirmed", "declined"]) {
    for (const v of [{ starts_at: "2026-10-31T18:00:00Z", slot_text: "Saturday at 2" }, { starts_at: "", slot_text: "Saturday at 2" }]) {
      const viewing = { ...v, status };
      const board = statusOf({ listing_id: "t:1", status: "pending", red_flags: [], viewing });
      const app = viewingStatus(viewing);
      assert.deepEqual([app.words, app.sub], [board.words, board.sub], `${status} ${v.starts_at}`);
    }
  }
  assert.equal(viewingStatus({ starts_at: "", slot_text: "", status: "declined" }).words, "The agent can’t make the viewing");
});
