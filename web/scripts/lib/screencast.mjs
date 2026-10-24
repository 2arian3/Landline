// Recording a page with headless Chrome's screencast, for record-demo-gif.mjs and record-demo-video.mjs.
import { writeFileSync } from "node:fs";
import { join } from "node:path";

/** Saves every screencast frame into `dir` as it arrives. Returns the frames: { file, at } in seconds. */
export function recordFrames(page, dir) {
  const frames = [];
  page.on((m) => {
    if (m.method !== "Page.screencastFrame") return;
    const file = join(dir, `f${String(frames.length).padStart(5, "0")}.jpg`);
    writeFileSync(file, Buffer.from(m.params.data, "base64"));
    frames.push({ file, at: m.params.metadata.timestamp });
    page.send("Page.screencastFrameAck", { sessionId: m.params.sessionId }).catch(() => {});
  });
  return frames;
}

/**
 * ffmpeg's concat list for the frames. The screencast only sends a frame when the page changes, so each
 * frame lasts until the next one, and the last until `end` (seconds, the screencast's clock).
 */
export function frameList(frames, end, dir) {
  const path = join(dir, "frames.txt");
  const lines = frames.map((f, i) => `file '${f.file}'\nduration ${((frames[i + 1]?.at ?? end) - f.at).toFixed(3)}`);
  writeFileSync(path, `${lines.join("\n")}\nfile '${frames[frames.length - 1].file}'\n`);
  return path;
}
