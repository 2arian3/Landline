"""The audio sim (E-04): the real voice paths on Higgs Realtime with audio, and the timing math.

The other side is an `Ear`, a transport like a browser tab or a Twilio stream: it streams speech clips in real
time (`TICK_S` frames, silence between lines, as a phone line does), plays the agent's audio on a clock, and
can talk over the agent to test barge-in. `Tap` wraps the Higgs connection and timestamps every event both
ways. The Concierge runs through `voice/bridge.converse` (24 kHz PCM, the browser path) and the Caller through
`voice/phone_caller.PhoneCaller` (μ-law, the phone path), unchanged.

The numbers come from pure functions over that timeline (`first_audio`, `barge_ins`), so `make test` checks
them with no network:
- time to first audio: from the end of the other side's speech (the last voiced frame sent) to the first
  `response.output_audio.delta`, and from Higgs' `speech_stopped` to it;
- barge-in: from the start of the interruption's voice to `speech_started` and to the transport's clear;
  what had played by then against what the server's record of that reply says; and whether the next reply
  answers the interruption (its words or its tool arguments hold the interruption's keyword).
"""
from __future__ import annotations

import asyncio
import base64
import io
import json
import re
import statistics
import time
import wave
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

TICK_S = 0.04              # one frame of input audio
SPEAK_AFTER_S = 1.2        # the other side waits this long after the agent's audio has played out
BARGE_AFTER_S = 1.2        # how far into an agent reply the other side starts talking over it
BARGE_MIN_S = 2.5          # only replies at least this long are talked over
DETECT_WITHIN_MS = 2000    # a barge-in counts only if Higgs heard it within this
NO_REPLY_S = 15.0          # the other side moves on if the agent says nothing for this long
VOICED = 500               # |sample| above this counts as voice (16-bit)


# ── audio formats ────────────────────────────────────────────────────────────

def read_wav(path: Path) -> tuple[list[int], int]:
    with wave.open(str(path)) as w:
        assert w.getnchannels() == 1 and w.getsampwidth() == 2, path
        raw = w.readframes(w.getnframes())
        return list(memoryview(raw).cast("h")), w.getframerate()


def write_wav(path: Path, samples: list[int], rate: int) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm16(samples))


def wav_samples(data: bytes) -> tuple[list[int], int]:
    with wave.open(io.BytesIO(data)) as w:
        return list(memoryview(w.readframes(w.getnframes())).cast("h")), w.getframerate()


def resample(samples: list[int], src: int, dst: int) -> list[int]:
    """Linear interpolation; plain enough for speech clips going to 8 or 24 kHz."""
    if src == dst or not samples:
        return list(samples)
    n = int(len(samples) * dst / src)
    out = []
    for i in range(n):
        x = i * src / dst
        j = int(x)
        frac = x - j
        a = samples[j]
        b = samples[j + 1] if j + 1 < len(samples) else a
        out.append(int(a + (b - a) * frac))
    return out


def pcm16(samples: list[int]) -> bytes:
    return b"".join(max(-32768, min(32767, s)).to_bytes(2, "little", signed=True) for s in samples)


def ulaw(samples: list[int]) -> bytes:
    """G.711 μ-law, as Twilio sends it."""
    out = bytearray()
    for s in samples:
        sign = 0x80 if s < 0 else 0
        m = min(-s if s < 0 else s, 32635) + 0x84
        exponent = max(0, m.bit_length() - 8)
        mantissa = (m >> (exponent + 3)) & 0x0F
        out.append(~(sign | (exponent << 4) | mantissa) & 0xFF)
    return bytes(out)


def onset_ms(samples: list[int], rate: int) -> int:
    """Where the voice starts in a clip: TTS leaves a little silence at the front."""
    for i, s in enumerate(samples):
        if abs(s) > VOICED:
            return round(i * 1000 / rate)
    return 0


