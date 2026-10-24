"""Both sides of a call as audio on one clock, and where each transcript line sits on it (A-10).

The clock is milliseconds since the call connected.

- **The caller's track** is every chunk of their audio sent to Higgs, back to back from 0. Both transports
  stream without gaps (Twilio sends 20 ms frames, the judge's page 40 ms ones, silence included), so a
  byte's place on the track is when it was said. It is also the clock Higgs counts on:
  `input_audio_buffer.speech_started` carries `audio_start_ms` and `speech_stopped` carries
  `audio_end_ms`, both in milliseconds of audio appended so far, with the line's `item_id`. Checked on
  Higgs on 24 Oct: a second utterance 8,680 ms into the audio came back at 8,724.
- **The agent's track** is what the caller heard. Higgs sends audio faster than it plays, so each chunk
  starts when the one before it has finished playing, or now if nothing is playing (the bridge's
  `Playback` keeps the same clock for hanging up). A barge-in drops whatever hadn't played yet, and the
  agent's line ends there.

    rec = CallRecorder(format_of(transport.audio_format))
    rec.heard(b64)                        # each chunk of the caller's audio sent to Higgs
    rec.played(b64, item_id)              # each chunk of Higgs's audio sent to the caller
    rec.speech_started(item_id, 1236)     # and rec.speech_stopped(item_id, 4500)
    rec.cut()                             # barge-in
    start_ms, end_ms = rec.span(item_id)  # when its transcript arrives; then rec.stored(item_id, idx)
    chunks, moved = rec.take()            # every few seconds, then rec.finish() when the call ends

`take` hands out what is ready to store: the caller's audio so far, and the agent's audio that has
already played, so nothing stored is ever taken back. It also hands out the stored lines whose place
changed since (an agent line cut short by a barge-in). `render_wav` turns stored chunks back into a WAV.
"""
from __future__ import annotations

import base64
import io
import math
import time
import wave
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from landline.db.rows import AudioRow


@dataclass(frozen=True)
class Format:
    name: str                   # call_audio.format: pcmu | pcm16
    rate: int                   # samples a second
    width: int                  # bytes a sample

    @property
    def bytes_per_ms(self) -> int:
        return self.rate * self.width // 1000


PCMU_8K = Format("pcmu", 8000, 1)       # the phone: G.711 μ-law
PCM16_24K = Format("pcm16", 24000, 2)   # the judge's browser


def format_of(audio_format: dict[str, Any]) -> Format:
    """The format of a Higgs audio format (voice.higgs.PCMU or PCM_24K)."""
    if audio_format.get("type") == "audio/pcmu":
        return PCMU_8K
    if audio_format.get("type") == "audio/pcm":
        return Format("pcm16", int(audio_format.get("rate") or 24000), 2)
    raise ValueError(f"no recording format for {audio_format!r}")


@dataclass(frozen=True)
class Chunk:
    """Audio ready to store: one call_audio row but its seq."""
    track: str                  # the caller's speaker (renter | landlord), or agent
    start_ms: int
    audio: bytes


@dataclass(frozen=True)
class Placed:
    """A stored line's place in the audio, changed since it was stored."""
    idx: int
    start_ms: int | None
    end_ms: int | None


class _Run:
    """Agent audio that plays without a gap. It starts on a whole millisecond, and `taken` (bytes already
    handed out) is a whole number of them, so every chunk it gives starts on a whole millisecond too."""

    def __init__(self, start_ms: int) -> None:
        self.start_ms, self.taken, self.buf = start_ms, 0, bytearray()


