// My search's form logic (src/lib/search.ts): the API's limits, checked before sending, and only what
// changed sent. npm test.
import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { test } from "node:test";

import { edit, formFrom, parseBudget, problems, problemsFrom422, savedFrom, splitList } from "../src/lib/search.ts";

const profile = {
  phone: "+14165550100",
  email: null,
  use_saved_search: false,
  shareable: ["pets", "move_in", "occupants"],
  preferences: { max_monthly: 2600, min_beds: 1, areas: ["Annex"], pets: [], must_have: ["parking"], move_in: "December 1" },
};
const saved = savedFrom(profile);
const form = (patch = {}) => ({ ...formFrom(saved), ...patch });

test("a budget change sends only the budget", () => {
  assert.deepEqual(edit(form({ budget: "$2,800" }), saved), { max_monthly: 2800 });
  assert.deepEqual(edit(form(), saved), {});
});

test("a cleared field is null, an emptied list is [], and words for a move-in day stay untouched", () => {
  assert.deepEqual(edit(form({ budget: "", must_have: " , " }), saved), { max_monthly: null, must_have: [] });
  assert.equal(formFrom(saved).move_in, "");   // "December 1" isn't a date, so the field starts empty
  assert.deepEqual(edit(form({ move_in: "2026-12-01" }), saved), { move_in: "2026-12-01" });
});

test("shareable goes in the API's order, and a studio is 0 bedrooms", () => {
  assert.deepEqual(saved.shareable, ["move_in", "occupants", "pets"]);
  assert.deepEqual(edit(form({ shareable: ["pets", "name"], beds: "0" }), saved),
    { min_beds: 0, shareable: ["name", "pets"] });
  assert.deepEqual(edit(form({ beds: "" }), saved), { min_beds: null });
});

test("the form refuses what the API would, before sending", () => {
  assert.deepEqual(problems(form()), {});
  assert.equal(parseBudget("20,000"), 20000);
  assert.equal(parseBudget("2600.50"), undefined);
  assert.ok(problems(form({ budget: "20001" })).budget);
  assert.ok(problems(form({ budget: "2,600.50" })).budget);
  assert.ok(problems(form({ areas: Array.from({ length: 11 }, (_, i) => `Area ${i}`).join(", ") })).areas);
  assert.ok(problems(form({ pets: "x".repeat(41) })).pets);
  assert.ok(problems(form({ must_have: Array.from({ length: 21 }, (_, i) => `m${i}`).join(",") })).must_have);
  assert.equal(problems(form({ move_in: "2026-02-30" })).move_in, "That isn't a date.");
  assert.equal(problems(form({ move_in: "Dec 1" })).move_in, "That isn't a date.");
  assert.equal(problems(form({ email: "renter@example" })).email, "That isn't an email address.");
  assert.deepEqual(problems(form({ email: " renter@example.com ", move_in: "2026-12-01" })), {});
  assert.deepEqual(splitList("Annex, Leslieville ,"), ["Annex", "Leslieville"]);
});

test("a budget runs from 1 to 20,000 dollars, so 0 is refused here instead of reaching the API's 422", () => {
  for (const refused of ["0", "$0", "20001", "lots"]) {
    assert.equal(problems(form({ budget: refused })).budget, "Use a whole number of dollars, from 1 to 20,000.", refused);
  }
  for (const fine of ["1", "20000", "$20,000", ""]) assert.equal(problems(form({ budget: fine })).budget, undefined, fine);
});

test("a 422 marks the fields it names", () => {
  const body = { detail: [
    { loc: ["body", "max_monthly"], msg: "Input should be less than or equal to 20000" },
    { loc: ["body", "areas", 3], msg: "String should have at most 80 characters" },
    { loc: ["body"], msg: "Field required" },
  ] };
  assert.deepEqual(problemsFrom422(body), {
    budget: "Input should be less than or equal to 20000",
    areas: "String should have at most 80 characters",
  });
  assert.deepEqual(problemsFrom422(null), {});
});

// The web's form (U-12b, web/lib/searchForm.ts) checks and sends the same way; once it's on main, hold the
// two to the same answers.
const WEB = new URL("../../web/lib/searchForm.ts", import.meta.url);
test("the app's form answers as the web's does", { skip: !existsSync(WEB) && "web/lib/searchForm.ts isn't on main yet" },
  async () => {
    const web = await import(WEB.href);
    const forms = [form(), form({ budget: "$2,800", beds: "0" }), form({ budget: "20001", email: "x@y" }), form({ budget: "0" }),
      form({ areas: "", pets: "cat, dog", move_in: "2026-12-01", shareable: ["name"], use_saved_search: true })];
    for (const f of forms) {
      assert.deepEqual(problems(f), web.problems(f));
      assert.deepEqual(edit(f, saved), web.edit(f, web.savedFrom(profile)));
    }
    assert.deepEqual(formFrom(saved), web.formFrom(web.savedFrom(profile)));
  });
