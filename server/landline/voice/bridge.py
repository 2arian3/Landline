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
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from fastapi import WebSocket, WebSocketDisconnect

from landline.agents.auditor import llm as auditor_llm
from landline.agents.concierge import backstop, tools
from landline.agents.concierge.prompt import (
    CALL_CONNECTED,
    FIRST_CALL,
    INSTRUCTIONS,
    LINK_SENT,
    RETURNING,
    SPOKE_BEFORE,
    WELCOME_BACK,
    language_name,
    primary,
)
from landline.config import Settings
from landline.contracts import SessionEnded, SessionStarted
from landline.orchestrator import SessionRuntime
from landline.voice import twilio
from landline.voice.calls import RenterCall, RenterCalls
from landline.voice.higgs import PCMU, ConnectHiggs, session_config
from landline.voice.line import Item, Line, Lines
from landline.voice.recorder import CallRecorder, Chunk, Placed, format_of
from landline.voice.sms import Texter

log = logging.getLogger(__name__)

_CATCHING: set[asyncio.Task] = set()   # catch-ups under way (agents/concierge/backstop.py)
_READER: list[Any] = []   # the text model for them, read from the environment once
START_TIMEOUT_S = 10      # Twilio sends `connected` then `start` right after the socket opens
POLICY_VIOLATION = 1008
HANG_UP_GRACE_S = 0.5     # after the goodbye has played, before hanging up
KEEP_AUDIO_EVERY_S = 5    # how often the call's audio so far is stored (voice/recorder.py)
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
                   set_status: Callable[[str], None] = lambda _: None, texter: Texter | None = None,
                   lines: Lines | None = None) -> str:
    """Runs one claimed call to its end, finalizes it and ends its session. Returns why it ended. The
    caller owns the socket and closes it afterwards. With a `texter`, a phone caller is texted the link
    to their board once, when the call connects, whatever the Concierge does next (a returning caller's
    board is already there). With `lines`, map taps reach the Concierge between turns (voice/line.py)."""
    reason = "error"
    # The call, its transcript and its audio are stored for the admin panel (orchestrator/calllog.py).
    kind = "voice" if transport.audio_format == PCMU else "browser"
    recorder = CallRecorder(format_of(transport.audio_format))
    runtime.calls.started(call.call_id, call.session_id, kind,
                          deadline_at=datetime.now(UTC) + timedelta(seconds=settings.renter_call_max_s))
    try:
        reason = await _talk(transport, call, runtime, connect_higgs, settings, set_status,
                             _link_texter(texter, call, settings), recorder, lines)
    finally:
        calls.finalize(call.call_id, reason)
        try:   # before it ends, and never in the way of ending it
            _keep(runtime, call.call_id, recorder, recorder.finish(), settings.record_audio)
        except Exception:
            log.exception("could not keep the end of call %s's audio", call.call_id)
        runtime.calls.ended(call.call_id, reason)
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


def opening(remembered: str | None, language: str | None = None) -> tuple[str, list[dict[str, Any]]]:
    """The Concierge's session instructions for a call, and the events that start it. Every call opens the
    same way: how it opens sits in the session instructions (FIRST_CALL, or WELCOME_BACK next to what code
    remembers, O-05), and the call starts with a quoted note and a plain response.create. Never a response
    object on response.create: its instructions replace the session's, so a welcome ignored the memory and
    made one up (docs/EVAL_FINDINGS.md), and first-time calls sent GREETING that way called no tools all call
    (#129). A returning caller whose stored language isn't English is welcomed back in it."""
    note = {"type": "message", "role": "user", "content": [{"type": "input_text", "text": CALL_CONNECTED}]}
    start = [{"type": "conversation.item.create", "item": note}, {"type": "response.create"}]
    if not remembered:
        return f"{INSTRUCTIONS}{FIRST_CALL}", start
    spoke = primary(language)
    before = SPOKE_BEFORE.format(language=language_name(spoke)) if spoke and spoke != "en" else ""
    return f"{INSTRUCTIONS}{before}{WELCOME_BACK}{RETURNING}{remembered}", start


def _reader() -> Any | None:
    """The text model the catch-up reads a line with: the Auditor's (LLM_PROVIDER, else the first key set,
    Boson's last). None without a key: the calls go on without catch-ups."""
    if not _READER:
        _READER.append(auditor_llm.from_env())
    return _READER[0]


def hint(remembered: str | None, language: str | None) -> str | None:
    """The transcription hint for a call: a returning caller's stored language, two letters, else None
    (Higgs detects it). A first-time caller's stored language can only be a default, so it isn't used."""
    lang = primary(language)
    return lang if remembered and len(lang) == 2 else None