class CallRecorder:
    def __init__(self, fmt: Format, caller: str = "renter", clock: Callable[[], float] = time.monotonic):
        self.fmt, self.caller, self.clock = fmt, caller, clock
        self.t0 = clock()
        self._heard = bytearray()             # the caller's audio not handed out yet
        self._heard_taken = 0                 # bytes of it handed out, a whole number of milliseconds
        self._run: _Run | None = None         # the agent's audio playing now, or last
        self._done: list[Chunk] = []          # agent audio from earlier runs, played and not handed out
        self._spans: dict[str, list[float | None]] = {}   # item_id -> [start_ms, end_ms]
        self._agent_items: set[str] = set()   # the item_ids of the agent's lines, which a barge-in cuts
        self._stored: dict[str, tuple[int, tuple[int | None, int | None]]] = {}  # item_id -> (idx, span)

    # ── what the bridge reports ────────────────────────────────────────────────

    def heard(self, b64: str) -> None:
        """A chunk of the caller's audio, as sent to Higgs."""
        self._heard += base64.b64decode(b64)

    def played(self, b64: str, item_id: str | None = None) -> None:
        """A chunk of Higgs's audio, as sent to the caller: it plays after whatever is still playing."""
        data = base64.b64decode(b64)
        now, run = self._now_ms(), self._run
        if run is None or self._end_ms(run) < now:   # nothing playing: a new run starts now
            if run is not None:
                self._close(run)
            run = self._run = _Run(math.ceil(now))
        start = self._end_ms(run)
        run.buf += data
        if item_id:
            self._agent_items.add(item_id)
            span = self._spans.setdefault(item_id, [None, None])
            span[0] = start if span[0] is None else min(span[0], start)
            span[1] = self._end_ms(run) if span[1] is None else max(span[1], self._end_ms(run))

    def speech_started(self, item_id: str | None, audio_start_ms: Any = None) -> None:
        """The caller started the line `item_id`, `audio_start_ms` into their audio (Higgs's number).
        Without it, the caller's audio sent so far stands in."""
        if item_id:
            self._spans.setdefault(item_id, [None, None])[0] = _ms(audio_start_ms, self._heard_ms())

    def speech_stopped(self, item_id: str | None, audio_end_ms: Any = None) -> None:
        """The caller's line `item_id` ended, `audio_end_ms` into their audio."""
        if item_id:
            self._spans.setdefault(item_id, [None, None])[1] = _ms(audio_end_ms, self._heard_ms())

    def cut(self) -> None:
        """Barge-in: what the agent hadn't said yet is dropped, and its lines end where it stopped."""
        run = self._run
        if run is None:
            return
        keep = self._played_bytes(run, self._now_ms())
        if keep >= len(run.buf):
            return
        del run.buf[keep:]
        stop = self._end_ms(run)
        for item_id in self._agent_items:
            span = self._spans[item_id]
            if span[1] is not None and span[1] > stop:
                span[0], span[1] = min(span[0] if span[0] is not None else stop, stop), stop

    def span(self, item_id: str | None) -> tuple[int | None, int | None]:
        """Where the line `item_id` is, as far as is known now."""
        start, end = self._spans.get(item_id or "", (None, None))
        return _round(start), _round(end)

    def stored(self, item_id: str | None, idx: int | None) -> None:
        """The line `item_id` was stored as turn `idx`, at its span as of now."""
        if item_id and idx is not None:
            self._stored[item_id] = (idx, self.span(item_id))

    # ── what goes to the database ──────────────────────────────────────────────

    def take(self) -> tuple[list[Chunk], list[Placed]]:
        """What is ready to store, oldest first: the caller's audio so far and the agent's audio that has
        played, each on whole milliseconds; and the stored lines whose place has changed."""
        chunks: list[Chunk] = []
        n = len(self._heard) // self.fmt.bytes_per_ms * self.fmt.bytes_per_ms
        if n:
            chunks.append(self._take_heard(n))
        chunks += self._done
        self._done = []
        if self._run is not None:
            played = self._played_bytes(self._run, self._now_ms())
            played -= played % self.fmt.bytes_per_ms
            if played:
                chunks.append(self._take_run(self._run, played))
        return chunks, self._moved()

    def finish(self) -> tuple[list[Chunk], list[Placed]]:
        """The call is over: everything left, but agent audio that never played."""
        self.cut()
        chunks, moved = self.take()
        if self._heard:
            chunks.append(self._take_heard(len(self._heard)))
        if self._run is not None and self._run.buf:
            chunks.append(self._take_run(self._run, len(self._run.buf)))
        self._run = None
        return chunks, moved

    # ── the clock ──────────────────────────────────────────────────────────────

    def _now_ms(self) -> float:
        return (self.clock() - self.t0) * 1000

    def _heard_ms(self) -> float:
        return (self._heard_taken + len(self._heard)) / self.fmt.bytes_per_ms

    def _end_ms(self, run: _Run) -> float:
        return run.start_ms + (run.taken + len(run.buf)) / self.fmt.bytes_per_ms

    def _played_bytes(self, run: _Run, now: float) -> int:
        """How much of the run's buffer has played by `now`, in whole samples."""
        played = int((now - run.start_ms) * self.fmt.bytes_per_ms + 1e-6) - run.taken   # 49.99999 ms is 50
        played -= played % self.fmt.width
        return max(0, min(len(run.buf), played))

    def _take_heard(self, n: int) -> Chunk:
        chunk = Chunk(self.caller, self._heard_taken // self.fmt.bytes_per_ms, bytes(self._heard[:n]))
        del self._heard[:n]
        self._heard_taken += n
        return chunk

    def _take_run(self, run: _Run, n: int) -> Chunk:
        chunk = Chunk("agent", run.start_ms + run.taken // self.fmt.bytes_per_ms, bytes(run.buf[:n]))
        del run.buf[:n]
        run.taken += n
        return chunk

    def _close(self, run: _Run) -> None:
        """A run that has finished playing: the rest of it is ready to hand out."""
        if run.buf:
            self._done.append(self._take_run(run, len(run.buf)))

    def _moved(self) -> list[Placed]:
        moved = []
        for item_id, (idx, was) in self._stored.items():
            now = self.span(item_id)
            if now != was:
                self._stored[item_id] = (idx, now)
                moved.append(Placed(idx, *now))
        return moved


def _ms(value: Any, fallback: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _round(value: float | None) -> int | None:
    return None if value is None else int(round(value))


# ── reading it back ────────────────────────────────────────────────────────

def _ulaw_tables() -> tuple[bytes, bytes]:
    """G.711 μ-law byte → PCM16, as two tables: the low byte and the high byte of each sample."""
    lo, hi = bytearray(256), bytearray(256)
    for b in range(256):
        u = ~b & 0xFF
        sample = ((((u & 0x0F) << 3) + 0x84) << ((u >> 4) & 0x07)) - 0x84
        sample = (-sample if u & 0x80 else sample) & 0xFFFF
        lo[b], hi[b] = sample & 0xFF, sample >> 8
    return bytes(lo), bytes(hi)


_ULAW_LO, _ULAW_HI = _ulaw_tables()


def pcm16(audio: bytes, fmt: str) -> bytes:
    """A chunk's audio as PCM16 little-endian. μ-law is decoded; PCM16 passes through."""
    if fmt != "pcmu":
        return audio
    out = bytearray(2 * len(audio))
    out[0::2] = audio.translate(_ULAW_LO)
    out[1::2] = audio.translate(_ULAW_HI)
    return bytes(out)


def duration_ms(rows: Iterable[AudioRow]) -> int:
    """Where the last chunk ends."""
    end = 0
    for r in rows:
        width = 1 if r.format == "pcmu" else 2
        end = max(end, r.start_ms + len(r.audio) * 1000 // (r.rate * width))
    return end


def render_wav(rows: Sequence[AudioRow], channels: Sequence[Iterable[str]], from_ms: int | None = None,
               to_ms: int | None = None) -> bytes:
    """A PCM16 WAV of a call's stored chunks between `from_ms` and `to_ms` (the whole call by default),
    with one channel per entry of `channels`, each holding those tracks; silence where nothing was."""
    rate = rows[0].rate if rows else 8000
    start = max(0, from_ms or 0)
    end = max(start, duration_ms(rows) if to_ms is None else to_ms)
    a, b = start * rate // 1000, end * rate // 1000
    tracks = [set(names) for names in channels]
    pcms = []
    for names in tracks:
        out = bytearray(2 * (b - a))
        for r in rows:
            if r.track not in names or r.rate != rate:
                continue
            pcm = pcm16(r.audio, r.format)
            s = r.start_ms * rate // 1000
            lo, hi = max(s, a), min(s + len(pcm) // 2, b)
            if lo < hi:
                out[2 * (lo - a):2 * (hi - a)] = pcm[2 * (lo - s):2 * (hi - s)]
        pcms.append(out)
    frames = pcms[0] if len(pcms) == 1 else _interleave(pcms)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(len(pcms))
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(frames))
    return buf.getvalue()


def _interleave(pcms: list[bytearray]) -> bytearray:
    """PCM16 channels, same length, into one frame after another."""
    n, step = len(pcms), 2 * len(pcms)
    out = bytearray(len(pcms[0]) * n)
    for i, pcm in enumerate(pcms):
        out[2 * i::step] = pcm[0::2]
        out[2 * i + 1::step] = pcm[1::2]
    return out
