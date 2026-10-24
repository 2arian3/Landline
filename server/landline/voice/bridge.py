"""The renter's conversation with the Concierge on Higgs Realtime, over any audio transport.

A transport is where the renter's audio comes from and goes to: a Twilio media stream on the phone
(base64 μ-law 8 kHz in JSON frames, see `TwilioTransport`), or the judge's browser (PCM16 24 kHz in
binary frames, see `voice/browser.py`). Higgs takes and returns the transport's own format, so audio
passes straight through either way.

`converse` runs the call: open Higgs with the Concierge and its tools, greet, pump audio both ways, run
tool calls against the session runtime, and end exactly once. The call ends through
`RenterCalls.finalize` (the renter hangs up, a socket closes, Higgs fails, the Concierge says goodbye,
or the deadline passes), and its session gets one `SessionEnded`. Code owns the call: nothing about it
comes from the model or a URL.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Protocol

from fastapi import WebSocket, WebSocketDisconnect

from landline.agents.concierge import tools
from landline.agents.concierge.prompt import GREETING, INSTRUCTIONS, RETURNING, WELCOME_BACK
from landline.config import Settings
from landline.contracts import SessionEnded, SessionStarted
from landline.orchestrator import SessionRuntime
from landline.voice import twilio
from landline.voice.calls import RenterCall, RenterCalls
from landline.voice.higgs import PCMU, ConnectHiggs, session_config
from landline.voice.sms import Texter

log = logging.getLogger(__name__)

START_TIMEOUT_S = 10      # Twilio sends `connected` then `start` right after the socket opens
POLICY_VIOLATION = 1008
HANG_UP_GRACE_S = 0.5     # after the goodbye has played, before hanging up
SESSION_END = {"hangup": "hangup", "end_session": "end_session", "deadline": "timeout"}   # else "closed"
LINK_TEXT = "Landline: here are the places that fit what you've told us. They update as we talk: {link}"


class Transport(Protocol):
    """The renter's end of the call."""

    audio_format: dict[str, Any]   # what Higgs is told to expect and send, e.g. higgs.PCMU
    bytes_per_second: int          # of that audio, for the playback clock

    async def receive_audio(self) -> str | None:
        """The next chunk of the renter's audio, base64 in `audio_format`; None once they've hung up."""

    async def send_audio(self, b64: str) -> None:
        """Plays a chunk of the Concierge's audio (base64, as Higgs sent it)."""

    async def send_clear(self) -> None:
        """Drops audio queued but not played yet, so the Concierge stops when the renter talks."""


