// Smoke check of the ?dev=1 demo, to run after each merge. In headless Chrome at 375 px and 1440 px, on a
// fresh session each: the page loads with no console errors, every card has a pin with its number and the
// pins match the card order (U-02's Done when; the demo board shows only places with a map point, so a card
// without a pin is a failure), and the scripted hand-off (Call the top 4, Hang up) reaches the results.
// Exits 1 on any failure, and on anything it can't check.
//
//   1. the API:          make dev
//   2. the web app:      make web
//   3. this check:       node web/scripts/check-demo.mjs
//
// WEB is the web app (default http://localhost:3000). API is where /api answers, if not through WEB.
// SHOTS=<dir> saves a screenshot of each width once the results are in.
import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { button, launch, sleep } from "./lib/cdp.mjs";

const WEB = (process.env.WEB || "http://localhost:3000").replace(/\/+$/, "");
const API = (process.env.API || WEB).replace(/\/+$/, "");
const SHOTS = process.env.SHOTS;

// Cards in DOM order as "rank address" (from each card's label, "2. 701 Don Mills Rd, $2,100 a month, …"),
// and the pins as the same, read from their labels ("Number 2: 701 Don Mills Rd").
const CARDS = `[...document.querySelectorAll('ol[aria-label="Best matches, ranked"] > li > button')]
  .map(b => b.getAttribute('aria-label').match(/^(\\d+)\\. (.*?), \\$/)).filter(Boolean).map(m => m[1] + ' ' + m[2])`;
const PINS = `[...document.querySelectorAll('button.pin')]
  .map(p => p.getAttribute('aria-label').replace(/^Number (\\d+): /, '$1 '))`;
// Each pin's status by its place (the address in its label), so a late re-rank can't pair it with another.
const STATUSES = `Object.fromEntries([...document.querySelectorAll('button.pin')]
  .map(p => [p.getAttribute('aria-label').replace(/^Number \\d+: /, ''), p.dataset.status]))`;
const RESULTS = `document.querySelector('section[aria-label="What the calls found"]')?.textContent ?? null`;

let failed = false;
const fail = (width, what) => {
  failed = true;
  console.log(`  FAIL ${width}px: ${what}`);
};

/** The pins against the cards: one per card, same number, same place, and no extras. Returns what's wrong. */
async function pinsMatchCards(page) {
  const cards = await page.read(CARDS);
  const pins = await page.read(PINS);
  const wrong = [];
  if (!cards.length) wrong.push("no cards on the board");
  cards.forEach((c, i) => {
    if (!c.startsWith(`${i + 1} `)) wrong.push(`card ${i + 1} in the list is numbered ${c.split(" ")[0]}`);
    if (!pins.includes(c)) wrong.push(`no pin for card ${c}`);
  });
  if (pins.length > cards.length) wrong.push(`${pins.length} pins for ${cards.length} cards`);
  return { cards: cards.length, wrong };
}

async function session(sid) {
  const res = await fetch(`${API}/api/sessions/${sid}`);
  if (!res.ok) throw new Error(`GET /api/sessions/${sid} answered ${res.status} (set API if /api isn't served through WEB)`);
  return res.json();
}

async function check(page, width, height, mobile) {
  const sid = `smoke-${width}-${Date.now().toString(36)}`;
  await page.send("Emulation.setDeviceMetricsOverride", { width, height, deviceScaleFactor: mobile ? 2 : 1, mobile });
  await page.send("Page.navigate", { url: `${WEB}/s/${sid}?dev=1` });
  console.log(`${width}px  ${WEB}/s/${sid}?dev=1`);

  await page.until(`(${CARDS}).length > 0 && document.querySelectorAll('button.pin').length > 0 && !!(${button("Call the top 4")})`,
    30000, "the board, its pins and the demo controls");
  await sleep(1000); // let the first fit and any late re-render settle
  const first = await pinsMatchCards(page);
  first.wrong.forEach((w) => fail(width, w));
  console.log(`  loaded: ${first.cards} cards${first.wrong.length ? "" : ", each with its pin, in order"}`);

  await page.read(`${button("Call the top 4")}.click()`);
  await page.until(`!!(${button("Hang up")})`, 10000, "\"Hang up\" after \"Call the top 4\"");
  const planned = await page.read(`document.querySelectorAll('button.pin[data-mark="will_call"]').length`);
  if (planned < 1) fail(width, "\"Call the top 4\" marked no pins to call");
  await page.read(`${button("Hang up")}.click()`);
  const t0 = Date.now();
  await page.until(`!!(${button("Start over")})`, 45000, "the scripted calls to finish (\"Start over\")");
  const view = await session(sid);
  console.log(`  hand-off: ${planned} pins marked to call, results after ${((Date.now() - t0) / 1000).toFixed(1)}s`);
  if (view.phase !== "done" || !view.results) fail(width, `the session ended ${view.phase} with no results text`);
  if (view.calls.some((c) => c.state !== "ended")) fail(width, "a call is still open after the results");

  // Every pin shows its place's status from the server, once the page has caught up (up to 5 s).
  const want = Object.fromEntries(view.board.map((c) => [c.address, c.status]));
  const mismatched = async () => {
    const shown = await page.read(STATUSES);
    return Object.entries(shown).filter(([address, status]) => address in want && want[address] !== status)
      .map(([address, status]) => `${address} shows ${status}, the server says ${want[address]}`);
  };
  let off = await mismatched();
  for (const t = Date.now(); off.length && Date.now() - t < 5000; off = await mismatched()) await sleep(250);
  off.forEach((w) => fail(width, `pin for ${w}`));

  const shown = await page.read(RESULTS);
  if (shown !== null && (!view.results || !shown.includes(view.results.slice(0, 40))))
    fail(width, "the results on the page don't match the session's");
  console.log(`  results: ${shown !== null ? "shown atop the panel" : "in the session (no results panel on this build)"}`);
  const last = await pinsMatchCards(page);
  last.wrong.forEach((w) => fail(width, `after the results, ${w}`));

  if (SHOTS) {
    mkdirSync(SHOTS, { recursive: true });
    const { data } = await page.send("Page.captureScreenshot", { format: "png" });
    const path = join(SHOTS, `demo-${width}.png`);
    writeFileSync(path, Buffer.from(data, "base64"));
    console.log(`  screenshot: ${path}`);
  }
}

const page = await launch();
try {
  const errors = [];
  page.on((m) => {
    if (m.method === "Runtime.consoleAPICalled" && m.params.type === "error")
      errors.push(m.params.args.map((a) => a.value ?? a.description ?? "").join(" "));
    else if (m.method === "Runtime.exceptionThrown")
      errors.push(m.params.exceptionDetails.exception?.description ?? m.params.exceptionDetails.text);
    else if (m.method === "Log.entryAdded" && m.params.entry.level === "error")
      errors.push(`${m.params.entry.text}${m.params.entry.url ? ` (${m.params.entry.url})` : ""}`);
  });
  await page.send("Runtime.enable");
  await page.send("Log.enable");
  for (const [width, height, mobile] of [[375, 812, true], [1440, 900, false]]) {
    errors.length = 0;
    try {
      await check(page, width, height, mobile);
    } catch (e) {
      fail(width, e.message);
    } finally {
      // Reported whatever happened above: an error here is often why a step never showed.
      errors.forEach((e) => fail(width, `console error: ${e}`));
      if (!errors.length) console.log("  console: no errors");
    }
  }
} catch (e) {
  failed = true;
  console.log(`  FAIL: ${e.message}`);
} finally {
  await page.close();
}
console.log(failed ? "FAILED" : "ok");
process.exit(failed ? 1 : 0);