def voiced_ms(samples: list[int], rate: int) -> int:
    """Where the voice ends in a clip."""
    for i in range(len(samples) - 1, -1, -1):
        if abs(samples[i]) > VOICED:
            return round((i + 1) * 1000 / rate)
    return 0


@dataclass(frozen=True)
class Format:
    name: str           # pcm24 | pcmu
    rate: int
    bytes_per_sample: int
    silence: bytes      # one sample of silence

    @property
    def bytes_per_second(self) -> int:
        return self.rate * self.bytes_per_sample

    def encode(self, samples: list[int]) -> bytes:
        return ulaw(samples) if self.name == "pcmu" else pcm16(samples)


PCM24 = Format("pcm24", 24000, 2, b"\x00\x00")
PCMU8 = Format("pcmu", 8000, 1, b"\xff")


@dataclass(frozen=True)
class Clip:
    id: str
    text: str
    audio: bytes        # in the session's format
    onset_ms: int       # where the voice starts
    voice_end_ms: int   # where it ends
    keyword: str = ""   # for an interruption: what the agent's next reply should pick up (a regex)

    @classmethod
    def load(cls, path: Path, fmt: Format, text: str, keyword: str = "") -> Clip:
        samples, rate = read_wav(path)
        samples = resample(samples, rate, fmt.rate)
        return cls(path.stem, text, fmt.encode(samples), onset_ms(samples, fmt.rate),
                   voiced_ms(samples, fmt.rate), keyword)


# ── the timeline ─────────────────────────────────────────────────────────────