async def _talk(transport: Transport, call: RenterCall, runtime: SessionRuntime, connect_higgs: ConnectHiggs,
                settings: Settings, set_status: Callable[[str], None],
                text_link: Callable[[], bool] | None, recorder: CallRecorder,
                lines: Lines | None = None) -> str:
    remembered = runtime.opening(call.session_id)
    language = runtime.state(call.session_id).preferences.language   # restored by Profiles, from code
    instructions, start = opening(remembered, language)
    if text_link is not None:
        instructions += LINK_SENT   # so the Concierge tells them; the text goes once Higgs is up
    try:
        higgs = await connect_higgs(settings.boson_api_key or "", session_config(
            instructions=instructions, tools=tools.TOOLS, voice=settings.higgs_voice_renter,
            audio_format=transport.audio_format, language=hint(remembered, language)))
    except Exception as exc:
        log.exception("Higgs session failed for call %s", call.call_id)
        set_status(f"error: {exc}"[:200])
        return "higgs_error"
    set_status("ok")
    if text_link is not None:
        text_link()

    ctx = tools.ToolContext(session_id=call.session_id, call_id=call.call_id, runtime=runtime)
    playback = Playback(transport.bytes_per_second)
    pumps: set[asyncio.Task[str]] = set()
    keeper = asyncio.create_task(_keep_every(runtime, call.call_id, recorder, settings.record_audio))
    line = lines.open(call.session_id, higgs.send) if lines is not None else None
    try:
        for event in start:
            await higgs.send(event)
        pumps = {asyncio.create_task(_renter_to_higgs(transport, higgs, recorder)),
                 asyncio.create_task(_higgs_to_renter(higgs, transport, ctx, playback, recorder, line,
                                                     reader=_reader()))}
        done, _ = await asyncio.wait(pumps, timeout=settings.renter_call_max_s,
                                     return_when=asyncio.FIRST_COMPLETED)
        return _why(done)
    finally:
        for task in (*pumps, keeper):
            task.cancel()
        await asyncio.gather(*pumps, keeper, return_exceptions=True)
        if line is not None:
            lines.close(call.session_id, line)
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
                     texter: Texter | None = None, lines: Lines | None = None) -> None:
    """A Twilio media stream for a call that /twiml/renter minted; its id comes back in `start`. The call
    starts its session in the runtime, the way /api/judge/start does for the browser. With a `texter`,
    the caller gets their board link by text; with `lines`, map taps reach the Concierge."""
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
                       settings=settings, set_status=set_status, texter=texter, lines=lines)
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

async def _renter_to_higgs(transport: Transport, higgs: Any, recorder: CallRecorder) -> str:
    """The renter's audio, chunk by chunk, into Higgs. Ends when they hang up."""
    while (audio := await transport.receive_audio()) is not None:
        recorder.heard(audio)
        await higgs.send({"type": "input_audio_buffer.append", "audio": audio})
    return "hangup"


async def _higgs_to_renter(higgs: Any, transport: Transport, ctx: tools.ToolContext,
                           playback: Playback, recorder: CallRecorder, line: Line | None = None,
                           reader: Any | None = None) -> str:
    """The Concierge's audio back to the renter, barge-in and tool calls. Ends when Higgs closes, or once
    the goodbye has played after end_session. `line` sees every event, to deliver map taps between turns.
    With `reader` (a text model), a renter line in another script that got a reply with no tool is read
    for its preferences in the background (agents/concierge/backstop.py)."""
    spoke = False   # whether the response under way has sent any audio yet
    turn = backstop.Turn()
    async for event in higgs.events():
        kind = event.get("type")
        if line is not None:
            if ctx.hang_up:
                line.close()   # saying goodbye: nothing more goes in
            await line.seen(event)
        if kind == "response.created":
            spoke = False
            turn.response_created()
        elif kind == "response.output_audio.delta":
            spoke = True
            await transport.send_audio(event["delta"])
            playback.sent(event["delta"])
            recorder.played(event["delta"], event.get("item_id"))
        elif kind == "input_audio_buffer.speech_started":
            await transport.send_clear()
            playback.clear()
            recorder.cut()
            recorder.speech_started(event.get("item_id"), event.get("audio_start_ms"))
            turn.speech_started()
        elif kind == "input_audio_buffer.speech_stopped":
            recorder.speech_stopped(event.get("item_id"), event.get("audio_end_ms"))
        elif kind == "response.function_call_arguments.done":
            turn.tool_called(str(event.get("name", "")))
            await run_tool(higgs, event, ctx, spoke=spoke)
        elif kind == "response.done":
            if ends_call(event, ctx):
                await asyncio.sleep(playback.remaining() + HANG_UP_GRACE_S)
                return "end_session"
            turn.response_done(event)
        elif kind == "conversation.item.input_audio_transcription.completed":
            log.info("call %s renter: %s", ctx.call_id, event.get("transcript", ""))
            _said(ctx, recorder, "renter", event)
            turn.transcript(str(event.get("transcript") or ""))
        elif kind == "response.output_audio_transcript.done":
            log.info("call %s concierge: %s", ctx.call_id, event.get("transcript", ""))
            _said(ctx, recorder, "agent", event)
        elif kind == "error":
            log.warning("call %s Higgs error: %s", ctx.call_id, event.get("error"))
        if reader is not None and not ctx.hang_up and (said := turn.line()) is not None:
            task = asyncio.create_task(backstop.catch_up(reader, said, ctx))   # off the voice path
            _CATCHING.add(task)   # held until done, even after the call ends
            task.add_done_callback(_CATCHING.discard)
        if not ctx.hang_up and (asked := turn.call_line()) is not None:
            task = asyncio.create_task(_calls_asked(asked, ctx, line))
            _CATCHING.add(task)
            task.add_done_callback(_CATCHING.discard)
    return "higgs_closed"


