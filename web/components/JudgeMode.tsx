"use client";

import { useState } from "react";
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
  const line = useJudgeLine();

  async function begin() {
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

  // Done ends the brief the way hanging up ends a phone call: the places the judge picked get called,
  // and the board stays up to watch them. The audio closes either way.
  async function done() {
    if (sessionId) {
      await fetch(api(`/api/judge/${encodeURIComponent(sessionId)}/done`), { method: "POST" }).catch(() => null);
    }
    line.end();
  }

  function again() {
    setSessionId(null);
    setPhase("intro");
  }

  return (
    <LiveBoard
      sessionId={sessionId}
      intro={<Intro phase={phase} waitMin={waitMin} onStart={begin} />}
      dock={<VoiceBar line={line} onDone={done} onAgain={again} />}
    />
  );
}

function Intro({ phase, waitMin, onStart }: { phase: Phase; waitMin: number; onStart: () => void }) {
  const waiting = phase === "asking" || phase === "starting";
  return (
    <div className="space-y-6 pt-2">
      <div>
        <h1 className="text-title font-strong">Talk to Landline</h1>
        <p className="mt-2 text-support text-ink-2">
          Tell it what you&rsquo;re looking for in Toronto. The map follows the conversation, and when you ask, it
          phones the listing agents and brings back what they said.
        </p>
      </div>

      <div>
        <h2 className="text-meta font-label text-ink-3">Try saying</h2>
        <ul className="mt-2 space-y-2">
          {TRY_SAYING.map((s) => (
            <li key={s} className="text-fact text-ink">
              &ldquo;{s}&rdquo;
            </li>
          ))}
        </ul>
      </div>

      <p className="text-meta text-ink-3">
        The listing agents here are simulated, so no real phone rings. Speak in English or your own language; Landline
        answers in yours.
      </p>

      {phase === "denied" && (
        <Notice>
          Landline can&rsquo;t hear you. Allow the microphone for this site (the icon at the left of the address bar),
          then try again.
        </Notice>
      )}
      {phase === "nomic" && <Notice>No microphone found. Plug one in or switch devices, then try again.</Notice>}
      {phase === "soon" && (
        <Notice>Judge mode opens soon. You can look around the live board in the meantime.</Notice>
      )}
      {phase === "limited" && (
        <Notice>
          This network has started a few sessions already. Try again in{" "}
          {waitMin === 1 ? "a minute" : `${waitMin} minutes`}.
        </Notice>
      )}
      {phase === "busy" && <Notice>All lines are busy right now. Try again in a minute.</Notice>}
      {phase === "closed" && (
        <Notice>Judge mode isn&rsquo;t open right now. You can still look around the live board.</Notice>
      )}

      <div className="space-y-3">
        {/* Closed until the browser audio is live: nothing to start, so no button that asks for the mic. */}
        {phase !== "soon" && (
          <>
            <button
              type="button"
              onClick={onStart}
              disabled={waiting}
              className="h-12 w-full rounded-[var(--radius-control)] bg-ink text-support font-label text-white transition-opacity duration-150 disabled:opacity-60"
            >
              {phase === "asking"
                ? "Waiting for the microphone…"
                : phase === "starting"
                  ? "Connecting…"
                  : phase === "intro"
                    ? "Start talking"
                    : "Try again"}
            </button>
            <p className="text-meta text-ink-3">
              {phase === "asking"
                ? "Your browser is asking to use the microphone."
                : "Uses your microphone. You can mute or end any time."}
            </p>
          </>
        )}
        <a href="/?dev=1" className="inline-block text-support text-ink-2 underline underline-offset-2">
          Look around without talking
        </a>
      </div>
    </div>
  );
}

function Notice({ children }: { children: React.ReactNode }) {
  return (
    <p
      role="alert"
      className="rounded-[var(--radius-card)] bg-surface p-3 text-support text-ink"
      style={{ boxShadow: "0 0 0 1px var(--color-line)" }}
    >
      {children}
    </p>
  );
}

function VoiceBar({
  line,
  onDone,
  onAgain,
}: {
  line: ReturnType<typeof useJudgeLine>;
  onDone: () => void;
  onAgain: () => void;
}) {
  const over = line.state === "ended" || line.state === "dropped";
  const label =
    line.state === "connecting"
      ? "Connecting…"
      : line.state === "ended"
        ? "Brief done"
        : line.state === "dropped"
          ? "The line dropped"
          : line.talking
            ? "Landline is talking"
            : line.muted
              ? "Muted"
              : "Listening";

  return (
    <div className="flex items-center gap-3">
      <div className="flex min-w-0 flex-1 items-center gap-2.5" aria-live="polite">
        {!over && <Meter level={line.level} talking={line.talking} />}
        <p className="truncate text-support font-label text-ink">{label}</p>
      </div>
      {over ? (
        <>
          {/* A dropped line can still end the brief, so what the judge picked gets called. */}
          {line.state === "dropped" && <Secondary onClick={onDone}>Done</Secondary>}
          <button
            type="button"
            onClick={onAgain}
            className="h-10 shrink-0 rounded-[var(--radius-control)] bg-ink px-4 text-support font-label text-white"
          >
            Start again
          </button>
        </>
      ) : (
        <>
          <Secondary onClick={line.toggleMute} pressed={line.muted} disabled={line.state !== "live"}>
            {line.muted ? "Unmute" : "Mute"}
          </Secondary>
          <button
            type="button"
            onClick={onDone}
            className="h-10 shrink-0 rounded-[var(--radius-control)] bg-ink px-4 text-support font-label text-white"
          >
            Done
          </button>
        </>
      )}
    </div>
  );
}

function Secondary({
  children,
  onClick,
  pressed,
  disabled,
}: {
  children: React.ReactNode;
  onClick: () => void;
  pressed?: boolean;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={pressed}
      disabled={disabled}
      className="h-10 shrink-0 rounded-[var(--radius-control)] bg-surface px-3 text-support font-label text-ink hover:bg-press disabled:opacity-60"
      style={{ boxShadow: "0 0 0 1px var(--color-line)" }}
    >
      {children}
    </button>
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
