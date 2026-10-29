"use client";

import { useEffect, useState } from "react";
import { Icon, type IconName } from "./Icon";
import { LiveBoard } from "./LiveBoard";
import { askForMic, useJudgeLine } from "@/lib/judgeAudio";
import { api } from "@/lib/live";

type Phase = "intro" | "asking" | "denied" | "nomic" | "starting" | "soon" | "limited" | "busy" | "closed" | "live";

/** Why POST /api/judge/start said no: "closed" until the browser audio is live, "rate_limited", "busy". */
type Refusal = { reason?: string; retry_after_s?: number };

const TRY_SAYING = [
  "A one-bedroom near Bloor, under $2,600.",
  "Natural light matters most.",
  "That one’s too dark.",
  "Call the top three.",
];

/**
 * /try: judges talk to Landline in the browser. The listing agents are
 * simulated (A-03), so no phone rings. The page asks for the microphone only
 * after the judge says they want to talk (the intro is the pre-prompt), and
 * says plainly what to do when something is in the way.
 */
export function JudgeMode() {
  const [phase, setPhase] = useState<Phase>("intro");
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [waitMin, setWaitMin] = useState(1);
  const [doneFailed, setDoneFailed] = useState(false); // Done couldn't reach the server: nothing was ended
  const line = useJudgeLine();

  async function begin() {
    setDoneFailed(false);
    setPhase("asking");
    const mic = await askForMic();
    if (mic === "denied") return setPhase("denied");
    if (mic === "unavailable") return setPhase("nomic");
    setPhase("starting");
    const stop = () => mic.getTracks().forEach((t) => t.stop());
    let res: Response;
    try {
      res = await fetch(api("/api/judge/start"), { method: "POST", signal: AbortSignal.timeout(8000) });
    } catch {
      stop();
      return setPhase("closed");
    }
    if (!res.ok) {
      stop();
      const why = (await res.json().catch(() => ({}))) as Refusal;
      if (why.reason === "closed") return setPhase("soon");
      if (res.status === 429) {
        setWaitMin(Math.max(1, Math.ceil((why.retry_after_s ?? 600) / 60)));
        return setPhase("limited");
      }
      return setPhase(res.status === 503 ? "busy" : "closed");
    }
    const { session_id, ws_url } = (await res.json()) as { session_id: string; ws_url: string };
    setSessionId(session_id);
    setPhase("live");
    await line.start(mic, wsAddress(ws_url));
  }

  // Done ends the brief the way hanging up ends a phone call. The audio closes first, so Landline stops at
  // once; then the places the judge picked get called, and the board stays up to watch them.
  // Pressed while still connecting, it ends the brief all the same (no socket opens after it). If the server
  // can't be reached, the dock says so and Done can be tried again: nothing gets called until it answers.
  async function done() {
    line.end();
    if (!sessionId) return;
    setDoneFailed(false);
    const res = await fetch(api(`/api/judge/${encodeURIComponent(sessionId)}/done`), {
      method: "POST",
      signal: AbortSignal.timeout(8000),
    }).catch(() => null);
    setDoneFailed(!res?.ok);
  }

  // Starting over ends the old session first, so its line is free for the next start.
  function again() {
    setDoneFailed(false);
    if (sessionId) hangUp(sessionId);
    setSessionId(null);
    setPhase("intro");
  }

  // So does leaving the page, instead of holding the line until the server's timeout. Not when the page
  // only goes into the back/forward cache: it may come back, and an open call keeps its line anyway.
  useEffect(() => {
    if (!sessionId) return;
    const leave = (e: PageTransitionEvent) => {
      if (!e.persisted) hangUp(sessionId, true);
    };
    window.addEventListener("pagehide", leave);
    return () => window.removeEventListener("pagehide", leave);
  }, [sessionId]);

  return (
    <LiveBoard
      sessionId={sessionId}
      intro={<Intro phase={phase} waitMin={waitMin} onStart={begin} />}
      dock={<VoiceBar line={line} doneFailed={doneFailed} onDone={done} onAgain={again} />}
      line={line.state === "connecting" || line.state === "dropped" ? LINE_WORDS[line.state] : null}
    />
  );
}

/** What the line's state says, in the dock and (while it isn't live) the header: one wording for both. */
const LINE_WORDS = { connecting: "Connecting…", ended: "Brief done", dropped: "The line dropped" } as const;

/** Frees the judge's line now. Fire and forget; `beacon` for a page that's going away. */
function hangUp(sessionId: string, beacon = false) {
  const url = api(`/api/judge/${encodeURIComponent(sessionId)}/hang-up`);
  if (beacon && navigator.sendBeacon?.(url)) return;
  void fetch(url, { method: "POST", keepalive: true }).catch(() => null);
}

