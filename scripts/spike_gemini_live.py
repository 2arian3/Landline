"""Gemini Live spike: time to first audio, one tool round trip, barge-in, text mode, concurrency.

    uv run python scripts/spike_gemini_live.py

Needs GEMINI_API_KEY. Prints one JSON object and does not place a phone call. Exits 0 when the key
is missing so a checkout without it is not a failure. The numbers belong in the gemini-live skill.
"""
# Path is inserted before the project imports. Ruff's import-order rule doesn't cover that.
# ruff: noqa: E402
from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
import time
import warnings
import wave
from pathlib import Path

with warnings.catch_warnings():
    warnings.simplefilter("ignore", DeprecationWarning)
    import audioop

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "server"), str(ROOT)]

from dotenv import load_dotenv

from landline.voice.gemini_live import GeminiError, connect
from landline.voice.higgs import session_config

CLIP = ROOT / "evals" / "fixtures" / "audio" / "r1-budget.wav"
NOTE = {
    "type": "function", "name": "note",
    "description": "Write down the single word the caller asked you to remember.",
    "parameters": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
}


def _pcm16(path: Path, rate: int = 16000) -> bytes:
    with wave.open(str(path)) as wf:
        width, channels, src = wf.getsampwidth(), wf.getnchannels(), wf.getframerate()
        pcm = wf.readframes(wf.getnframes())
    if width != 2:
        pcm = audioop.lin2lin(pcm, width, 2)
    if channels > 1:
        pcm = audioop.tomono(pcm, 2, 0.5, 0.5)
    if src != rate:
        pcm, _ = audioop.ratecv(pcm, 2, 1, src, rate, None)
    return pcm


async def _watch(session):
    """One reader for the session. Two `events()` loops would split the queue between them."""
    seen: list[dict] = []

    async def pump() -> None:
        async for event in session.events():
            seen.append(event)

    task = asyncio.create_task(pump())

    async def until(kind: str, timeout: float, start: int = 0) -> tuple[dict | None, float, int]:
        began = time.monotonic()
        index = start
        while time.monotonic() - began < timeout:
            while index < len(seen):
                event = seen[index]
                index += 1
                if event.get("type") in (kind, "error"):
                    return event, time.monotonic() - began, index
            await asyncio.sleep(0.02)
        return None, time.monotonic() - began, index

    return task, until


async def _text_mode(key: str, model: str, voice: str) -> dict:
    started = time.monotonic()
    try:
        session = await connect(key, session_config(
            instructions="Answer with the single word pong and call no tools.", tools=[], modality="text",
        ), model=model, voice=voice)
    except Exception as exc:
        return {"ok": False, "error": str(exc), "seconds": round(time.monotonic() - started, 2)}
    try:
        await session.send({"type": "conversation.item.create", "item": {
            "type": "message", "role": "user",
            "content": [{"type": "input_text", "text": "Reply with the single word pong."}]}})
        await session.send({"type": "response.create"})
        _pump, until = await _watch(session)
        event, waited, _ = await until("response.output_text.done", 25)
    except Exception as exc:
        return {"ok": False, "error": str(exc), "seconds": round(time.monotonic() - started, 2)}
    finally:
        await session.close()
    text = "" if not event else str(event.get("text") or event.get("error") or "")
    return {"ok": bool(event and event.get("type") == "response.output_text.done"),
            "text": text[:80], "seconds": round(waited, 2)}


