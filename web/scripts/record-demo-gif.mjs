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
import { spawnSync } from "node:child_process";
import { mkdtempSync, rmSync, statSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { button, launch, sleep } from "./lib/cdp.mjs";
import { frameList, recordFrames } from "./lib/screencast.mjs";

const WEB = (process.env.WEB || "http://localhost:3000").replace(/\/+$/, "");
const PHONE = process.env.VIEW === "phone";
const [W, H] = PHONE ? [390, 844] : [1280, 800];
const OUT = resolve(process.env.OUT || "landline-demo.gif");
const WIDTH = Number(process.env.WIDTH || (PHONE ? 390 : 960));
const FPS = Number(process.env.FPS || 12);
const FFMPEG = process.env.FFMPEG || "/opt/homebrew/bin/ffmpeg";
const CARDS = `document.querySelectorAll('ol[aria-label="Best matches, ranked"] > li > button')`;

const work = mkdtempSync(join(tmpdir(), "landline-gif-"));
try {
  const page = await launch({ args: ["--hide-scrollbars"] });
  const frames = recordFrames(page, work);
  const click = (label) => page.read(`${button(label)}.click()`);
  try {
    await page.send("Emulation.setDeviceMetricsOverride", { width: W, height: H, deviceScaleFactor: PHONE ? 2 : 1, mobile: PHONE });
    const sid = `gif-${Date.now().toString(36)}`;
    await page.send("Page.navigate", { url: `${WEB}/s/${sid}?dev=1` });
    await page.until(`${CARDS}.length > 0 && document.querySelectorAll('button.pin').length > 0 && !!(${button("Call the top 4")})`,
      40000, "the board and its pins");
    await sleep(2500); // tiles and the first fit, before recording starts
    await page.send("Page.startScreencast", { format: "jpeg", quality: 85, maxWidth: W * (PHONE ? 2 : 1), maxHeight: H * (PHONE ? 2 : 1), everyNthFrame: 1 });

    await sleep(1500); // the board as the renter first sees it
    // What the renter says, typed into the demo controls: a lower budget re-ranks the board.
    await page.read(`(() => { const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
      const s = document.getElementById('demo-budget'); set.call(s, '2200'); s.dispatchEvent(new Event('input', { bubbles: true })); })()`);
    await click("Say it");
    await sleep(3000);
    await click("Call the top 4");
    await page.until(`!!(${button("Hang up")})`, 10000, "Hang up");
    await sleep(1800);
    await click("Hang up");
    await page.until(`!!(${button("Start over")})`, 45000, "the results");
    await sleep(3000);
    // The booked viewing, opened.
    const booked = await page.read(`[...document.querySelectorAll('button.pin')].find(p => p.dataset.status === 'booked')?.textContent ?? null`);
    if (booked) {
      await page.read(`${CARDS}[${Number(booked) - 1}]?.click()`);
      await sleep(3500);
    }
    await page.send("Page.stopScreencast");
  } finally {
    await page.close();
  }

  if (frames.length < 2) throw new Error("no frames recorded");
  const end = frames[frames.length - 1].at + 2; // hold the last frame for 2 s
  const ff = spawnSync(FFMPEG, ["-loglevel", "error", "-threads", "2", "-y", "-f", "concat", "-safe", "0", "-i", frameList(frames, end, work),
    "-vf", `fps=${FPS},scale=${WIDTH}:-1:flags=lanczos,split[a][b];[a]palettegen=stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle`,
    "-loop", "0", OUT], { stdio: "inherit" });
  if (ff.status !== 0) throw new Error(`ffmpeg failed (${FFMPEG}; set FFMPEG)`);
  const seconds = end - frames[0].at;
  console.log(`${OUT}: ${seconds.toFixed(1)} s, ${(statSync(OUT).size / 1e6).toFixed(1)} MB (${frames.length} frames at ${WIDTH} px, ${FPS} fps)`);
} catch (e) {
  console.error(e.message);
  process.exitCode = 1;
} finally {
  rmSync(work, { recursive: true, force: true });
}