function Intro({ phase, waitMin, onStart }: { phase: Phase; waitMin: number; onStart: () => void }) {
  const waiting = phase === "asking" || phase === "starting";
  return (
    <div className="space-y-6 pt-2">
      <div>
        <h1 className="t-title">Talk to Landline</h1>
        <p className="t-callout c-2 mt-2">
          Tell it what you&rsquo;re looking for in Toronto. The map follows the conversation, and when you ask, it
          phones the listing agents and brings back what they said.
        </p>
      </div>

      {/* Content inside the glass sits on solid white. */}
      <div className="rounded-[var(--radius-md)] bg-surface p-4 shadow-[var(--shadow-hairline)]">
        <h2 className="label">
          <Icon name="message-circle" />
          Try saying
        </h2>
        <ul className="mt-2 space-y-2">
          {TRY_SAYING.map((s) => (
            <li key={s} className="said">
              &ldquo;{s}&rdquo;
            </li>
          ))}
        </ul>
      </div>

      <p className="t-caption c-2">
        The listing agents here are simulated, so no real phone rings. Speak in English or your own language; Landline
        answers in yours.
      </p>

      {phase === "denied" && (
        <Notice glyph="circle-alert">
          Landline can&rsquo;t hear you. Allow the microphone for this site (the icon at the left of the address bar),
          then try again.
        </Notice>
      )}
      {phase === "nomic" && (
        <Notice glyph="circle-alert">No microphone found. Plug one in or switch devices, then try again.</Notice>
      )}
      {phase === "soon" && (
        <Notice glyph="clock">Judge mode opens soon. You can look around the live board in the meantime.</Notice>
      )}
      {phase === "limited" && (
        <Notice glyph="clock">
          This network has started a few sessions already. Try again in{" "}
          {waitMin === 1 ? "a minute" : `${waitMin} minutes`}.
        </Notice>
      )}
      {phase === "busy" && <Notice glyph="clock">All lines are busy right now. Try again in a minute.</Notice>}
      {phase === "closed" && (
        <Notice glyph="circle-alert">Judge mode isn&rsquo;t open right now. You can still look around the live board.</Notice>
      )}

      <div className="space-y-2">
        {/* Closed until the browser audio is live: nothing to start, so no button that asks for the mic. */}
        {phase !== "soon" && (
          <>
            {/* While it waits it stays readable (it says what it's waiting for) and ignores a second press. */}
            <button
              type="button"
              onClick={waiting ? undefined : onStart}
              aria-disabled={waiting || undefined}
              className="btn btn-primary w-full"
            >
              {!waiting && <Icon name="audio-lines" size={20} />}
              {phase === "asking"
                ? "Waiting for the microphone…"
                : phase === "starting"
                  ? "Connecting…"
                  : phase === "intro"
                    ? "Start talking"
                    : "Try again"}
            </button>
            <p className="t-caption c-2">
              {phase === "asking"
                ? "Your browser is asking to use the microphone."
                : "Uses your microphone. The call is recorded. You can mute or end any time."}
            </p>
          </>
        )}
        <a href="/?dev=1" className="btn btn-quiet btn-small -ml-3 gap-1 pr-2!">
          Look around without talking
          <Icon name="chevron-right" size={18} />
        </a>
      </div>
    </div>
  );
}

function Notice({ glyph, children }: { glyph: IconName; children: React.ReactNode }) {
  return (
    <p role="alert" className="banner">
      <Icon name={glyph} />
      <span>{children}</span>
    </p>
  );
}

function VoiceBar({
  line,
  doneFailed,
  onDone,
  onAgain,
}: {
  line: ReturnType<typeof useJudgeLine>;
  doneFailed: boolean;
  onDone: () => void;
  onAgain: () => void;
}) {
  const over = line.state === "ended" || line.state === "dropped";
  const label = doneFailed
    ? "Couldn\u2019t end the brief"
    : line.state === "connecting" || line.state === "ended" || line.state === "dropped"
      ? LINE_WORDS[line.state]
      : line.talking
            ? "Landline is talking"
            : line.muted
              ? "Muted"
              : "Listening";

  return (
    <div className="flex items-center gap-2">
      <div className="flex min-w-0 flex-1 items-center gap-2.5" aria-live="polite">
        {!over && <Meter level={line.level} talking={line.talking} />}
        <p className="t-headline truncate">{label}</p>
      </div>
      {over ? (
        <>
          {/* A dropped line can still end the brief, so what the judge picked gets called; a Done that
              couldn't reach the server can be tried again. */}
          {(line.state === "dropped" || doneFailed) && (
            <button type="button" onClick={onDone} className="btn btn-secondary btn-small shrink-0">
              {doneFailed ? "Try again" : "Done"}
            </button>
          )}
          <button type="button" onClick={onAgain} className="btn btn-primary btn-small shrink-0">
            Start again
          </button>
        </>
      ) : (
        <>
          <button
            type="button"
            onClick={line.toggleMute}
            aria-pressed={line.muted}
            disabled={line.state !== "live"}
            className="btn btn-secondary btn-small shrink-0"
          >
            {line.muted ? "Unmute" : "Mute"}
          </button>
          <button type="button" onClick={onDone} className="btn btn-primary btn-small shrink-0">
            Done
          </button>
        </>
      )}
    </div>
  );
}

/** Five bars: the microphone's real level while listening; a slow wave while Landline's audio plays. */
function Meter({ level, talking }: { level: number; talking: boolean }) {
  const weights = [0.55, 0.85, 1, 0.75, 0.5];
  return (
    <span aria-hidden className="flex h-5 w-[26px] shrink-0 items-center justify-between">
      {weights.map((w, i) => (
        <span
          key={i}
          className={`w-[3px] rounded-full bg-ink ${talking ? "meter-talk" : ""}`}
          style={{
            height: talking ? undefined : `${Math.max(3, Math.round(20 * Math.min(1, level * w * 1.4)))}px`,
            animationDelay: talking ? `${i * 120}ms` : undefined,
            transition: "height 90ms linear",
          }}
        />
      ))}
    </span>
  );
}

/** ws_url may be absolute or a path on the API; make it a ws(s) URL either way. */
function wsAddress(u: string): string {
  if (/^wss?:\/\//.test(u)) return u;
  const base = api("") || window.location.origin;
  const url = new URL(u, base);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  return url.toString();
}
