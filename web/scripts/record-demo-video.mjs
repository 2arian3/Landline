// Records the demo video (U-09): one continuous take of the ?dev=1 demo at 1440 CSS px, rendered at 1080p,
// with a title card, a caption for each beat, a "Simulated calls" label while the calls run, and an end card.
// The cards and captions are drawn in the page itself, so they use the app's own type; headless Chrome's
// screencast keeps each frame's real timing, and ffmpeg writes an H.264 MP4.
//
//   1. the API:          DEMO_TICK_S=2.5 make dev      (scripted calls slow enough to read their captions)
//   2. the web app:      cd web && npm run build && npm start      (a production build: no dev badge)
//   3. this recording:   node web/scripts/record-demo-video.mjs
//
// WEB is the web app (default http://localhost:3000), OUT the MP4 (default ./landline-demo.mp4).
// The end card's numbers come from E-05: ACCURACY, UNGROUNDED and FIRST_AUDIO (placeholders until set).
// FFMPEG and CHROME point at the binaries if they aren't where macOS and Homebrew put them.
import { spawn, spawnSync } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

const WEB = (process.env.WEB || "http://localhost:3000").replace(/\/+$/, "");
const OUT = resolve(process.env.OUT || "landline-demo.mp4");
const FFMPEG = process.env.FFMPEG || "/opt/homebrew/bin/ffmpeg";
const CHROME = process.env.CHROME || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const STATS = {
  accuracy: process.env.ACCURACY || "[E-05: extraction accuracy]",
  ungrounded: process.env.UNGROUNDED || "[E-05: ungrounded facts]",
  firstAudio: process.env.FIRST_AUDIO || "[E-05: time to first audio]",
};
const TITLE = "Landline: a voice agent that works for renters, not brokerages.";
const CAPTIONS = {
  board: "A renter tells Landline what they need. Toronto’s listings re-rank as they talk.",
  budget: "“Under $2,200.” The board re-ranks at once.",
  plan: "“Call the top four. I’m free Saturday afternoon.”",
  hangUp: "They hang up. Landline calls all four listing agents at the same time.",
  live: "Every call is transcribed live, and each fact keeps the agent’s own words.",
  results: "Results land on the map: what each call found, a red flag, a booked viewing.",
  booked: "A viewing booked for Saturday at 2 pm, inside the renter’s window.",
  welcome: "Next call: “Welcome back.” Landline remembers each renter by phone number.",
};
const [W, H, SCALE] = [1440, 810, 1920 / 1440]; // 16:9 at 1440 CSS px, drawn at 1920x1080
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const work = mkdtempSync(join(tmpdir(), "landline-video-"));
const chrome = spawn(CHROME, ["--headless=new", "--remote-debugging-port=9338", "--no-first-run", "--hide-scrollbars", "--mute-audio",
  `--user-data-dir=${join(work, "profile")}`], { stdio: "ignore" });
