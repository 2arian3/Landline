"""The renter's conversation with the Concierge on Higgs Realtime, over any audio transport.

A transport is where the renter's audio comes from and goes to: a Twilio media stream on the phone
(base64 μ-law 8 kHz in JSON frames, see `TwilioTransport`), or the judge's browser (PCM16 24 kHz in
binary frames, see `voice/browser.py`). Higgs takes and returns the transport's own format, so audio
passes straight through either way.

`converse` runs the call: open Higgs with the Concierge, greet, pump audio both ways, and end the call
exactly once through `RenterCalls.finalize` (the renter hangs up, a socket closes, Higgs fails, or the
deadline passes). Code owns the call: nothing about it comes from the model or a URL.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from typing import Any, Protocol

from fastapi import WebSocket, WebSocketDisconnect

from landline.agents.concierge.prompt import GREETING, INSTRUCTIONS
from landline.config import Settings
from landline.voice import twilio
from landline.voice.calls import RenterCall, RenterCalls
from landline.voice.higgs import PCMU, ConnectHiggs, session_config

log = logging.getLogger(__name__)

START_TIMEOUT_S = 10   # Twilio sends `connected` then `start` right after the socket opens
POLICY_VIOLATION = 1008


class Transport(Protocol):
    """The renter's end of the call."""

    audio_format: dict[str, Any]   # what Higgs is told to expect and send, e.g. higgs.PCMU

    async def receive_audio(self) -> str | None:
        """The next chunk of the renter's audio, base64 in `audio_format`; None once they've hung up."""

    async def send_audio(self, b64: str) -> None:
        """Plays a chunk of the Concierge's audio (base64, as Higgs sent it)."""

    async def send_clear(self) -> None:
        """Drops audio queued but not played yet, so the Concierge stops when the renter talks."""


async def converse(transport: Transport, call: RenterCall, *, calls: RenterCalls, connect_higgs: ConnectHiggs,
                   settings: Settings, set_status: Callable[[str], None] = lambda _: None) -> str:
    """Runs one claimed call to its end and finalizes it. Returns why it ended. The caller owns the socket
    and closes it afterwards."""
    try:
        # No tools for now: the Concierge only talks.
        higgs = await connect_higgs(settings.boson_api_key or "", session_config(
            instructions=INSTRUCTIONS, tools=[], voice=settings.higgs_voice_renter,
            audio_format=transport.audio_format))
    except Exception as exc:
        log.exception("Higgs session failed for call %s", call.call_id)
        set_status(f"error: {exc}"[:200])
        calls.finalize(call.call_id, "higgs_error")
        return "higgs_error"
    set_status("ok")

    reason = "error"
    pumps: set[asyncio.Task[str]] = set()
    try:
        await higgs.send({"type": "response.create", "response": {"instructions": GREETING}})
        pumps = {asyncio.create_task(_renter_to_higgs(transport, higgs)),
                 asyncio.create_task(_higgs_to_renter(higgs, transport, call.call_id))}
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
    return reason


# ── the phone: Twilio media stream ──────────────────────────────────────────

class TwilioTransport:
    audio_format = PCMU

    def __init__(self, ws: WebSocket, stream_sid: str) -> None:
        self.ws = ws
        self.stream_sid = stream_sid

    async def receive_audio(self) -> str | None:
        while True:
            try:
                msg = await self.ws.receive_json()
            except WebSocketDisconnect:
                return None
            if msg.get("event") == "media":
                return msg["media"]["payload"]
            if msg.get("event") == "stop":
                return None

    async def send_audio(self, b64: str) -> None:
        await self.ws.send_json(twilio.media(self.stream_sid, b64))

    async def send_clear(self) -> None:
        await self.ws.send_json(twilio.clear(self.stream_sid))


async def run_bridge(ws: WebSocket, *, calls: RenterCalls, connect_higgs: ConnectHiggs, settings: Settings,
                     set_status: Callable[[str], None] = lambda _: None) -> None:
    """A Twilio media stream for a call that /twiml/renter minted; its id comes back in `start`."""
    await ws.accept()
    start = await _wait_for_start(ws)
    call_id = str(((start or {}).get("customParameters") or {}).get("call_id", ""))
    call = calls.claim(call_id) if start else None
    if call is None:
        log.warning("media stream refused: unknown or reused call_id %r", call_id)
        await close_socket(ws, POLICY_VIOLATION)
        return
    try:
        await converse(TwilioTransport(ws, str(start.get("streamSid", ""))), call, calls=calls,
                       connect_higgs=connect_higgs, settings=settings, set_status=set_status)
    finally:
        await close_socket(ws)


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


# ── shared ──────────────────────────────────────────────────────────────────

async def _renter_to_higgs(transport: Transport, higgs: Any) -> str:
    """The renter's audio, chunk by chunk, into Higgs. Ends when they hang up."""
    while (audio := await transport.receive_audio()) is not None:
        await higgs.send({"type": "input_audio_buffer.append", "audio": audio})
    return "hangup"


async def _higgs_to_renter(higgs: Any, transport: Transport, call_id: str) -> str:
    """The Concierge's audio back to the renter, and barge-in. Ends when Higgs closes."""
    async for event in higgs.events():
        kind = event.get("type")
        if kind == "response.output_audio.delta":
            await transport.send_audio(event["delta"])
        elif kind == "input_audio_buffer.speech_started":
            await transport.send_clear()
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


async def close_socket(ws: WebSocket, code: int = 1000) -> None:
    with contextlib.suppress(Exception):
        await ws.close(code=code)
