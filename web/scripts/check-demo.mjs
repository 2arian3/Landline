// Smoke check of the ?dev=1 demo, to run after each merge. In headless Chrome at 375 px and 1440 px, on a
// fresh session each: the page loads with no console errors, every card has a pin with its number and the
// pins match the card order (U-02's Done when), and the scripted hand-off (Call the top 4, Hang up) reaches
// the results. Exits 1 on any failure.
//
//   1. the API:          make dev
//   2. the web app:      make web
//   3. this check:       node web/scripts/check-demo.mjs
//
// WEB is the web app (default http://localhost:3000). API is where /api answers, if not through WEB.
// SHOTS=<dir> saves a screenshot of each width once the results are in.
import { spawn } from "node:child_process";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const WEB = (process.env.WEB || "http://localhost:3000").replace(/\/+$/, "");
const API = (process.env.API || WEB).replace(/\/+$/, "");
const CHROME = process.env.CHROME || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const SHOTS = process.env.SHOTS;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const chrome = spawn(CHROME, ["--headless=new", "--remote-debugging-port=9336", "--no-first-run",
  `--user-data-dir=${mkdtempSync(join(tmpdir(), "landline-demo-"))}`], { stdio: "ignore" });
let target;
for (let i = 0; i < 60 && !target; i++) {
  try {
    target = (await (await fetch("http://127.0.0.1:9336/json/list")).json()).find((t) => t.type === "page");
  } catch {
    await sleep(250);
  }
}
if (!target) {
  console.error(`no headless Chrome at ${CHROME} (set CHROME)`);
  chrome.kill();
  process.exit(1);
}
const ws = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((ok) => (ws.onopen = ok));
let seq = 0;
const replies = new Map();
const errors = [];
ws.onmessage = (ev) => {
  const m = JSON.parse(ev.data);
  if (m.id) return replies.get(m.id)?.(m.result);
  if (m.method === "Runtime.consoleAPICalled" && m.params.type === "error")
    errors.push(m.params.args.map((a) => a.value ?? a.description ?? "").join(" "));
  else if (m.method === "Runtime.exceptionThrown")
    errors.push(m.params.exceptionDetails.exception?.description ?? m.params.exceptionDetails.text);
  else if (m.method === "Log.entryAdded" && m.params.entry.level === "error")
    errors.push(`${m.params.entry.text}${m.params.entry.url ? ` (${m.params.entry.url})` : ""}`);
};
const send = (method, params = {}) => new Promise((ok) => replies.set(++seq, ok) && ws.send(JSON.stringify({ id: seq, method, params })));
const read = async (expr) => (await send("Runtime.evaluate", { expression: expr, returnByValue: true }))?.result?.value;
const until = async (expr, ms) => {
  for (const t = Date.now(); Date.now() - t < ms; await sleep(250)) if (await read(expr)) return true;
  return false;
};
await send("Runtime.enable");
await send("Log.enable");

// Cards in DOM order as "rank address" (from each card's label, "2. 701 Don Mills Rd, $2,100 a month, …"),
// and the pins as the same, read from their labels ("Number 2: 701 Don Mills Rd").
const CARDS = `[...document.querySelectorAll('ol[aria-label="Best matches, ranked"] > li > button')]
  .map(b => b.getAttribute('aria-label').match(/^(\\d+)\\. (.*?), \\$/)).filter(Boolean).map(m => m[1] + ' ' + m[2])`;
const PINS = `[...document.querySelectorAll('button.pin')]
  .map(p => p.getAttribute('aria-label').replace(/^Number (\\d+): /, '$1 '))`;
const STATUSES = `Object.fromEntries([...document.querySelectorAll('button.pin')]
  .map(p => [p.getAttribute('aria-label').replace(/^Number (\\d+): .*/, '$1'), p.dataset.status]))`;
const button = (label) => `[...document.querySelectorAll('button')].find(b => b.textContent.trim() === ${JSON.stringify(label)})`;
const RESULTS = `document.querySelector('section[aria-label="What the calls found"]')?.textContent ?? null`;