class Playback:
    """When the renter will have heard everything sent so far. Higgs sends audio faster than real time,
    so the goodbye is still playing when its response ends; hanging up waits for this clock."""

    def __init__(self, bytes_per_second: int, clock: Callable[[], float] = time.monotonic) -> None:
        self.rate, self.clock, self.until = bytes_per_second, clock, 0.0

    def sent(self, b64: str) -> None:
        now = self.clock()
        self.until = max(self.until, now) + (len(b64) * 3 // 4) / self.rate

    def clear(self) -> None:
        self.until = self.clock()

    def remaining(self) -> float:
        return max(0.0, self.until - self.clock())


async def converse(transport: Transport, call: RenterCall, *, calls: RenterCalls, runtime: SessionRuntime,
                   connect_higgs: ConnectHiggs, settings: Settings,
                   set_status: Callable[[str], None] = lambda _: None, texter: Texter | None = None) -> str:
    """Runs one claimed call to its end, finalizes it and ends its session. Returns why it ended. The
    caller owns the socket and closes it afterwards. With a `texter`, a phone caller is texted the link
    to their board the first time their preferences change."""
    reason = "error"
    try:
        reason = await _talk(transport, call, runtime, connect_higgs, settings, set_status,
                             _link_texter(texter, call, settings))
    finally:
        calls.finalize(call.call_id, reason)
        await _end_session(runtime, call.session_id, reason)
    return reason


def _link_texter(texter: Texter | None, call: RenterCall, settings: Settings) -> Callable[[], bool] | None:
    """For a phone call: texts the renter the link to their board, in the background. None when there's
    no one to text (a browser call) or no way to send (no Twilio credentials or sending number)."""
    sender = call.called or settings.twilio_phone_number
    if texter is None or not texter.ready or not call.caller or not sender:
        return None
    link = f"{settings.web_url.rstrip('/')}/s/{call.session_id}"

    def text() -> bool:
        log.info("call %s: texting the board link", call.call_id)
        texter.send_soon(call.caller, sender, LINK_TEXT.format(link=link))
        return True
    return text


async def _talk(transport: Transport, call: RenterCall, runtime: SessionRuntime, connect_higgs: ConnectHiggs,
                settings: Settings, set_status: Callable[[str], None],
                text_link: Callable[[], bool] | None) -> str:
    # A returning caller (O-05): the Concierge gets what the runtime remembers, and welcomes them back.
    remembered = runtime.opening(call.session_id)
    instructions = f"{INSTRUCTIONS}{RETURNING}{remembered}" if remembered else INSTRUCTIONS
    try:
        higgs = await connect_higgs(settings.boson_api_key or "", session_config(
            instructions=instructions, tools=tools.TOOLS, voice=settings.higgs_voice_renter,
            audio_format=transport.audio_format))
    except Exception as exc:
        log.exception("Higgs session failed for call %s", call.call_id)
        set_status(f"error: {exc}"[:200])
        return "higgs_error"
    set_status("ok")

    ctx = tools.ToolContext(session_id=call.session_id, call_id=call.call_id, runtime=runtime,
                            text_link=text_link)
    playback = Playback(transport.bytes_per_second)
    pumps: set[asyncio.Task[str]] = set()
    try:
        greeting = WELCOME_BACK if remembered else GREETING
        await higgs.send({"type": "response.create", "response": {"instructions": greeting}})
        pumps = {asyncio.create_task(_renter_to_higgs(transport, higgs)),
                 asyncio.create_task(_higgs_to_renter(higgs, transport, ctx, playback))}
        done, _ = await asyncio.wait(pumps, timeout=settings.renter_call_max_s,
                                     return_when=asyncio.FIRST_COMPLETED)
        return _why(done)
    finally:
        for task in pumps:
            task.cancel()
        await asyncio.gather(*pumps, return_exceptions=True)
        with contextlib.suppress(Exception):
            await higgs.close()


async def _end_session(runtime: SessionRuntime, session_id: str, reason: str) -> None:
    """The brief is over. A second end (the page's Done button, a retry) changes nothing."""
    event = SessionEnded(session_id=session_id, at=datetime.now(UTC), idem_key=f"{session_id}:ended",
                         reason=SESSION_END.get(reason, "closed"))
    try:
        await runtime.emit(event)
    except Exception:
        log.exception("could not end session %s", session_id)


# ── the phone: Twilio media stream ──────────────────────────────────────────

class TwilioTransport:
    audio_format = PCMU
    bytes_per_second = 8000   # μ-law: one byte a sample

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


async def run_bridge(ws: WebSocket, *, calls: RenterCalls, runtime: SessionRuntime,
                     connect_higgs: ConnectHiggs, settings: Settings,
                     set_status: Callable[[str], None] = lambda _: None,
                     texter: Texter | None = None) -> None:
    """A Twilio media stream for a call that /twiml/renter minted; its id comes back in `start`. The call
    starts its session in the runtime, the way /api/judge/start does for the browser. With a `texter`,
    the caller gets their board link by text."""
    await ws.accept()
    start = await _wait_for_start(ws)
    call_id = str(((start or {}).get("customParameters") or {}).get("call_id", ""))
    call = calls.claim(call_id) if start else None
    if call is None:
        log.warning("media stream refused: unknown or reused call_id %r", call_id)
        await close_socket(ws, POLICY_VIOLATION)
        return
    try:
        await runtime.start(SessionStarted(session_id=call.session_id, at=datetime.now(UTC),
                                           idem_key=f"{call.session_id}:started", channel="phone",
                                           caller_phone=call.caller))
    except Exception:
        log.exception("could not start session %s for call %s", call.session_id, call.call_id)
        calls.finalize(call.call_id, "error")
        await close_socket(ws)
        return
    try:
        transport = TwilioTransport(ws, str(start.get("streamSid", "")))
        await converse(transport, call, calls=calls, runtime=runtime, connect_higgs=connect_higgs,
                       settings=settings, set_status=set_status, texter=texter)
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


async def _higgs_to_renter(higgs: Any, transport: Transport, ctx: tools.ToolContext,
                           playback: Playback) -> str:
    """The Concierge's audio back to the renter, barge-in and tool calls. Ends when Higgs closes, or once
    the goodbye has played after end_session."""
    async for event in higgs.events():
        kind = event.get("type")
        if kind == "response.output_audio.delta":
            await transport.send_audio(event["delta"])
            playback.sent(event["delta"])
        elif kind == "input_audio_buffer.speech_started":
            await transport.send_clear()
            playback.clear()
        elif kind == "response.function_call_arguments.done":
            await _run_tool(higgs, event, ctx)
        elif kind == "response.done" and ctx.hang_up:
            await asyncio.sleep(playback.remaining() + HANG_UP_GRACE_S)
            return "end_session"
        elif kind == "conversation.item.input_audio_transcription.completed":
            log.info("call %s renter: %s", ctx.call_id, event.get("transcript", ""))
        elif kind == "response.output_audio_transcript.done":
            log.info("call %s concierge: %s", ctx.call_id, event.get("transcript", ""))
        elif kind == "error":
            log.warning("call %s Higgs error: %s", ctx.call_id, event.get("error"))
    return "higgs_closed"


async def _run_tool(higgs: Any, event: dict[str, Any], ctx: tools.ToolContext) -> None:
    # Higgs's own `call_id` here names this tool call, not our phone call.
    tool_call_id = str(event.get("call_id", ""))
    try:
        output = await tools.handle(str(event.get("name", "")), event.get("arguments") or "{}",
                                    tool_call_id, ctx)
    except Exception:
        log.exception("tool %s failed on call %s", event.get("name"), ctx.call_id)
        output = tools.BROKEN
    await higgs.send({"type": "conversation.item.create",
                      "item": {"type": "function_call_output", "call_id": tool_call_id, "output": output}})
    if not ctx.hang_up:   # after end_session there's nothing more to say: the goodbye is already out
        await higgs.send({"type": "response.create"})


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
