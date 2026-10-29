// formatWhen: the board's one time format, in Toronto, never relative. Run with `npm test`.
import assert from "node:assert/strict";
import { test } from "node:test";
import { formatWhen, viewingWhen } from "./time.ts";

const plain = (s: string | null) => s?.replace(/ /g, " ") ?? null;

test("a time reads as weekday, month and day, then the clock, in Toronto", () => {
  assert.equal(plain(formatWhen("2026-10-31T18:00:00Z")), "Sat, Oct 31 · 2:00 PM");   // EDT, UTC-4
  assert.equal(plain(formatWhen("2026-10-31T14:00:00-04:00")), "Sat, Oct 31 · 2:00 PM");
  assert.equal(plain(formatWhen("2026-11-02T19:30:00Z")), "Mon, Nov 2 · 2:30 PM");   // EST from Nov 1, UTC-5
});

test("noon, midnight and the minutes are written out", () => {
  assert.equal(plain(formatWhen("2026-10-31T16:00:00Z")), "Sat, Oct 31 · 12:00 PM");
  assert.equal(plain(formatWhen("2026-11-01T04:00:00Z")), "Sun, Nov 1 · 12:00 AM");
  assert.equal(plain(formatWhen("2026-10-31T13:05:00Z")), "Sat, Oct 31 · 9:05 AM");
});

test("a late UTC time is still the Toronto day", () => {
  assert.equal(plain(formatWhen("2026-11-01T02:30:00Z")), "Sat, Oct 31 · 10:30 PM");
});

test("the date and the clock each hold together; a line can only wrap after the dot", () => {
  const t = formatWhen("2026-10-31T18:00:00Z")!;
  assert.deepEqual(t.split(" "), ["Sat, Oct 31 ·", "2:00 PM"]);
});

test("a time that doesn't parse is null, and a viewing falls back to the agent's own words", () => {
  assert.equal(formatWhen("soon"), null);
  assert.equal(formatWhen(""), null);
  assert.equal(formatWhen(null), null);
  assert.equal(viewingWhen({ starts_at: "soon", slot_text: "Saturday at 2 pm" }), "Saturday at 2 pm");
  assert.equal(plain(viewingWhen({ starts_at: "2026-10-31T18:00:00Z", slot_text: "Saturday at 2 pm" })),
    "Sat, Oct 31 · 2:00 PM");
});
