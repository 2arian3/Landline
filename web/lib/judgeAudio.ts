"use client";

import { useCallback, useEffect, useRef, useState } from "react";

/**
 * Browser audio for judge mode (U-06), talking to the browser transport (A-03)
 * at the `ws_url` that POST /api/judge/start returns.
 *
 * WIRE FORMAT (agree changes with A-03; change it here only):
 *   - binary frames, both ways: PCM16 little-endian, mono, 24 kHz
 *     (Higgs `audio/pcm` at 24000, proxied through our server)
 *   - text frames from the server are JSON; `{"type":"clear"}` stops audio
 *     already queued (barge-in). Anything else is ignored here.
 *
 * Everything the page shows comes from real signals: the mic level is the
 * microphone's RMS, "Landline is talking" is audio actually queued to play.
 */

export type LineState = "idle" | "connecting" | "live" | "ended" | "dropped";

const RATE = 24000;
const SEND_EVERY = 960; // samples: 40 ms at 24 kHz

const WORKLET = `
class LandlineMic extends AudioWorkletProcessor {
  constructor() { super(); this.ratio = sampleRate / ${RATE}; this.pos = 0; }
  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (!ch) return true;
    const out = [];
    while (this.pos < ch.length) {
      const i = Math.floor(this.pos), f = this.pos - i;
      const a = ch[i], b = i + 1 < ch.length ? ch[i + 1] : a;
      out.push(a + (b - a) * f);
      this.pos += this.ratio;
    }
    this.pos -= ch.length;
    let sum = 0;
    for (let i = 0; i < ch.length; i++) sum += ch[i] * ch[i];
    const pcm = new Int16Array(out.length);
    for (let i = 0; i < out.length; i++) {
      const s = Math.max(-1, Math.min(1, out[i]));
      pcm[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
    }
    this.port.postMessage({ pcm: pcm.buffer, rms: Math.sqrt(sum / ch.length) }, [pcm.buffer]);
    return true;
  }
}
registerProcessor("landline-mic", LandlineMic);
`;

/** Asks for the microphone. Resolves to the stream, or to why not. */
export async function askForMic(): Promise<MediaStream | "denied" | "unavailable"> {
  if (!navigator.mediaDevices?.getUserMedia) return "unavailable";
  try {
    return await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 },
    });
  } catch (e) {
    const name = (e as DOMException)?.name;
    return name === "NotAllowedError" || name === "SecurityError" ? "denied" : "unavailable";
  }
}