class Timeline:
    """Every mark of one run, in seconds on one monotonic clock."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self.clock, self.t0, self.marks = clock, clock(), []

    def mark(self, kind: str, **data: Any) -> None:
        self.marks.append({"t": round(self.clock() - self.t0, 4), "kind": kind, **data})


class Tap:
    """A Higgs session that timestamps every event both ways (the `kind` is `in:<type>` / `out:<type>`)."""

    def __init__(self, higgs: Any, timeline: Timeline,
                 played: Callable[[], int | None] | None = None) -> None:
        """With `played` (how many ms of the agent's reply have played, None when nothing is playing), the
        tap also sends `conversation.item.truncate` for that reply on `speech_started`: the fix the bridge
        doesn't have yet, tried here so the report can say whether Higgs takes it."""
        self.higgs, self.timeline, self.played = higgs, timeline, played
        self.audio_item: str | None = None

    async def send(self, event: dict[str, Any]) -> None:
        kind = event.get("type", "")
        if kind != "input_audio_buffer.append":      # 25 a second: not worth a mark each
            self.timeline.mark(f"out:{kind}", **_brief(event))
        await self.higgs.send(event)

    async def events(self):
        async for event in self.higgs.events():
            kind = event.get("type", "")
            self.timeline.mark(f"in:{kind}", **_brief(event))
            if kind == "response.output_audio.delta":
                self.audio_item = event.get("item_id") or self.audio_item
            elif kind == "input_audio_buffer.speech_started" and self.played and self.audio_item:
                ms = self.played()
                if ms is not None:
                    await self.send({"type": "conversation.item.truncate", "item_id": self.audio_item,
                                     "content_index": 0, "audio_end_ms": ms})
            yield event

    async def close(self) -> None:
        await self.higgs.close()


def _brief(event: dict[str, Any]) -> dict[str, Any]:
    """What the metrics need from an event; never the audio itself."""
    out: dict[str, Any] = {}
    for key in ("item_id", "response_id", "audio_start_ms", "audio_end_ms", "content_index", "name"):
        if key in event:
            out[key] = event[key]
    if event.get("type") == "response.output_audio.delta":
        out["bytes"] = len(event.get("delta") or "") * 3 // 4
    if event.get("type") == "response.output_audio_transcript.done":
        out["transcript"] = event.get("transcript", "")
    if event.get("type") == "conversation.item.input_audio_transcription.completed":
        out["transcript"] = event.get("transcript", "")
    if event.get("type") == "response.function_call_arguments.done":
        out["arguments"] = event.get("arguments", "")
    if event.get("type") == "response.done":
        response = event.get("response") or {}
        out["status"] = response.get("status")
        out["response_id"] = response.get("id")
    if event.get("type") == "error":
        out["error"] = json.dumps(event.get("error"))[:300]
    if event.get("type") == "conversation.item.truncate":
        out["item_id"], out["audio_end_ms"] = event.get("item_id"), event.get("audio_end_ms")
    return out


# ── the other side ───────────────────────────────────────────────────────────

class Ear:
    """The human end of the call, as a `Transport` (voice/bridge.py). It says its lines in order, each once
    the agent's audio has played out, and hangs up after the last. With `barges`, it talks over that many
    agent replies, `BARGE_AFTER_S` into each, with interruption clips in turn."""

    def __init__(self, fmt: Format, lines: list[Clip], timeline: Timeline, *, first: bool = False,
                 barges: int = 0, interruptions: list[Clip] | None = None,
                 max_s: float = 240.0) -> None:
        self.fmt, self.lines, self.timeline = fmt, list(lines), timeline
        self.audio_format = ({"type": "audio/pcmu"} if fmt.name == "pcmu"
                             else {"type": "audio/pcm", "rate": 24000})
        self.bytes_per_second = fmt.bytes_per_second
        self.queue = bytearray()
        self.speaking: Clip | None = None
        self.turn_ready = first               # the landlord speaks first on a listing call
        self.barges, self.interruptions = barges, list(interruptions or [])
        self.barged = 0
        self.play_start = self.play_until = 0.0   # the agent's reply now playing, on the timeline clock
        self.reply_bytes = 0
        self.last_audio = -1.0
        self.done_at: float | None = None
        self.max_s = max_s
        self._next_tick = None

    def now(self) -> float:
        return self.timeline.clock() - self.timeline.t0

    # Transport

    async def receive_audio(self) -> str | None:
        """The next frame, paced to real time: speech when a line is under way, else silence."""
        loop_now = time.monotonic()
        if self._next_tick is None:
            self._next_tick = loop_now
        self._next_tick += TICK_S
        await asyncio.sleep(max(0.0, self._next_tick - time.monotonic()))
        now = self.now()
        if now > self.max_s or (self.done_at is not None and now > self.done_at):
            self.timeline.mark("ear:hangup")
            return None
        self._maybe_speak(now)
        n = int(self.fmt.bytes_per_second * TICK_S)
        if self.queue:
            frame, self.queue = bytes(self.queue[:n]), self.queue[n:]
            if not self.queue and self.speaking is not None:
                clip = self.speaking
                tail = len(clip.audio) / self.fmt.bytes_per_second * 1000 - clip.voice_end_ms
                self.timeline.mark("ear:speech_end", clip=clip.id, voice_end_s=round(now - tail / 1000, 4),
                                   barge=bool(clip.keyword))
                self.speaking = None
            frame += self.fmt.silence * ((n - len(frame)) // self.fmt.bytes_per_sample)
        else:
            frame = self.fmt.silence * (n // self.fmt.bytes_per_sample)
        return base64.b64encode(frame).decode()

    async def send_audio(self, b64: str) -> None:
        now = self.now()
        size = len(b64) * 3 // 4
        if self.play_until <= now and now - self.last_audio > 0.5:   # a new reply starts playing
            self.play_start, self.reply_bytes = now, 0
            self.timeline.mark("ear:reply_starts")
        self.last_audio = now
        self.reply_bytes += size
        self.play_until = max(self.play_until, now) + size / self.fmt.bytes_per_second

    def played_ms(self) -> int | None:
        """How much of the agent's reply has played, or None when nothing is playing (for `Tap`)."""
        now = self.now()
        if self.play_until <= now:
            return None
        return round((now - self.play_start) * 1000)

    async def send_clear(self) -> None:
        now = self.now()
        playing = self.play_until > now
        played = max(0.0, min(now, self.play_until) - self.play_start)
        self.timeline.mark("ear:clear", played_ms=round(played * 1000), playing=playing,
                           reply_ms=round(self.reply_bytes / self.fmt.bytes_per_second * 1000))
        self.play_until = min(self.play_until, now)

    # Turn-taking

    def _maybe_speak(self, now: float) -> None:
        if self.speaking is not None:
            return
        playing = self.play_until > now
        reply_s = self.reply_bytes / self.fmt.bytes_per_second
        if playing and self.barged < self.barges and self.interruptions and reply_s >= BARGE_MIN_S \
                and now - self.play_start >= BARGE_AFTER_S:
            clip = self.interruptions[self.barged % len(self.interruptions)]
            self.barged += 1
            self._say(clip, now)
            return
        if playing:
            return
        if self._said_at >= 0 and self.last_audio <= self._said_at and now - self._said_at > NO_REPLY_S:
            self.timeline.mark("ear:no_reply")       # the agent said nothing back: carry on regardless
            self.last_audio = now
        if self.turn_ready or (
            self.last_audio >= 0
            and now - max(self.play_until, self.last_audio) >= SPEAK_AFTER_S
            and self.last_audio > self._said_at
        ):
            self.turn_ready = False
            if self.lines:
                self._say(self.lines.pop(0), now)
            elif self.done_at is None:
                self.done_at = now + 2.0

    _said_at = -1.0

    def _say(self, clip: Clip, now: float) -> None:
        self.queue += clip.audio
        self.speaking = clip
        self._said_at = now
        self.timeline.mark("ear:speech_start", clip=clip.id,
                           voice_start_s=round(now + clip.onset_ms / 1000, 4),
                           barge=bool(clip.keyword), keyword=clip.keyword)


# ── the numbers ──────────────────────────────────────────────────────────────

@dataclass
class FirstAudio:
    clip: str
    from_speech_end_ms: int | None     # the last voiced frame sent → first audio delta
    from_speech_stopped_ms: int | None  # Higgs' speech_stopped → first audio delta


def first_audio(marks: list[dict[str, Any]]) -> list[FirstAudio]:
    """One entry per line the other side finished (interruptions excluded): how long until the agent's
    audio started. A line the agent never answered gets None."""
    out = []
    for i, m in enumerate(marks):
        if m["kind"] != "ear:speech_end" or m.get("barge"):
            continue
        rest = marks[i + 1:]
        nxt = next((j for j, x in enumerate(rest) if x["kind"] == "ear:speech_start"), len(rest))
        window = rest[:nxt]
        delta = next((x["t"] for x in window if x["kind"] == "in:response.output_audio.delta"), None)
        stopped = next((x["t"] for x in window if x["kind"] == "in:input_audio_buffer.speech_stopped"), None)
        voice_end = m.get("voice_end_s", m["t"])
        out.append(FirstAudio(
            m["clip"],
            None if delta is None else round((delta - voice_end) * 1000),
            None if delta is None or stopped is None else round((delta - stopped) * 1000)))
    return out


def greeting_audio_ms(marks: list[dict[str, Any]]) -> int | None:
    """From the first response.create sent to the first audio delta (the Concierge's greeting)."""
    sent = next((m["t"] for m in marks if m["kind"] == "out:response.create"), None)
    delta = next((m["t"] for m in marks if m["kind"] == "in:response.output_audio.delta"
                  and sent is not None and m["t"] >= sent), None)
    return None if sent is None or delta is None else round((delta - sent) * 1000)


@dataclass
class BargeIn:
    clip: str
    keyword: str
    detected_ms: int | None        # voice onset → speech_started
    cleared_ms: int | None         # voice onset → the transport's clear (what the person hears stop)
    played_ms: int | None          # agent audio that had played when it was cleared
    server_end_ms: int | None      # audio_end_ms of a truncate for the interrupted reply, if any was sent
    interrupted_status: str | None  # response.done status of the reply talked over
    answer: str                    # the agent's next words after the interruption
    answered: bool                 # its words or tool arguments pick up the keyword
    ok: bool = False
    why: list[str] = field(default_factory=list)
    acked_end_ms: int | None = None   # audio_end_ms in Higgs' conversation.item.truncated, if it sent one
    truncate_error: str = ""          # Higgs' error after our truncate, if any


def barge_ins(marks: list[dict[str, Any]]) -> list[BargeIn]:
    out = []
    for i, m in enumerate(marks):
        if m["kind"] != "ear:speech_start" or not m.get("barge"):
            continue
        onset = m.get("voice_start_s", m["t"])
        before, rest = marks[:i], marks[i + 1:]
        nxt = next((j for j, x in enumerate(rest) if x["kind"] == "ear:speech_start"), len(rest))
        window = rest[:nxt]
        started = next((x["t"] for x in window if x["kind"] == "in:input_audio_buffer.speech_started"), None)
        clear = next((x for x in window if x["kind"] == "ear:clear"), None)
        truncate = next((x for x in window if x["kind"] == "out:conversation.item.truncate"), None)
        last_reply = next((x.get("response_id") for x in reversed(before)
                           if x["kind"] == "in:response.output_audio.delta" and x.get("response_id")), None)
        status = next((x.get("status") for x in marks if x["kind"] == "in:response.done"
                       and x.get("response_id") == last_reply), None) if last_reply else None
        stopped_at = next((j for j, x in enumerate(window)
                           if x["kind"] == "in:input_audio_buffer.speech_stopped"), None)
        after = window[stopped_at + 1:] if stopped_at is not None else []
        words = " ".join(x.get("transcript", "") for x in after
                         if x["kind"] == "in:response.output_audio_transcript.done")
        args = " ".join(x.get("arguments", "") for x in after
                        if x["kind"] == "in:response.function_call_arguments.done")
        answered = bool(m.get("keyword")) and bool(re.search(m["keyword"], f"{words} {args}", re.I))
        b = BargeIn(m["clip"], m.get("keyword", ""),
                    None if started is None else round((started - onset) * 1000),
                    None if clear is None else round((clear["t"] - onset) * 1000),
                    None if clear is None else clear.get("played_ms"),
                    None if truncate is None else truncate.get("audio_end_ms"),
                    status, words.strip(), answered)
        if truncate is not None:
            after_cut = window[window.index(truncate) + 1:]
            ack = next((x for x in after_cut if x["kind"] == "in:conversation.item.truncated"), None)
            err = next((x for x in after_cut[:20] if x["kind"] == "in:error"), None)
            b.acked_end_ms = None if ack is None else ack.get("audio_end_ms")
            b.truncate_error = "" if err is None or ack is not None else str(err.get("error", ""))[:120]
        if b.detected_ms is None or b.detected_ms > DETECT_WITHIN_MS:
            b.why.append("not heard in time")
        if b.cleared_ms is None:
            b.why.append("audio never cleared")
        if not answered:
            b.why.append("next reply didn't pick it up")
        b.ok = not b.why
        out.append(b)
    return out


def summary(values: list[int | None]) -> dict[str, Any]:
    got = sorted(v for v in values if v is not None)
    if not got:
        return {"n": 0, "missing": len(values), "p50": None, "p90": None}
    return {"n": len(got), "missing": len(values) - len(got), "p50": round(statistics.median(got)),
            "p90": got[min(len(got) - 1, int(0.9 * len(got)))]}


def to_json(items: list[Any]) -> list[dict[str, Any]]:
    return [asdict(x) for x in items]