/** The pins against the cards: one per card, same number, same place. Returns what's wrong. */
async function pinsMatchCards() {
  const cards = await read(CARDS);
  const pins = new Set(await read(PINS));
  const wrong = [];
  if (!cards.length) wrong.push("no cards on the board");
  cards.forEach((c, i) => {
    if (!c.startsWith(`${i + 1} `)) wrong.push(`card ${i + 1} in the list is numbered ${c.split(" ")[0]}`);
    if (!pins.has(c)) wrong.push(`no pin for card ${c}`);
  });
  if (pins.size > cards.length) wrong.push(`${pins.size} pins for ${cards.length} cards`);
  return { cards: cards.length, wrong };
}

let failed = false;
const fail = (width, what) => {
  failed = true;
  console.log(`  FAIL ${width}px: ${what}`);
};

for (const [width, height, mobile] of [[375, 812, true], [1440, 900, false]]) {
  errors.length = 0;
  const sid = `smoke-${width}-${Date.now().toString(36)}`;
  await send("Emulation.setDeviceMetricsOverride", { width, height, deviceScaleFactor: mobile ? 2 : 1, mobile });
  await send("Page.navigate", { url: `${WEB}/s/${sid}?dev=1` });
  console.log(`${width}px  ${WEB}/s/${sid}?dev=1`);

  if (!(await until(`(${CARDS}).length > 0 && document.querySelectorAll('button.pin').length > 0 && !!(${button("Call the top 4")})`, 30000))) {
    fail(width, "the board, its pins or the demo controls never showed");
    continue;
  }
  await sleep(1000); // let the first fit and any late re-render settle
  const first = await pinsMatchCards();
  first.wrong.forEach((w) => fail(width, w));
  console.log(`  loaded: ${first.cards} cards${first.wrong.length ? "" : ", each with its pin, in order"}`);

  await read(`${button("Call the top 4")}.click()`);
  if (!(await until(`!!(${button("Hang up")})`, 10000))) {
    fail(width, "\"Call the top 4\" never offered \"Hang up\"");
    continue;
  }
  const planned = await read(`document.querySelectorAll('button.pin[data-mark="will_call"]').length`);
  await read(`${button("Hang up")}.click()`);
  const t0 = Date.now();
  if (!(await until(`!!(${button("Start over")})`, 45000))) {
    fail(width, "the scripted calls never finished (no \"Start over\")");
    continue;
  }
  const view = await (await fetch(`${API}/api/sessions/${sid}`)).json();
  console.log(`  hand-off: ${planned} pins marked to call, results after ${((Date.now() - t0) / 1000).toFixed(1)}s`);
  if (view.phase !== "done" || !view.results) fail(width, `the session ended ${view.phase} with no results text`);
  if (view.calls.some((c) => c.state !== "ended")) fail(width, "a call is still open after the results");
  await sleep(1000);
  const statuses = await read(STATUSES);
  for (const card of view.board) {
    // A missing pin is reported below, with the order.
    if (card.rank in statuses && statuses[card.rank] !== card.status)
      fail(width, `pin ${card.rank} shows ${statuses[card.rank]}, the server says ${card.status}`);
  }
  const shown = await read(RESULTS);
  if (shown !== null && !shown.includes(view.results.slice(0, 40)))
    fail(width, "the results on the page don't match the session's");
  console.log(`  results: ${shown !== null ? "shown atop the panel" : "in the session (no results panel on this build)"}`);
  const last = await pinsMatchCards();
  last.wrong.forEach((w) => fail(width, `after the results, ${w}`));

  if (SHOTS) {
    const { data } = await send("Page.captureScreenshot", { format: "png" });
    const path = join(SHOTS, `demo-${width}.png`);
    writeFileSync(path, Buffer.from(data, "base64"));
    console.log(`  screenshot: ${path}`);
  }
  errors.forEach((e) => fail(width, `console error: ${e}`));
  if (!errors.length) console.log("  console: no errors");
}

ws.close();
chrome.kill();
console.log(failed ? "FAILED" : "ok");
process.exit(failed ? 1 : 0);
