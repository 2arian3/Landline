// The "My search" form's own checks, and that only what changed goes to PATCH /api/me/profile.
// Run with `npm test`.
import assert from "node:assert/strict";
import { test } from "node:test";
import { edit, formFrom, parseBudget, problems, problemsFrom422, type Saved, savedFrom, splitList } from "./searchForm.ts";

const SAVED: Saved = {
  max_monthly: 2600,
  min_beds: 1,
  areas: ["Annex"],
  pets: ["cat"],
  must_have: [],
  move_in: "December 1", // the renter's words from a call: not a date the form can show
  email: null,
  shareable: ["move_in", "pets"],
  use_saved_search: false,
};

test("a budget is a whole number of dollars, typed any usual way", () => {
  assert.equal(parseBudget("2600"), 2600);
  assert.equal(parseBudget("$2,600"), 2600);
  assert.equal(parseBudget(" "), null);
  assert.equal(parseBudget("2600.50"), undefined);
  assert.equal(parseBudget("lots"), undefined);
});

test("lists split on commas", () => {
  assert.deepEqual(splitList("Annex, Leslieville ,"), ["Annex", "Leslieville"]);
  assert.deepEqual(splitList(""), []);
});

test("the form starts from the saved values, and words for a move-in date stay out of the date field", () => {
  const form = formFrom(SAVED);
  assert.deepEqual([form.budget, form.beds, form.areas, form.pets, form.move_in], ["2600", "1", "Annex", "cat", ""]);
});

test("nothing changed sends nothing; a budget change sends only the budget", () => {
  const form = formFrom(SAVED);
  assert.deepEqual(edit(form, SAVED), {});
  assert.deepEqual(edit({ ...form, budget: "$2,400" }, SAVED), { max_monthly: 2400 });
});

test("clearing a field sends null or an empty list", () => {
  const form = formFrom(SAVED);
  assert.deepEqual(edit({ ...form, budget: "", areas: "", beds: "" }, SAVED), {
    max_monthly: null,
    min_beds: null,
    areas: [],
  });
});

test("the email, a real date, what's shared and the switch go as typed", () => {
  const form = formFrom(SAVED);
  const changed = { ...form, email: " sam@example.com ", move_in: "2026-12-01", shareable: ["pets", "name"] as Saved["shareable"], use_saved_search: true };
  assert.deepEqual(edit(changed, SAVED), {
    move_in: "2026-12-01",
    email: "sam@example.com",
    shareable: ["name", "pets"],
    use_saved_search: true,
  });
});

test("a bad budget, date or email is caught before sending", () => {
  const form = formFrom(SAVED);
  assert.deepEqual(Object.keys(problems({ ...form, budget: "2600.5", move_in: "2026-02-30", email: "sam@" })), [
    "budget",
    "move_in",
    "email",
  ]);
  assert.deepEqual(problems(form), {});
});

test("the profile the API returns becomes the form's starting values", () => {
  const saved = savedFrom({
    phone: "+14165550123",
    email: "sara@example.com",
    use_saved_search: true,
    shareable: ["move_in", "occupants", "pets", "something new"],
    preferences: { max_monthly: 2600, min_beds: 1, areas: ["Annex"], move_in: "2026-12-01" },
    updated_at: null,
  });
  assert.deepEqual([saved.email, saved.shareable, saved.pets, saved.must_have], [
    "sara@example.com",
    ["move_in", "occupants", "pets"],
    [],
    [],
  ]);
  assert.equal(formFrom(saved).move_in, "2026-12-01");
});

test("the form keeps to U-12a's limits", () => {
  const form = formFrom(SAVED);
  assert.ok(problems({ ...form, budget: "25000" }).budget);
  assert.ok(problems({ ...form, areas: Array.from({ length: 11 }, (_, n) => `Area ${n}`).join(", ") }).areas);
  assert.ok(problems({ ...form, pets: "a".repeat(41) }).pets);
});

test("a 422 lands on the field it's about", () => {
  const body = {
    detail: [
      { loc: ["body", "max_monthly"], msg: "Input should be a valid integer" },
      { loc: ["body", "areas", 2], msg: "String should have at most 80 characters" },
      { loc: ["body", "email"], msg: "value is not a valid email address" },
    ],
  };
  assert.deepEqual(problemsFrom422(body), {
    budget: "Input should be a valid integer",
    areas: "String should have at most 80 characters",
    email: "Value is not a valid email address",
  });
  const checked = { detail: [{ loc: ["body", "email"], msg: "Value error, not an email address" },
                             { loc: ["body", "move_in"], msg: "Value error, a date written as YYYY-MM-DD" }] };
  assert.deepEqual(problemsFrom422(checked), { email: "Not an email address", move_in: "A date written as YYYY-MM-DD" });
  assert.deepEqual(problemsFrom422({ detail: "sign in with your phone" }), {});
});