export function useJudgeLine() {
  const [state, setState] = useState<LineState>("idle");
  const [muted, setMuted] = useState(false);
  const [level, setLevel] = useState(0); // 0..1, smoothed
  const [talking, setTalking] = useState(false); // Landline's audio is playing

  const ws = useRef<WebSocket | null>(null);
  const ctx = useRef<AudioContext | null>(null);
  const stream = useRef<MediaStream | null>(null);
  const queued = useRef<AudioBufferSourceNode[]>([]);
  const playUntil = useRef(0);
  const rms = useRef(0);
  const raf = useRef(0);
  const ending = useRef(false);

  const teardown = useCallback(() => {
    cancelAnimationFrame(raf.current);
    queued.current.forEach((s) => {
      try {
        s.stop();
      } catch {
        // already stopped
      }
    });
    queued.current = [];
    stream.current?.getTracks().forEach((t) => t.stop());
    stream.current = null;
    void ctx.current?.close().catch(() => {});
    ctx.current = null;
    setLevel(0);
    setTalking(false);
  }, []);

  const start = useCallback(
    async (mic: MediaStream, wsUrl: string) => {
      ending.current = false;
      setState("connecting");
      stream.current = mic;
      const ac = new AudioContext();
      ctx.current = ac;
      let node: AudioWorkletNode;
      try {
        await ac.audioWorklet.addModule(URL.createObjectURL(new Blob([WORKLET], { type: "text/javascript" })));
        node = new AudioWorkletNode(ac, "landline-mic");
        ac.createMediaStreamSource(mic).connect(node);
      } catch {
        // No audio worklet here: say the line dropped rather than "Connecting…" for ever. Unless Done closed
        // the audio while it was being set up: then the brief has already ended.
        teardown();
        if (!ending.current) setState("dropped");
        return;
      }
      // Done pressed while the audio was being set up: the brief is over, so no socket opens after it.
      if (ending.current) {
        teardown();
        return;
      }

      const sock = new WebSocket(wsUrl);
      sock.binaryType = "arraybuffer";
      ws.current = sock;

      let pending: Int16Array[] = [];
      let pendingLen = 0;
      node.port.onmessage = (ev: MessageEvent<{ pcm: ArrayBuffer; rms: number }>) => {
        rms.current = ev.data.rms;
        if (sock.readyState !== WebSocket.OPEN) return;
        const chunk = new Int16Array(ev.data.pcm);
        pending.push(chunk);
        pendingLen += chunk.length;
        if (pendingLen < SEND_EVERY) return;
        const out = new Int16Array(pendingLen);
        let o = 0;
        for (const c of pending) {
          out.set(c, o);
          o += c.length;
        }
        pending = [];
        pendingLen = 0;
        sock.send(out.buffer);
      };

      sock.onopen = () => setState("live");
      // A normal close (1000) is the brief ending: the judge pressed Done, or Landline said goodbye and
      // ended it (end_session), and the session goes on to its calls. Anything else (a refusal, 1008 or
      // 1013, or a lost connection, 1006) is a dropped line.
      sock.onclose = (ev) => {
        teardown();
        setState(ending.current || ev.code === 1000 ? "ended" : "dropped");
      };
      sock.onerror = () => sock.close();
      sock.onmessage = (ev) => {
        if (typeof ev.data === "string") {
          try {
            if ((JSON.parse(ev.data) as { type?: string }).type === "clear") {
              queued.current.forEach((s) => {
                try {
                  s.stop();
                } catch {
                  // already stopped
                }
              });
              queued.current = [];
              playUntil.current = 0;
            }
          } catch {
            // not ours
          }
          return;
        }
        const pcm = new Int16Array(ev.data as ArrayBuffer);
        if (!pcm.length) return;
        const buf = ac.createBuffer(1, pcm.length, RATE);
        const ch = buf.getChannelData(0);
        for (let i = 0; i < pcm.length; i++) ch[i] = pcm[i] / 0x8000;
        const src = ac.createBufferSource();
        src.buffer = buf;
        src.connect(ac.destination);
        const at = Math.max(ac.currentTime + 0.02, playUntil.current);
        src.start(at);
        playUntil.current = at + buf.duration;
        queued.current.push(src);
        src.onended = () => {
          queued.current = queued.current.filter((s) => s !== src);
        };
      };

      // ~15 updates a second is plenty for a meter and a "talking" flag.
      let last = 0;
      const tick = (t: number) => {
        if (t - last > 66) {
          last = t;
          setLevel((l) => l * 0.5 + Math.min(1, rms.current * 6) * 0.5);
          setTalking(!!ctx.current && ctx.current.currentTime < playUntil.current);
        }
        raf.current = requestAnimationFrame(tick);
      };
      raf.current = requestAnimationFrame(tick);
    },
    [teardown],
  );

  const toggleMute = useCallback(() => {
    setMuted((m) => {
      stream.current?.getAudioTracks().forEach((t) => (t.enabled = m));
      return !m;
    });
  }, []);

  const end = useCallback(() => {
    ending.current = true;
    if (ws.current && ws.current.readyState <= WebSocket.OPEN) ws.current.close(1000, "renter ended");
    else {
      teardown();
      setState("ended");
    }
  }, [teardown]);

  useEffect(() => () => {
    ending.current = true;
    ws.current?.close();
    teardown();
  }, [teardown]);

  return { state, muted, level: muted ? 0 : level, talking, start, toggleMute, end };
}
