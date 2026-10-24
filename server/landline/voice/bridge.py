"""The renter's phone call: Twilio media stream ↔ Higgs Realtime, with the Concierge on the Higgs side.

Twilio sends the caller's audio as base64 μ-law 8 kHz frames, and Higgs takes and returns the same format,
so frames pass straight through. The call comes from the `call_id` Twilio hands back in `start`, which
code minted in the TwiML; nothing about the call is taken from the model or the stream URL.

A call ends exactly once, through `RenterCalls.finalize`: on Twilio `stop`, either socket closing, a
Higgs failure, or the deadline.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from landline.agents.concierge.prompt import GREETING, INSTRUCTIONS
from landline.config import Settings
from landline.voice import twilio
from landline.voice.calls import RenterCalls
from landline.voice.higgs import ConnectHiggs, session_config

log = logging.getLogger(__name__)

START_TIMEOUT_S = 10   # Twilio sends `connected` then `start` right after the socket opens
POLICY_VIOLATION = 1008


async def run_bridge(ws: WebSocket, *, calls: RenterCalls, connect_higgs: ConnectHiggs, settings: Settings,
                     set_status: Callable[[str], None] = lambda _: None) -> None:
    await ws.accept()
    start = await _wait_for_start(ws)
    call_id = str(((start or {}).get("customParameters") or {}).get("call_id", ""))
    call = calls.claim(call_id) if start else None
    if call is None:
        log.warning("media stream refused: unknown or reused call_id %r", call_id)
        await _close(ws, POLICY_VIOLATION)
        return
    stream_sid = str(start.get("streamSid", ""))

    try:
        # No tools for now: the Concierge only talks.
        higgs = await connect_higgs(settings.boson_api_key or "", session_config(
            instructions=INSTRUCTIONS, tools=[], voice=settings.higgs_voice_renter))
    except Exception as exc:
        log.exception("Higgs session failed for call %s", call.call_id)
        set_status(f"error: {exc}"[:200])
        calls.finalize(call.call_id, "higgs_error")
        await _close(ws)
        return
    set_status("ok")

    reason = "error"
    pumps: set[asyncio.Task[str]] = set()
    try:
        await higgs.send({"type": "response.create", "response": {"instructions": GREETING}})
        pumps = {asyncio.create_task(_twilio_to_higgs(ws, higgs)),
                 asyncio.create_task(_higgs_to_twilio(higgs, ws, stream_sid, call.call_id))}
        done, _ = await asyncio.wait(pumps, timeout=settings.renter_call_max_s,
                                     return_when=asyncio.FIRST_COMPLETED)
        reason = _why(done)
    finally:
        for task in pumps:
            task.cancel()
        await asyncio.gather(*pumps, return_exceptions=True)
        calls.finalize(call.call_id, reason)
        with contextlib.suppress(Exception):
            await higgs.close()
        await _close(ws)


async def _wait_for_start(ws: WebSocket) -> dict[str, Any] | None:
    try:
        async with asyncio.timeout(START_TIMEOUT_S):
            while True:
                msg = await ws.receive_json()
                if msg.get("event") == "start":
                    start = dict(msg.get("start") or {})
                    start.setdefault("streamSid", msg.get("streamSid"))
                    return start
                if msg.get("event") == "stop":
                    return None
    except (TimeoutError, WebSocketDisconnect, ValueError):
        return None


async def _twilio_to_higgs(ws: WebSocket, higgs: Any) -> str:
    """The caller's audio, frame by frame, into Higgs. Ends when the caller hangs up."""
    while True:
        try:
            msg = await ws.receive_json()
        except WebSocketDisconnect:
            return "hangup"
        event = msg.get("event")
        if event == "media":
            await higgs.send({"type": "input_audio_buffer.append", "audio": msg["media"]["payload"]})
        elif event == "stop":
            return "hangup"


async def _higgs_to_twilio(higgs: Any, ws: WebSocket, stream_sid: str, call_id: str) -> str:
    """The Concierge's audio back to the caller, and barge-in. Ends when Higgs closes."""
    async for event in higgs.events():
        kind = event.get("type")
        if kind == "response.output_audio.delta":
            await ws.send_json(twilio.media(stream_sid, event["delta"]))
        elif kind == "input_audio_buffer.speech_started":
            await ws.send_json(twilio.clear(stream_sid))
        elif kind == "conversation.item.input_audio_transcription.completed":
            log.info("call %s renter: %s", call_id, event.get("transcript", ""))
        elif kind == "response.output_audio_transcript.done":
            log.info("call %s concierge: %s", call_id, event.get("transcript", ""))
        elif kind == "error":
            log.warning("call %s Higgs error: %s", call_id, event.get("error"))
    return "higgs_closed"


def _why(done: set[asyncio.Task[str]]) -> str:
    if not done:
        return "deadline"
    task = next(iter(done))
    if task.cancelled():
        return "cancelled"
    if (exc := task.exception()) is not None:
        log.warning("bridge pump failed: %r", exc)
        return "error"
    return task.result()


async def _close(ws: WebSocket, code: int = 1000) -> None:
    with contextlib.suppress(Exception):
        await ws.close(code=code)
