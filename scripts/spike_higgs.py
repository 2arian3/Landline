"""Higgs Realtime spike (A-01).

Connects with audio/pcmu (Twilio's format) and measures the four things the
voice runtime needs before we write it:

  1. time to first audio
  2. whether conversation.item.truncate is accepted
  3. how many of 5 parallel sessions open, and any close 1013
  4. Farsi + English transcription on one 8 kHz clip

Needs BOSON_API_KEY in the environment or .env.

  uv run python scripts/spike_higgs.py
  uv run python scripts/spike_higgs.py latency
  uv run python scripts/spike_higgs.py truncate
  uv run python scripts/spike_higgs.py concurrency --sessions 5
  uv run python scripts/spike_higgs.py transcribe --clip path.wav
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import re
import struct
import sys
import time
import wave
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

try:
    from websockets.asyncio.client import connect as ws_connect
    from websockets.exceptions import ConnectionClosed, InvalidStatus
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "websockets is missing. Run `uv sync` (it comes in with uvicorn[standard])."
    ) from exc

ROOT = Path(__file__).resolve().parent.parent
WS_URL = "wss://api.boson.ai/v1/realtime?model=higgs-realtime"
TTS_URL = "https://api.boson.ai/v1/audio/speech"
PCMU_RATE = 8000
PCMU_FRAME = 160  # 20 ms, same as Twilio
SKILL_PATH = ROOT / ".claude" / "skills" / "higgs-realtime" / "SKILL.md"

# A renter sentence the Concierge has to hear on a real call.
MIXED_UTTERANCE = "سلام، I want a two-bedroom in Toronto. سگ دارم."
FA_SCRIPT = re.compile(r"[\u0600-\u06FF]")
EN_WORDS = re.compile(r"\b(two[-\s]?bedroom|toronto|dogs?|want|bedroom)\b", re.I)


# G.711 μ-law. audioop is gone in Python 3.13; this matches the ITU tables.
_MULAW_BIAS = 0x84
_MULAW_CLIP = 32635


def _lin2ulaw_sample(sample: int) -> int:
    sign = 0x80 if sample < 0 else 0x00
    if sample < 0:
        sample = -sample
    if sample > _MULAW_CLIP:
        sample = _MULAW_CLIP
    sample += _MULAW_BIAS
    exponent = 7
    mask = 0x4000
    while exponent > 0 and not (sample & mask):
        exponent -= 1
        mask >>= 1
    mantissa = (sample >> (exponent + 3)) & 0x0F
    return (~(sign | (exponent << 4) | mantissa)) & 0xFF


def _ulaw2lin_sample(ulaw: int) -> int:
    ulaw = (~ulaw) & 0xFF
    sample = ((ulaw & 0x0F) << 3) + _MULAW_BIAS
    sample <<= (ulaw & 0x70) >> 4
    sample -= _MULAW_BIAS
    return -sample if ulaw & 0x80 else sample


def _pcm16_to_samples(pcm16: bytes) -> list[int]:
    return list(struct.unpack(f"<{len(pcm16) // 2}h", pcm16))


def _samples_to_pcm16(samples: list[int]) -> bytes:
    return struct.pack(f"<{len(samples)}h", *[max(-32768, min(32767, int(s))) for s in samples])


def _resample_s16(samples: list[int], src_rate: int, dst_rate: int) -> list[int]:
    if src_rate == dst_rate or not samples:
        return samples
    n = max(1, int(round(len(samples) * dst_rate / src_rate)))
    out: list[int] = []
    for i in range(n):
        pos = i * (src_rate / dst_rate)
        lo = min(int(pos), len(samples) - 1)
        hi = min(lo + 1, len(samples) - 1)
        frac = pos - lo
        out.append(int(samples[lo] * (1 - frac) + samples[hi] * frac))
    return out


def _pcmu_silence(ms: int) -> bytes:
    return b"\xff" * (PCMU_RATE * ms // 1000)


def _linear_to_pcmu(pcm16: bytes, rate: int) -> bytes:
    samples = _pcm16_to_samples(pcm16)
    samples = _resample_s16(samples, rate, PCMU_RATE)
    return bytes(_lin2ulaw_sample(s) for s in samples)


def _pcmu_to_pcm16(pcmu: bytes) -> bytes:
    return _samples_to_pcm16([_ulaw2lin_sample(b) for b in pcmu])


def wav_to_pcmu(data: bytes) -> bytes:
    with wave.open(BytesIO(data), "rb") as wav:
        channels = wav.getnchannels()
        width = wav.getsampwidth()
        rate = wav.getframerate()
        frames = wav.readframes(wav.getnframes())
        comptype = (wav.getcomptype() or "NONE").upper()
    if comptype in {"ULAW", "MU-LAW", "MULAW"} or (width == 1 and rate == PCMU_RATE):
        if channels > 1:
            raise ValueError("mulaw clip must be mono")
        return frames
    if width != 2:
        raise ValueError(f"need 16-bit PCM or 8 kHz mulaw, got sampwidth={width}")
    if channels > 1:
        samples = _pcm16_to_samples(frames)
        frames = _samples_to_pcm16(
            [(samples[i] + samples[i + 1]) // 2 for i in range(0, len(samples) - 1, 2)]
        )
    return _linear_to_pcmu(frames, rate)


def load_clip(path: Path) -> bytes:
    raw = path.read_bytes()
    suffix = path.suffix.lower()
    if suffix == ".wav":
        return wav_to_pcmu(raw)
    if suffix in {".pcmu", ".ulaw", ".mu", ".raw"}:
        return raw
    raise ValueError(f"unsupported clip type {suffix}; use .wav or raw .pcmu")


def chunk_pcmu(audio: bytes, frame: int = PCMU_FRAME) -> list[bytes]:
    return [audio[i : i + frame] for i in range(0, len(audio), frame) if audio[i : i + frame]]


def session_update(*, vad: bool, instructions: str) -> dict[str, Any]:
    return {
        "type": "session.update",
        "session": {
            "model": "higgs-realtime",
            "instructions": instructions,
            "output_modalities": ["audio"],
            "audio": {
                "input": {
                    "format": {"type": "audio/pcmu"},
                    "transcription": {"model": "higgs-stt-3.1"},
                    "turn_detection": {"type": "server_vad"} if vad else None,
                },
                "output": {
                    "format": {"type": "audio/pcmu"},
                    "voice": "default",
                },
            },
            "tools": [],
            "tool_choice": "none",
            "temperature": 0.3,
        },
    }


@dataclass
class CloseInfo:
    code: int | None = None
    reason: str = ""


class HiggsSession:
    def __init__(self, ws: Any, verbose: bool = False) -> None:
        self.ws = ws
        self.verbose = verbose
        self.events: list[dict[str, Any]] = []
        self.close = CloseInfo()

    async def send(self, payload: dict[str, Any]) -> None:
        if self.verbose:
            print(f"  → {payload['type']}", flush=True)
        await self.ws.send(json.dumps(payload))

    async def recv(self, timeout: float) -> dict[str, Any]:
        raw = await asyncio.wait_for(self.ws.recv(), timeout)
        event = json.loads(raw)
        self.events.append(event)
        if self.verbose:
            extra = event.get("error") or event.get("transcript") or ""
            print(f"  ← {event.get('type')} {extra}".rstrip(), flush=True)
        return event

    async def wait_for(self, types: set[str], timeout: float) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"timed out waiting for {sorted(types)}")
            try:
                event = await self.recv(remaining)
            except ConnectionClosed as exc:
                self.close = CloseInfo(exc.code, exc.reason or "")
                raise
            kind = event.get("type")
            if kind in types or kind == "error":
                return event

    def first(self, kind: str) -> dict[str, Any] | None:
        return next((e for e in self.events if e.get("type") == kind), None)

    def item_id(self) -> str | None:
        for event in reversed(self.events):
            if event.get("item_id"):
                return str(event["item_id"])
            item = event.get("item")
            if isinstance(item, dict) and item.get("id"):
                return str(item["id"])
        return None

    def audio_bytes(self) -> int:
        total = 0
        for event in self.events:
            if event.get("type") != "response.output_audio.delta":
                continue
            delta = event.get("delta") or ""
            total += len(base64.b64decode(delta))
        return total


async def open_session(api_key: str, verbose: bool = False) -> HiggsSession:
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        ws = await ws_connect(
            WS_URL,
            additional_headers=headers,
            subprotocols=["realtime"],
            max_size=8 * 1024 * 1024,
            open_timeout=20,
            close_timeout=5,
        )
    except TypeError:
        ws = await ws_connect(
            WS_URL,
            extra_headers=headers,
            max_size=8 * 1024 * 1024,
            open_timeout=20,
            close_timeout=5,
        )
    return HiggsSession(ws, verbose=verbose)


async def start_pcmu_session(
    api_key: str,
    *,
    instructions: str,
    vad: bool,
    verbose: bool,
    timeout: float,
) -> HiggsSession:
    session = await open_session(api_key, verbose=verbose)
    await session.send(session_update(vad=vad, instructions=instructions))
    event = await session.wait_for({"session.created"}, timeout)
    if event.get("type") == "error":
        raise RuntimeError(f"session.update rejected: {event}")
    return session


@dataclass
class LatencyResult:
    connect_to_audio_ms: float | None = None
    create_to_audio_ms: float | None = None
    transcript: str = ""
    error: str = ""

    def summary(self) -> str:
        if self.error:
            return self.error
        return (
            f"{self.connect_to_audio_ms:.0f} ms connect → first delta; "
            f"{self.create_to_audio_ms:.0f} ms response.create → first delta"
        )


async def run_latency(api_key: str, *, timeout: float, verbose: bool) -> LatencyResult:
    t_connect = time.perf_counter()
    session = await start_pcmu_session(
        api_key,
        instructions="You are a latency probe. Reply in one short English sentence, then stop.",
        vad=True,
        verbose=verbose,
        timeout=timeout,
    )
    result = LatencyResult()
    try:
        t_create = time.perf_counter()
        await session.send(
            {
                "type": "response.create",
                "response": {
                    "input": [
                        {
                            "type": "message",
                            "role": "user",
                            "content": [{"type": "input_text", "text": "Say hello in one word."}],
                        }
                    ]
                },
            }
        )
        event = await session.wait_for({"response.output_audio.delta"}, timeout)
        if event.get("type") == "error":
            result.error = f"error: {event.get('error')}"
            return result
        now = time.perf_counter()
        result.connect_to_audio_ms = (now - t_connect) * 1000
        result.create_to_audio_ms = (now - t_create) * 1000
        done = session.first("response.output_audio_transcript.done")
        if not done:
            later = await session.wait_for(
                {"response.output_audio_transcript.done", "response.done"}, 8
            )
            if later.get("type") == "response.output_audio_transcript.done":
                done = later
        if done:
            result.transcript = str(done.get("transcript") or "")
        return result
    except (TimeoutError, ConnectionClosed, RuntimeError) as exc:
        result.error = _close_or(session, exc)
        return result
    finally:
        await session.ws.close()


@dataclass
class TruncateResult:
    accepted: bool | None = None
    detail: str = ""
    error: str = ""

    def summary(self) -> str:
        if self.error and self.accepted is None:
            return self.error
        if self.accepted:
            return f"yes ({self.detail})"
        return f"no ({self.detail or self.error})"


async def run_truncate(api_key: str, *, timeout: float, verbose: bool) -> TruncateResult:
    session = await start_pcmu_session(
        api_key,
        instructions="You are a barge-in probe. Speak slowly. Do not stop until told.",
        vad=True,
        verbose=verbose,
        timeout=timeout,
    )
    result = TruncateResult()
    try:
        await session.send(
            {
                "type": "response.create",
                "response": {
                    "instructions": "Count slowly from one to thirty in English. Pause between numbers.",
                },
            }
        )
        first = await session.wait_for({"response.output_audio.delta"}, timeout)
        if first.get("type") == "error":
            result.error = f"error: {first.get('error')}"
            return result
        # Collect a few more frames so audio_end_ms is non-zero.
        deadline = time.monotonic() + 0.4
        while time.monotonic() < deadline:
            try:
                await session.recv(0.15)
            except TimeoutError:
                break
        item_id = session.item_id()
        if not item_id:
            result.error = "no item_id on audio events"
            return result
        audio_end_ms = max(session.audio_bytes() * 1000 // PCMU_RATE, 80)
        try:
            await session.send({"type": "response.cancel"})
        except ConnectionClosed as exc:
            result.error = f"closed before cancel: {exc.code} {exc.reason}"
            return result
        await session.send(
            {
                "type": "conversation.item.truncate",
                "item_id": item_id,
                "content_index": 0,
                "audio_end_ms": audio_end_ms,
            }
        )
        event = await session.wait_for({"conversation.item.truncated"}, timeout=8)
        if event.get("type") == "conversation.item.truncated":
            result.accepted = True
            result.detail = (
                f"conversation.item.truncated item_id={event.get('item_id')} "
                f"audio_end_ms={event.get('audio_end_ms', audio_end_ms)}"
            )
            return result
        err = event.get("error") or {}
        result.accepted = False
        result.detail = f"error {err.get('code') or err.get('type')}: {err.get('message')}"
        # Truncate is documented against a completed item. Retry after the cancel settles.
        try:
            await session.wait_for({"response.done"}, timeout=6)
        except TimeoutError:
            pass
        await session.send(
            {
                "type": "conversation.item.truncate",
                "item_id": item_id,
                "content_index": 0,
                "audio_end_ms": audio_end_ms,
            }
        )
        retry = await session.wait_for({"conversation.item.truncated"}, timeout=8)
        if retry.get("type") == "conversation.item.truncated":
            result.accepted = True
            result.detail = (
                f"accepted after response.done; audio_end_ms={retry.get('audio_end_ms', audio_end_ms)}"
            )
        else:
            err = retry.get("error") or {}
            result.detail += f" | retry: {err.get('code') or err.get('type')}: {err.get('message')}"
        return result
    except (TimeoutError, ConnectionClosed, RuntimeError) as exc:
        result.error = _close_or(session, exc)
        return result
    finally:
        await session.ws.close()


@dataclass
class SessionAttempt:
    index: int
    ok: bool
    close_code: int | None = None
    detail: str = ""


@dataclass
class ConcurrencyResult:
    requested: int = 0
    opened: int = 0
    code_1013: int = 0
    attempts: list[SessionAttempt] = field(default_factory=list)
    error: str = ""

    def summary(self) -> str:
        if self.error and not self.attempts:
            return self.error
        return f"{self.opened}/{self.requested} opened, {self.code_1013}× close 1013"


async def _hold_session(
    index: int,
    api_key: str,
    *,
    hold: float,
    timeout: float,
    verbose: bool,
) -> SessionAttempt:
    try:
        session = await start_pcmu_session(
            api_key,
            instructions="You are a concurrency probe. Stay silent.",
            vad=True,
            verbose=verbose,
            timeout=timeout,
        )
    except ConnectionClosed as exc:
        return SessionAttempt(index, False, exc.code, exc.reason or "")
    except InvalidStatus as exc:
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)
        return SessionAttempt(index, False, status, str(exc))
    except Exception as exc:
        code = getattr(exc, "code", None)
        return SessionAttempt(index, False, code if isinstance(code, int) else None, str(exc))
    try:
        await asyncio.sleep(hold)
        return SessionAttempt(index, True, None, "session.created")
    finally:
        await session.ws.close()


async def run_concurrency(
    api_key: str,
    *,
    sessions: int,
    timeout: float,
    verbose: bool,
) -> ConcurrencyResult:
    result = ConcurrencyResult(requested=sessions)
    tries = await asyncio.gather(
        *[
            _hold_session(i, api_key, hold=2.0, timeout=timeout, verbose=verbose)
            for i in range(1, sessions + 1)
        ]
    )
    result.attempts = list(tries)
    result.opened = sum(1 for a in tries if a.ok)
    result.code_1013 = sum(1 for a in tries if a.close_code == 1013)
    return result


@dataclass
class TranscribeResult:
    transcript: str = ""
    expected: str = MIXED_UTTERANCE
    mixed: bool | None = None
    source: str = ""
    error: str = ""

    def summary(self) -> str:
        if self.error:
            return self.error
        flag = "yes" if self.mixed else "no"
        text = self.transcript or "(empty)"
        return f"{flag} — {text!r}  (from {self.source})"


async def synthesize_clip(api_key: str, text: str) -> bytes:
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(
            TTS_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": "higgs-tts-3",
                "input": text,
                "voice": "default",
                "response_format": "wav",
                "normalize": False,
            },
        )
    if response.status_code != 200:
        raise RuntimeError(f"TTS {response.status_code}: {response.text[:400]}")
    return wav_to_pcmu(response.content)


async def run_transcribe(
    api_key: str,
    *,
    clip: Path | None,
    expected: str,
    save_clip: Path | None,
    timeout: float,
    verbose: bool,
) -> TranscribeResult:
    result = TranscribeResult(expected=expected)
    try:
        if clip:
            pcmu = load_clip(clip)
            result.source = str(clip)
        else:
            pcmu = await synthesize_clip(api_key, expected)
            result.source = "higgs-tts-3 (synthesized mixed utterance)"
    except Exception as exc:
        result.error = f"clip: {exc}"
        return result

    # Pad so STT sees a clean start and end.
    pcmu = _pcmu_silence(300) + pcmu + _pcmu_silence(400)
    if save_clip:
        _write_pcm16_wav(save_clip, _pcmu_to_pcm16(pcmu))
        print(f"wrote clip to {save_clip}", flush=True)

    session = await start_pcmu_session(
        api_key,
        instructions="You are a transcription probe. Do not speak.",
        vad=False,
        verbose=verbose,
        timeout=timeout,
    )
    try:
        for frame in chunk_pcmu(pcmu, 800):
            await session.send(
                {
                    "type": "input_audio_buffer.append",
                    "audio": base64.b64encode(frame).decode("ascii"),
                }
            )
        await session.send({"type": "input_audio_buffer.commit"})
        event = await session.wait_for(
            {
                "conversation.item.input_audio_transcription.completed",
                "conversation.item.input_audio_transcription.failed",
            },
            timeout,
        )
        if event.get("type") == "error":
            result.error = f"error: {event.get('error')}"
            return result
        if event.get("type") == "conversation.item.input_audio_transcription.failed":
            result.error = f"transcription failed: {event.get('error') or event}"
            return result
        transcript = (
            event.get("transcript")
            or (event.get("item") or {}).get("transcript")
            or ""
        )
        result.transcript = str(transcript)
        result.mixed = bool(FA_SCRIPT.search(result.transcript) and EN_WORDS.search(result.transcript))
        return result
    except (TimeoutError, ConnectionClosed, RuntimeError) as exc:
        result.error = _close_or(session, exc)
        return result
    finally:
        await session.ws.close()


def _write_pcm16_wav(path: Path, pcm16: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(PCMU_RATE)
        wav.writeframes(pcm16)


def _close_or(session: HiggsSession, exc: BaseException) -> str:
    if session.close.code is not None:
        return f"closed {session.close.code} {session.close.reason}".rstrip()
    return str(exc)


def print_report(
    latency: LatencyResult | None,
    truncate: TruncateResult | None,
    concurrency: ConcurrencyResult | None,
    transcribe: TranscribeResult | None,
) -> None:
    rows = [
        (
            "Time to first audio (pcmu)",
            latency.summary() if latency else "skipped",
        ),
        (
            "conversation.item.truncate accepted",
            truncate.summary() if truncate else "skipped",
        ),
        (
            "Max concurrent sessions before 1013",
            concurrency.summary() if concurrency else "skipped",
        ),
        (
            "Farsi + English transcription on 8 kHz",
            transcribe.summary() if transcribe else "skipped",
        ),
    ]
    print()
    print("Higgs spike (A-01)")
    print("=" * 72)
    width = max(len(name) for name, _ in rows)
    for name, value in rows:
        print(f"{name:<{width}}  {value}")
    if concurrency:
        for attempt in concurrency.attempts:
            mark = "ok" if attempt.ok else f"fail code={attempt.close_code}"
            print(f"  session {attempt.index}: {mark} {attempt.detail}".rstrip())
    print()
    print("Paste into .claude/skills/higgs-realtime/SKILL.md:")
    print()
    print("| Measure | Value |")
    print("|---|---|")
    for name, value in rows:
        print(f"| {name} | {value} |")


def write_skill(
    latency: LatencyResult | None,
    truncate: TruncateResult | None,
    concurrency: ConcurrencyResult | None,
    transcribe: TranscribeResult | None,
) -> None:
    text = SKILL_PATH.read_text(encoding="utf-8")
    replacements = {
        "Time to first audio (pcmu)": latency.summary() if latency else "_tbd_",
        "Max concurrent sessions before 1013": concurrency.summary() if concurrency else "_tbd_",
        "`conversation.item.truncate` accepted": truncate.summary() if truncate else "_tbd_",
        "Farsi + English transcription on 8 kHz": transcribe.summary() if transcribe else "_tbd_",
    }
    for measure, value in replacements.items():
        text, n = re.subn(
            rf"(\| {re.escape(measure)} \| )([^|\n]+)(\s*\|)",
            rf"\g<1>{value}\3",
            text,
            count=1,
        )
        if n != 1:
            raise RuntimeError(f"could not update skill row for {measure!r}")
    SKILL_PATH.write_text(text, encoding="utf-8")
    print(f"updated {SKILL_PATH}")


def api_key_or_exit() -> str:
    load_dotenv(ROOT / ".env")
    key = os.getenv("BOSON_API_KEY", "").strip()
    if not key:
        print("BOSON_API_KEY is not set. Copy .env.example to .env and fill it in.", file=sys.stderr)
        raise SystemExit(2)
    return key


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "test",
        nargs="?",
        default="all",
        choices=["all", "latency", "truncate", "concurrency", "transcribe"],
        help="which probe to run (default: all)",
    )
    parser.add_argument("--sessions", type=int, default=5, help="parallel sessions for concurrency")
    parser.add_argument("--clip", type=Path, help="recorded mixed Farsi/English clip (.wav or .pcmu)")
    parser.add_argument("--expect", default=MIXED_UTTERANCE, help="text the clip is supposed to say")
    parser.add_argument("--save-clip", type=Path, help="write the 8 kHz clip used for transcription")
    parser.add_argument("--timeout", type=float, default=25, help="seconds to wait for a server event")
    parser.add_argument(
        "--write-skill",
        action="store_true",
        help="fill the table in the higgs-realtime skill",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="print every Higgs event type")
    return parser


async def async_main(args: argparse.Namespace) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    key = api_key_or_exit()
    want = args.test
    latency = truncate = concurrency = transcribe = None
    if want in {"all", "latency"}:
        print("· latency", flush=True)
        latency = await run_latency(key, timeout=args.timeout, verbose=args.verbose)
        print(f"  {latency.summary()}", flush=True)
    if want in {"all", "truncate"}:
        print("· truncate", flush=True)
        truncate = await run_truncate(key, timeout=args.timeout, verbose=args.verbose)
        print(f"  {truncate.summary()}", flush=True)
    if want in {"all", "concurrency"}:
        print(f"· concurrency ({args.sessions} sessions)", flush=True)
        concurrency = await run_concurrency(
            key, sessions=args.sessions, timeout=args.timeout, verbose=args.verbose
        )
        print(f"  {concurrency.summary()}", flush=True)
    if want in {"all", "transcribe"}:
        print("· transcribe", flush=True)
        transcribe = await run_transcribe(
            key,
            clip=args.clip,
            expected=args.expect,
            save_clip=args.save_clip,
            timeout=args.timeout,
            verbose=args.verbose,
        )
        print(f"  {transcribe.summary()}", flush=True)
    print_report(latency, truncate, concurrency, transcribe)
    if args.write_skill:
        write_skill(latency, truncate, concurrency, transcribe)
    failed = [
        item
        for item in (latency, truncate, concurrency, transcribe)
        if item is not None and getattr(item, "error", "")
    ]
    if concurrency and concurrency.opened == 0:
        failed.append(concurrency)
    return 1 if failed else 0


def main() -> int:
    return asyncio.run(async_main(build_parser().parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