async def _audio(key: str, model: str, voice: str) -> dict:
    out: dict = {}
    t0 = time.monotonic()
    try:
        session = await connect(key, session_config(
            instructions=(
                "You are Landline, calling for a renter. When asked to remember a word, call note "
                "with that word and then say it back in one short sentence. If someone talks over you, "
                "stop and answer what they just said."
            ),
            tools=[NOTE], voice=voice,
        ), model=model, voice=voice)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    out["connect_s"] = round(time.monotonic() - t0, 3)
    _pump, until = await _watch(session)
    cursor = 0
    try:
        await session.send({"type": "conversation.item.create", "item": {
            "type": "message", "role": "user",
            "content": [{"type": "input_text",
                         "text": "[The call just connected.] Say hello in one sentence."}]}})
        await session.send({"type": "response.create"})
        event, waited, cursor = await until("response.output_audio.delta", 25, cursor)
        out["ttfa_s"] = round(waited, 3)
        out["first_audio"] = bool(event and event.get("type") == "response.output_audio.delta")

        await session.send({"type": "conversation.item.create", "item": {
            "type": "message", "role": "user",
            "content": [{"type": "input_text", "text": "Remember the word harbour. Call note."}]}})
        await session.send({"type": "response.create"})
        t2 = time.monotonic()
        call, _, cursor = await until("response.function_call_arguments.done", 25, cursor)
        if not call or call.get("type") != "response.function_call_arguments.done":
            out["tool"] = {"ok": False, "error": str(call)}
        else:
            await session.send({"type": "conversation.item.create", "item": {
                "type": "function_call_output", "call_id": call.get("call_id"),
                "output": "Saved."}})
            await session.send({"type": "response.create"})
            follow, waited, cursor = await until("response.output_audio.delta", 25, cursor)
            out["tool"] = {
                "ok": bool(follow and follow.get("type") == "response.output_audio.delta"),
                "name": call.get("name"), "arguments": call.get("arguments"),
                "round_trip_s": round(time.monotonic() - t2, 3), "follow_up_s": round(waited, 3),
            }

        if CLIP.exists():
            pcm = _pcm16(CLIP)
            await session.send({"type": "conversation.item.create", "item": {
                "type": "message", "role": "user",
                "content": [{"type": "input_text", "text": "Count slowly from one to twenty."}]}})
            await session.send({"type": "response.create"})
            _, _, cursor = await until("response.output_audio.delta", 20, cursor)
            for i in range(0, min(len(pcm), 16000 * 2), 320):
                chunk = pcm[i:i + 320]
                await session.send({"type": "input_audio_buffer.append",
                                    "audio": base64.b64encode(chunk).decode()})
                await asyncio.sleep(0.01)
            barge, waited, _cursor = await until("input_audio_buffer.speech_started", 15, cursor)
            out["barge_in"] = {
                "speech_started": bool(barge and barge.get("type") == "input_audio_buffer.speech_started"),
                "seconds": round(waited, 3),
            }
        else:
            out["barge_in"] = {"speech_started": False, "error": "clip missing"}
    except Exception as exc:
        out["error"] = str(exc)
    finally:
        await session.close()
    out["ok"] = bool(out.get("first_audio"))
    return out


async def _concurrency(key: str, model: str, voice: str, cap: int = 5) -> dict:
    opened = []
    failed = None
    for _ in range(cap):
        try:
            session = await connect(key, session_config(
                instructions="Say hello.", tools=[], voice=voice), model=model, voice=voice)
        except GeminiError as exc:
            failed = str(exc)
            break
        opened.append(session)
    for session in opened:
        await session.close()
    return {"opened": len(opened), "stopped_because": failed}


async def main() -> int:
    load_dotenv(ROOT / ".env")
    key = os.getenv("GEMINI_API_KEY") or ""
    model = (os.getenv("GEMINI_LIVE_MODEL") or "gemini-3.8-live").strip()
    voice = (os.getenv("GEMINI_LIVE_VOICE") or "Aoede").strip()
    if not key:
        print(json.dumps({"skipped": "GEMINI_API_KEY is not set"}))
        return 0
    report = {
        "model": model, "voice": voice,
        "text": await _text_mode(key, model, voice),
        "audio": await _audio(key, model, voice),
        "concurrency": await _concurrency(key, model, voice),
    }
    print(json.dumps(report, indent=2))
    return 0 if report["audio"].get("ok") or report["text"].get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