let target;
for (let i = 0; i < 60 && !target; i++) {
  try {
    target = (await (await fetch("http://127.0.0.1:9338/json/list")).json()).find((t) => t.type === "page");
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
const CARDS = `[...document.querySelectorAll('ol[aria-label="Best matches, ranked"] > li > button')]`;
const openCard = (listingId, view) => {
  const address = view.board.find((c) => c.listing_id === listingId)?.address ?? "";
  return read(`${CARDS}.find(b => b.getAttribute('aria-label').includes(${JSON.stringify(address)}))?.click()`);
};
const back = () => read(`[...document.querySelectorAll('button')].find(b => /All places/.test(b.textContent))?.click()`);
const scrollPanel = (y) => read(`document.querySelector('aside .overflow-y-auto')?.scrollTo({ top: ${y}, behavior: 'smooth' })`);
const zoomOut = async (steps) => { // back out to the whole board after a place's close-up
  for (let i = 0; i < steps; i++) { await read(`document.querySelector('.maplibregl-ctrl-zoom-out')?.click()`); await sleep(350); }
};
// The live captions sit at the end of the detail: scroll the panel to their heading (the deepest element
// that says "On the call now"; document order puts it last).
const showCaptions = () => read(`[...document.querySelectorAll('aside *')].filter(e => /On the call now/.test(e.textContent))
  .at(-1)?.scrollIntoView({ behavior: 'smooth', block: 'start' })`);

// The video's own layer over the app: cards, the caption and the "Simulated calls" label, in the app's type
// and ink (live-map-ui: solid surfaces, sentence case, no gradients). The caption sits over the map, low.
const OVERLAY = `(() => {
  const css = document.createElement('style');
  css.textContent = \`
    #v-card { position: fixed; inset: 0; z-index: 100; display: grid; place-content: center; gap: 28px; padding: 0 120px;
      background: #1a1815; color: #fff; text-align: center; transition: opacity 400ms ease; }
    #v-card h1 { margin: 0; font-size: 46px; line-height: 1.2; font-weight: 600; letter-spacing: -0.01em; }
    #v-card p { margin: 0; font-size: 22px; line-height: 1.5; color: rgba(255,255,255,.78); }
    #v-cap { position: fixed; z-index: 90; left: 32px; right: 472px; bottom: 36px; display: flex; justify-content: center;
      pointer-events: none; transition: opacity 300ms ease; }
    #v-cap span { max-width: 860px; padding: 16px 24px; border-radius: 16px; background: #1a1815; color: #fff;
      font-size: 27px; line-height: 1.3; font-weight: 500; text-align: center; box-shadow: 0 6px 24px rgba(26,24,21,.25); }
    #v-sim { position: fixed; z-index: 90; top: 20px; left: 20px; padding: 8px 14px; border-radius: 12px; background: #fff;
      color: #1a1815; font-size: 17px; font-weight: 600; box-shadow: 0 0 0 1px rgba(26,24,21,.14); transition: opacity 300ms ease; }\`;
  document.head.append(css);
  const el = (id) => { const e = document.createElement('div'); e.id = id; e.style.opacity = '0'; document.body.append(e); return e; };
  const card = el('v-card'), cap = el('v-cap'), sim = el('v-sim');
  sim.textContent = 'Simulated calls';
  window.__v = {
    card(title, lines) { card.innerHTML = ''; const h = document.createElement('h1'); h.textContent = title; card.append(h);
      for (const l of lines) { const p = document.createElement('p'); p.textContent = l; card.append(p); } card.style.opacity = '1'; },
    hideCard() { card.style.opacity = '0'; },
    caption(text) { cap.innerHTML = ''; const s = document.createElement('span'); s.textContent = text; cap.append(s); cap.style.opacity = '1'; },
    sim(on) { sim.style.opacity = on ? '1' : '0'; },
  };
})()`;
const v = (call) => read(`window.__v.${call}`);

let sid;
let stoppedAt = 0; // when the take ended, in the screencast's clock (seconds since the epoch)
const view = async () => (await fetch(`${WEB}/api/sessions/${sid}`)).json();

try {
  await send("Emulation.setDeviceMetricsOverride", { width: W, height: H, deviceScaleFactor: SCALE, mobile: false });
  sid = `video-${Date.now().toString(36)}`;
  await send("Page.navigate", { url: `${WEB}/s/${sid}?dev=1` });
  await until(`${CARDS}.length > 0 && document.querySelectorAll('button.pin').length > 0 && !!(${button("Call the top 4")})`,
    40000, "the board and its pins");
  await read(OVERLAY);
  await v(`card(${JSON.stringify(TITLE)}, [])`);
  await sleep(3000); // tiles and the first fit settle under the title card, before recording starts
  await send("Page.startScreencast", { format: "jpeg", quality: 88, maxWidth: 1920, maxHeight: 1080, everyNthFrame: 1 });

  // Title.
  await sleep(4000);
  await v("hideCard()");
  await sleep(600);

  // 1. The board.
  await v(`caption(${JSON.stringify(CAPTIONS.board)})`);
  for (let i = 0; i < 6; i++) { // the renter's eye moving down the list: each card raises its pin
    await read(`${CARDS}[${i}]?.dispatchEvent(new MouseEvent('mouseover', { bubbles: true }))`);
    await sleep(2400);
    await read(`${CARDS}[${i}]?.dispatchEvent(new MouseEvent('mouseout', { bubbles: true }))`);
  }
  await sleep(2000);

  // 2. The budget drops and the board re-ranks.
  await v(`caption(${JSON.stringify(CAPTIONS.budget)})`);
  await sleep(1800);
  await read(`(() => { const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
    const s = document.getElementById('demo-budget'); set.call(s, '2200'); s.dispatchEvent(new Event('input', { bubbles: true })); })()`);
  await click("Say it");
  await sleep(12000);

  // 3. The renter picks the places to call.
  await v(`caption(${JSON.stringify(CAPTIONS.plan)})`);
  await sleep(1800);
  await click("Call the top 4");
  await until(`!!(${button("Hang up")})`, 10000, "Hang up");
  await sleep(11000);

  // 4. They hang up; the calls go out.
  await v(`caption(${JSON.stringify(CAPTIONS.hangUp)})`);
  await v("sim(true)");
  await sleep(1500);
  await click("Hang up");
  const planned = (await view()).plan;
  const booked = planned[2]; // the scripted booking: the third place in the plan
  for (const t = Date.now(); Date.now() - t < 20000; await sleep(200)) {
    if ((await view()).calls.some((c) => c.state === "ringing")) break;
  }
  await sleep(5000);

  // 5. A live call's captions, in its place's detail.
  for (const t = Date.now(); Date.now() - t < 30000; await sleep(200)) {
    if ((await view()).calls.find((c) => c.listing_id === booked)?.state === "live") break;
  }
  await openCard(booked, await view());
  await v(`caption(${JSON.stringify(CAPTIONS.live)})`);
  const liveAt = Date.now();
  await sleep(1200);
  await showCaptions();
  for (let t = Date.now(); Date.now() - t < 40000 && (await view()).phase !== "done"; ) await sleep(300);
  await sleep(Math.max(1500, 9000 - (Date.now() - liveAt)));

  // 6. The results.
  await back();
  await v("sim(false)");
  await scrollPanel(0);
  await v(`caption(${JSON.stringify(CAPTIONS.results)})`);
  await zoomOut(3);
  await sleep(9000);
  await scrollPanel(420);
  await sleep(6000);
  await scrollPanel(0);
  await sleep(3000);
  // the red flag, then a fact in the agent's own words, each in its place's detail
  for (const status of ["suspect", "verified"]) {
    const lid = (await view()).board.find((c) => c.status === status)?.listing_id;
    if (!lid) continue;
    await openCard(lid, await view());
    await sleep(6500);
    await back();
    await sleep(1200);
  }

  // 7. The booked viewing.
  await openCard(booked, await view());
  await v(`caption(${JSON.stringify(CAPTIONS.booked)})`);
  await sleep(14000);

  // 8. The next call: welcome back.
  await back();
  await sleep(800);
  await click("Start over");
  await until(`!!(${button("Call the top 4")})`, 10000, "a fresh brief");
  await read(`document.getElementById('demo-returning')?.click()`);
  await scrollPanel(0);
  await v(`caption(${JSON.stringify(CAPTIONS.welcome)})`);
  await zoomOut(3);
  await sleep(14000);

  // End card.
  await v(`card("Built on Higgs Realtime · Team AAMEL", ${JSON.stringify([
    `Extraction accuracy: ${STATS.accuracy}`, `Ungrounded facts shown: ${STATS.ungrounded}`, `Time to first audio: ${STATS.firstAudio}`,
  ])})`);
  await sleep(4400);
  await send("Page.stopScreencast");
  stoppedAt = Date.now() / 1000;
} finally {
  ws.close();
  chrome.kill();
}

if (frames.length < 2) {
  console.error("no frames recorded");
  process.exit(1);
}
// Each frame lasts until the next one (the screencast only sends a frame when the page changes), and the
// last one until the take ended, so the end card holds.
const end = Math.max(stoppedAt, frames[frames.length - 1].at + 0.5);
const list = frames.map((f, i) => `file '${f.file}'\nduration ${((frames[i + 1]?.at ?? end) - f.at).toFixed(3)}`);
writeFileSync(join(work, "frames.txt"), `${list.join("\n")}\nfile '${frames[frames.length - 1].file}'\n`);
const ff = spawnSync(FFMPEG, ["-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", join(work, "frames.txt"),
  "-vf", "fps=30,scale=1920:1080:flags=lanczos,format=yuv420p", "-c:v", "libx264", "-preset", "medium", "-crf", "20",
  "-threads", "2", "-movflags", "+faststart", "-an", OUT], { stdio: "inherit" });
rmSync(work, { recursive: true, force: true });
if (ff.status !== 0) {
  console.error(`ffmpeg failed (${FFMPEG}; set FFMPEG)`);
  process.exit(1);
}
const seconds = end - frames[0].at;
const m = Math.floor(seconds / 60);
console.log(`${OUT}: ${m}:${String(Math.round(seconds % 60)).padStart(2, "0")} (${seconds.toFixed(1)} s), 1920x1080, ${frames.length} frames`);
