// Headless Chrome over the DevTools protocol, for the scripts in web/scripts (no dependencies).
//
//   const page = await launch();
//   try { ...page.send / page.read / page.until... } finally { await page.close(); }
//
// Chrome picks a free debugging port (written to DevToolsActivePort in its own temp profile), so a
// leftover Chrome from an earlier run can't answer for this one. Every call times out, a CDP error or an
// exception thrown in the page rejects instead of passing as `undefined`, and close() (awaited) kills
// Chrome and removes its profile.
import { spawn } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const CHROME = process.env.CHROME || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

export async function launch({ args = [], timeoutMs = 15000 } = {}) {
  const profile = mkdtempSync(join(tmpdir(), "landline-chrome-"));
  const chrome = spawn(CHROME, ["--headless=new", "--remote-debugging-port=0", "--no-first-run", "--mute-audio",
    `--user-data-dir=${profile}`, ...args], { stdio: "ignore" });
  const exited = new Promise((done) => chrome.once("exit", done));
  // Chrome writes to its profile while it shuts down: wait for it (2 s at most), then remove the profile.
  const close = async () => {
    chrome.kill();
    await Promise.race([exited, sleep(2000)]);
    rmSync(profile, { recursive: true, force: true, maxRetries: 10, retryDelay: 100 });
  };
  try {
    const deadline = Date.now() + timeoutMs;
    const portFile = join(profile, "DevToolsActivePort");
    while (!existsSync(portFile) || !readFileSync(portFile, "utf8").includes("\n")) {
      if (Date.now() > deadline) throw new Error(`no headless Chrome at ${CHROME} (set CHROME)`);
      await sleep(100);
    }
    const port = readFileSync(portFile, "utf8").split("\n")[0];
    let target;
    while (!target) {
      if (Date.now() > deadline) throw new Error("Chrome started but never opened a page");
      target = await fetch(`http://127.0.0.1:${port}/json/list`).then((r) => r.json())
        .then((list) => list.find((t) => t.type === "page")).catch(() => undefined);
      if (!target) await sleep(100);
    }
    const ws = new WebSocket(target.webSocketDebuggerUrl);
    await new Promise((ok, fail) => ((ws.onopen = ok), (ws.onerror = () => fail(new Error("DevTools socket failed")))));
    return session(ws, () => {
      ws.close();
      return close();
    });
  } catch (e) {
    await close();
    throw e;
  }
}

function session(ws, close) {
  let seq = 0;
  const replies = new Map();
  const listeners = new Set();
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.id) return replies.get(m.id)?.(m);
    for (const fn of listeners) fn(m);
  };
  ws.onclose = () => {
    for (const [, done] of replies) done({ error: { message: "DevTools socket closed" } });
  };

  /** A DevTools command; rejects on a CDP error or after `timeoutMs`. */
  const send = (method, params = {}, timeoutMs = 15000) => new Promise((ok, fail) => {
    const id = ++seq;
    const timer = setTimeout(() => {
      replies.delete(id);
      fail(new Error(`${method} timed out`));
    }, timeoutMs);
    replies.set(id, (m) => {
      clearTimeout(timer);
      replies.delete(id);
      if (m.error) fail(new Error(`${method}: ${m.error.message}`));
      else ok(m.result);
    });
    ws.send(JSON.stringify({ id, method, params }));
  });

  /** An expression's value in the page (promises awaited); an exception in the page rejects. */
  const read = async (expression) => {
    const r = await send("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true });
    if (r.exceptionDetails) {
      throw new Error(`in the page: ${r.exceptionDetails.exception?.description ?? r.exceptionDetails.text}`);
    }
    return r.result.value;
  };

  /** Waits for an expression to be truthy; throws naming `what` when it isn't within `ms`. */
  const until = async (expression, ms, what) => {
    for (const t = Date.now(); Date.now() - t < ms; await sleep(150)) if (await read(expression)) return;
    throw new Error(`timed out waiting for ${what}`);
  };

  /** Every DevTools event (console messages, screencast frames…). */
  const on = (fn) => listeners.add(fn);

  return { send, read, until, on, close };
}

/** A button by its exact text, as an expression. */
export const button = (label) =>
  `[...document.querySelectorAll('button')].find(b => b.textContent.trim() === ${JSON.stringify(label)})`;