async def _calls_asked(said: str, ctx: tools.ToolContext, line: Line | None) -> None:
    """The renter asked for calls or a viewing and the Concierge ran no call_listings (#127): code records
    the plan, and the Concierge hears call_listings' reply between turns, to read it back."""
    try:
        reply = await backstop.catch_up_calls(said, ctx)
        if reply is not None and line is not None:
            await line.push(Item("plan", f"{backstop.PLANNED} {reply}", line.clock()))
    except Exception:
        log.exception("call %s: the calls the renter asked for couldn't be recorded", ctx.call_id)


def _said(ctx: tools.ToolContext, recorder: CallRecorder, speaker: str, event: dict[str, Any]) -> None:
    """Stores a transcript line at its place in the call's audio."""
    item_id = event.get("item_id")
    start_ms, end_ms = recorder.span(item_id)
    idx = ctx.runtime.calls.said(ctx.call_id, speaker, str(event.get("transcript", "")),
                                 start_ms=start_ms, end_ms=end_ms)
    recorder.stored(item_id, idx)


async def _keep_every(runtime: SessionRuntime, call_id: str, recorder: CallRecorder, record: bool) -> None:
    """Stores what the recorder has ready every few seconds, so a crash loses only the last few."""
    while True:
        await asyncio.sleep(KEEP_AUDIO_EVERY_S)
        try:
            _keep(runtime, call_id, recorder, recorder.take(), record)
        except Exception:
            log.exception("could not keep call %s's audio", call_id)


def _keep(runtime: SessionRuntime, call_id: str, recorder: CallRecorder,
          taken: tuple[list[Chunk], list[Placed]], record: bool) -> None:
    chunks, moved = taken
    if record:
        for chunk in chunks:
            runtime.calls.audio(call_id, chunk.track, chunk.start_ms, chunk.audio,
                                fmt=recorder.fmt.name, rate=recorder.fmt.rate)
    for line in moved:
        runtime.calls.placed(call_id, line.idx, line.start_ms, line.end_ms)


async def run_tool(higgs: Any, event: dict[str, Any], ctx: Any, *, spoke: bool = True,
                   agent: Any = tools) -> None:
    """Runs one tool call and answers Higgs. `spoke` says whether this response said anything before the
    call, which matters only for the tool that hangs up. `agent` is the agent's tools module: the
    Concierge's by default, or the Caller's (agents/caller/tools.py), each with handle, BROKEN and
    SAY_GOODBYE, and a context with call_id, hang_up and bye_response."""
    # Higgs's own `call_id` here names this tool call, not our phone call.
    tool_call_id = str(event.get("call_id", ""))
    try:
        output = await agent.handle(str(event.get("name", "")), event.get("arguments") or "{}",
                                    tool_call_id, ctx)
    except Exception:
        log.exception("tool %s failed on call %s", event.get("name"), ctx.call_id)
        output = agent.BROKEN
    # Hanging up with nothing said yet: one more response for the goodbye, or the other person hears the
    # line drop. Asked once. The request goes in the tool's answer, not in response instructions, which
    # would replace the agent's own (it then greets: "Hello! How can I help you today?").
    say_bye = ctx.hang_up and not spoke and ctx.bye_response is None
    if say_bye:
        output, ctx.bye_response = agent.SAY_GOODBYE, str(event.get("response_id") or "")
    await higgs.send({"type": "conversation.item.create",
                      "item": {"type": "function_call_output", "call_id": tool_call_id, "output": output}})
    if not ctx.hang_up or say_bye:   # otherwise the goodbye is already out, and nothing more is said
        await higgs.send({"type": "response.create"})


def ends_call(done: dict[str, Any], ctx: Any) -> bool:
    """Whether this `response.done` is the last thing said: after end_session, unless it is the response
    that called end_session silently and a goodbye is still coming."""
    if not ctx.hang_up:
        return False
    return ctx.bye_response is None or (done.get("response") or {}).get("id") != ctx.bye_response


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
