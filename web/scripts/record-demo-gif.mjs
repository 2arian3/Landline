// Records the ?dev=1 hand-off as a GIF for the README (U-09): the board loads, a budget change re-ranks it,
// "Call the top 4", "Hang up", the scripted calls ring and come back, and the booked place opens. Headless
// Chrome's screencast keeps each frame's real timing, and ffmpeg turns the frames into a GIF.
//
//   1. the API:          make dev
//   2. the web app:      cd web && npm run build && npm start      (a production build: no dev badge)
//   3. this recording:   node web/scripts/record-demo-gif.mjs
//
// WEB is the web app (default http://localhost:3000).
// VIEW=desktop (1280x800, the default) or VIEW=phone (390x844). OUT is the GIF (default ./landline-demo.gif).
// WIDTH is the GIF's width in pixels (default 960 desktop, 390 phone), FPS its frame rate (default 12).
// FFMPEG and CHROME point at the binaries if they aren't where macOS and Homebrew put them.
import { spawn, spawnSync } from "node:child_process";
import { mkdtempSync, rmSync, statSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

const WEB = (process.env.WEB || "http://localhost:3000").replace(/\/+$/, "");
const PHONE = process.env.VIEW === "phone";
const [W, H] = PHONE ? [390, 844] : [1280, 800];
const OUT = resolve(process.env.OUT || "landline-demo.gif");
const WIDTH = Number(process.env.WIDTH || (PHONE ? 390 : 960));
const FPS = Number(process.env.FPS || 12);
const FFMPEG = process.env.FFMPEG || "/opt/homebrew/bin/ffmpeg";
const CHROME = process.env.CHROME || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const work = mkdtempSync(join(tmpdir(), "landline-gif-"));
const chrome = spawn(CHROME, ["--headless=new", "--remote-debugging-port=9337", "--no-first-run", "--hide-scrollbars",
  `--user-data-dir=${join(work, "profile")}`], { stdio: "ignore" });
let target;
for (let i = 0; i < 60 && !target; i++) {
  try {
    target = (await (await fetch("http://127.0.0.1:9337/json/list")).json()).find((t) => t.type === "page");
  } catch {
    await sleep(250);
  }
}
if (!target) {
  console.error(`no headless Chrome at ${CHROME} (set CHROME)`);
  process.exit(1);
}
const ws = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((ok) => (ws.onopen = ok));
let seq = 0;
const replies = new Map();
const frames = []; // { file, at } with `at` in seconds, the page's own clock
ws.onmessage = (ev) => {
  const m = JSON.parse(ev.data);
  if (m.id) return replies.get(m.id)?.(m.result);
  if (m.method === "Page.screencastFrame") {
    const file = join(work, `f${String(frames.length).padStart(5, "0")}.jpg`);
    writeFileSync(file, Buffer.from(m.params.data, "base64"));
    frames.push({ file, at: m.params.metadata.timestamp });
    ws.send(JSON.stringify({ id: ++seq, method: "Page.screencastFrameAck", params: { sessionId: m.params.sessionId } }));
  }
};
const send = (method, params = {}) => new Promise((ok) => replies.set(++seq, ok) && ws.send(JSON.stringify({ id: seq, method, params })));
const read = async (expr) => (await send("Runtime.evaluate", { expression: expr, returnByValue: true }))?.result?.value;
const until = async (expr, ms, what) => {
  for (const t = Date.now(); Date.now() - t < ms; await sleep(150)) if (await read(expr)) return;
  throw new Error(`timed out waiting for ${what}`);
};
const button = (label) => `[...document.querySelectorAll('button')].find(b => b.textContent.trim() === ${JSON.stringify(label)})`;
const click = (label) => read(`${button(label)}.click()`);
const CARDS = `document.querySelectorAll('ol[aria-label="Best matches, ranked"] > li > button')`;

try {
  await send("Emulation.setDeviceMetricsOverride", { width: W, height: H, deviceScaleFactor: PHONE ? 2 : 1, mobile: PHONE });
  const sid = `gif-${Date.now().toString(36)}`;
  await send("Page.navigate", { url: `${WEB}/s/${sid}?dev=1` });
  await until(`${CARDS}.length > 0 && document.querySelectorAll('button.pin').length > 0 && !!(${button("Call the top 4")})`,
    40000, "the board and its pins");
  await sleep(2500); // tiles and the first fit, before recording starts
  await send("Page.startScreencast", { format: "jpeg", quality: 85, maxWidth: W * (PHONE ? 2 : 1), maxHeight: H * (PHONE ? 2 : 1), everyNthFrame: 1 });

  await sleep(1500); // the board as the renter first sees it
  // What the renter says, typed into the demo controls: a lower budget re-ranks the board.
  await read(`(() => { const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
    const s = document.getElementById('demo-budget'); set.call(s, '2200'); s.dispatchEvent(new Event('input', { bubbles: true })); })()`);
  await click("Say it");
  await sleep(3000);
  await click("Call the top 4");
  await until(`!!(${button("Hang up")})`, 10000, "Hang up");
  await sleep(1800);
  await click("Hang up");
  await until(`!!(${button("Start over")})`, 45000, "the results");
  await sleep(3000);
  // The booked viewing, opened.
  const booked = await read(`[...document.querySelectorAll('button.pin')].find(p => p.dataset.status === 'booked')?.textContent ?? null`);
  if (booked) {
    await read(`${CARDS}[${Number(booked) - 1}]?.click()`);
    await sleep(3500);
  }
  await send("Page.stopScreencast");
} finally {
  ws.close();
  chrome.kill();
}

if (frames.length < 2) {
  console.error("no frames recorded");
  process.exit(1);
}
// Each frame lasts until the next one; the screencast only sends a frame when the page changes.
const list = frames.map((f, i) => {
  const next = frames[i + 1]?.at ?? f.at + 2; // hold the last frame for 2 s
  return `file '${f.file}'\nduration ${(next - f.at).toFixed(3)}`;
});
writeFileSync(join(work, "frames.txt"), `${list.join("\n")}\nfile '${frames[frames.length - 1].file}'\n`);
const ff = spawnSync(FFMPEG, ["-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", join(work, "frames.txt"),
  "-vf", `fps=${FPS},scale=${WIDTH}:-1:flags=lanczos,split[a][b];[a]palettegen=stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle`,
  "-loop", "0", OUT], { stdio: "inherit" });
rmSync(work, { recursive: true, force: true });
if (ff.status !== 0) {
  console.error(`ffmpeg failed (${FFMPEG}; set FFMPEG)`);
  process.exit(1);
}
const seconds = frames[frames.length - 1].at - frames[0].at + 2;
console.log(`${OUT}: ${seconds.toFixed(1)} s, ${(statSync(OUT).size / 1e6).toFixed(1)} MB (${frames.length} frames at ${WIDTH} px, ${FPS} fps)`);
