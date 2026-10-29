// Checks that the board keeps following its session when the event stream stalls without an error,
// the way it did behind a Cloudflare quick tunnel (U-03). Runs a proxy in front of the API whose event
// stream misbehaves, opens a session page through it in headless Chrome, changes the session on the API
// directly, and reports how long the page took to show the change.
//
//   1. the API:          make dev                               (API_UPSTREAM, default :8000)
//   2. the web app:      cd web && API_URL=http://localhost:8002 npm run dev -- -p 3011
//   3. this check:       node web/scripts/check-stalled-stream.mjs
//
// Modes, each on a fresh session: "headers" answers the stream and then never writes a byte, "silent"
// never answers it, "none" passes it through (the healthy case). Then two more: "down" refuses every
// /api request for the first 14 s (the panel should say it can't reach Landline, and the board and the
// catalogue should come back on their own), and "slow-first" holds the first snapshot past the page's
// 4 s timeout and fails it while the stream is fine (the board should still come). Exits 1 if any page
// stays stuck.
import { spawn } from "node:child_process";
import { mkdtempSync } from "node:fs";
import http from "node:http";
import { tmpdir } from "node:os";
import { join } from "node:path";

const API = new URL(process.env.API_UPSTREAM || "http://localhost:8000");
const WEB = process.env.WEB || "http://localhost:3011";
const CHROME = process.env.CHROME || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

let mode = "headers";
let downUntil = 0;
const counts = { streams: 0, snapshots: 0, catalogue: 0 };
const proxy = http.createServer((req, res) => {
  const path = req.url.split("?")[0];
  const snapshot = /^\/api\/sessions\/[^/]+$/.test(path);
  if (path.endsWith("/stream")) counts.streams += 1;
  else if (snapshot) counts.snapshots += 1;
  if (mode === "down" && Date.now() < downUntil) return void res.writeHead(502).end();
  if (mode === "slow-first" && snapshot && counts.snapshots === 1) return void setTimeout(() => res.writeHead(502).end(), 6000);
  if (path.endsWith("/stream") && (mode === "headers" || mode === "silent")) {
    if (mode === "headers") res.writeHead(200, { "content-type": "text/event-stream" });
    return; // and nothing more
  }
  const up = http.request({ host: API.hostname, port: API.port, method: req.method, path: req.url, headers: req.headers },
    (r) => {
      if (path === "/api/catalogue" && r.statusCode === 200) counts.catalogue += 1;
      res.writeHead(r.statusCode, r.headers);
      r.pipe(res);
    });
  up.on("error", () => res.writeHead(502).end());
  res.on("close", () => up.destroy());
  req.pipe(up);
});
await new Promise((ok) => proxy.listen(8002, ok));

const chrome = spawn(CHROME, ["--headless=new", "--remote-debugging-port=9335", "--no-first-run",
  `--user-data-dir=${mkdtempSync(join(tmpdir(), "landline-stall-"))}`], { stdio: "ignore" });
let target;
for (let i = 0; i < 60 && !target; i++) {
  try {
    target = (await (await fetch("http://127.0.0.1:9335/json/list")).json()).find((t) => t.type === "page");
  } catch {
    await sleep(250);
  }
}
const ws = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((ok) => (ws.onopen = ok));
let seq = 0;
const replies = new Map();
ws.onmessage = (ev) => {
  const m = JSON.parse(ev.data);
  replies.get(m.id)?.(m.result);
};
const send = (method, params = {}) => new Promise((ok) => replies.set(++seq, ok) && ws.send(JSON.stringify({ id: seq, method, params })));
const read = async (expr) => (await send("Runtime.evaluate", { expression: expr, returnByValue: true })).result.value;
await send("Emulation.setDeviceMetricsOverride", { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false });

// The panel's "What Landline heard" line, and the status next to the brand ("Live" or "Reconnecting…").
const HEARD = "[...document.querySelectorAll('p')].find(p => p.previousElementSibling?.textContent === 'What Landline heard')?.textContent ?? ''";
const STATUS = "document.querySelector('[aria-live=polite]')?.textContent ?? ''";
const UNREACHABLE = "/Can.t reach Landline/.test(document.body.innerText)";

let failed = false;
for (mode of ["headers", "silent", "none"]) {
  counts.streams = counts.snapshots = 0;
  const sid = `stall-${mode}-${Date.now().toString(36)}`;
  await send("Page.navigate", { url: `${WEB}/s/${sid}` });
  for (let t = Date.now(); Date.now() - t < 30000 && !(await read(HEARD)); ) await sleep(250);
  await sleep(12000); // sit on the stalled stream for a while, as on the phone
  const before = await read(STATUS);
  await fetch(new URL(`/api/sessions/${sid}/preferences`, API), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ max_monthly: 2200, min_beds: 1 }),
  });
  const t0 = Date.now();
  while (Date.now() - t0 < 20000 && !(await read(HEARD)).includes("$2,200")) await sleep(250);
  const shown = (await read(HEARD)).includes("$2,200");
  failed ||= !shown;
  console.log(`${mode.padEnd(7)} ${shown ? `updated ${((Date.now() - t0) / 1000).toFixed(1)}s after the change` : "STUCK"}` +
    ` | status after 12s: ${before}, now: ${await read(STATUS)} | ${counts.streams} streams, ${counts.snapshots} snapshots`);
}

mode = "down";
counts.streams = counts.snapshots = counts.catalogue = 0;
{
  await send("Network.clearBrowserCache"); // the earlier modes' catalogue would answer from the cache
  downUntil = Date.now() + 14000;
  await send("Page.navigate", { url: `${WEB}/s/down-${Date.now().toString(36)}` });
  await sleep(12000);
  const said = await read(UNREACHABLE);
  while (Date.now() < downUntil) await sleep(250);
  const t0 = Date.now();
  while (Date.now() - t0 < 15000 && !(await read(HEARD))) await sleep(250);
  const back = (Date.now() - t0) / 1000;
  const shown = !!(await read(HEARD));
  for (let t = Date.now(); Date.now() - t < 10000 && !counts.catalogue; ) await sleep(250); // retries every 8 s at most
  failed ||= !(said && shown && counts.catalogue);
  console.log(`down    ${said ? "said it can't reach Landline" : "NO unreachable line"} | ` +
    `${shown ? `board back ${back.toFixed(1)}s after the API` : "board NEVER came back"} | ` +
    `catalogue ${counts.catalogue ? "loaded" : "NEVER loaded"}`);
}

mode = "slow-first";
counts.streams = counts.snapshots = counts.catalogue = 0;
{
  const t0 = Date.now();
  await send("Page.navigate", { url: `${WEB}/s/slow-${Date.now().toString(36)}` });
  while (Date.now() - t0 < 20000 && !(await read(HEARD))) await sleep(250);
  const shown = !!(await read(HEARD));
  failed ||= !shown;
  console.log(`slow-first ${shown ? `board after ${((Date.now() - t0) / 1000).toFixed(1)}s` : "STUCK on skeletons"}` +
    ` (first snapshot held 6 s, then failed) | ${counts.snapshots} snapshots`);
}
ws.close();
chrome.kill();
proxy.close();
process.exit(failed ? 1 : 0);
