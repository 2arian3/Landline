"""Listing calls over the phone (A-02, outbound): the Dispatcher's `CallRunner` for TRANSPORT=voice.

One call goes like this:

1. `dial()` checks the number against the allowlist itself (hard rule 6, a second guard behind the
   Dispatcher's), then asks Twilio to place the call. Twilio fetches `/twiml/outbound/{call_id}` the moment
   the call is answered, runs answering-machine detection alongside it and posts what it decided to
   `/twilio/outbound/amd/{call_id}`, and posts status changes to `/twilio/outbound/status/{call_id}`.
   All three URLs carry our call id; Twilio signs them, and an unknown id gets nothing.
2. The call is answered: the TwiML hands it to our media stream with the call id as a `<Parameter>`
   (PORTING #1), the stream claims the call once, and `converse` runs the Caller on it from the first second.
   Detection says a machine (after its beep): the call is switched to a short voicemail that hangs up, which
   ends the stream and the Caller with it.
3. Whatever happens first ends the call through `OutboundCalls.finalize(call_id, reason)`: Twilio saying
   busy, no-answer or failed, the voicemail finishing, the conversation ending, or the Dispatcher's
   deadline. Only the first does anything (PORTING #2). If Twilio's line is still up, it is hung up.

What is said on the line is `converse`: the Caller (A-05) on Higgs, in voice/phone_caller.py. Its first
words are the disclosure, from `opening()` here, and that the call is recorded when its audio is (A-10).
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

from landline.config import Settings
from landline.contracts import CallState, EndReason, as_e164
from landline.orchestrator import SessionRuntime
from landline.voice import twilio
from landline.voice.dispatch import CallResult, Dial, Report

log = logging.getLogger(__name__)

API = "https://api.twilio.com/2010-04-01/Accounts/{sid}/Calls.json"
HANG_UP_API = "https://api.twilio.com/2010-04-01/Accounts/{sid}/Calls/{call_sid}.json"
TIMEOUT_S = 10
HANG_UP_S = 3               # a deadline's hang-up must not hold the Dispatcher up
RING_S = 25                 # how long Twilio lets it ring before no-answer
DISCLOSURE = "I'm an AI assistant calling for a renter."
RECORDED = "This call is recorded."   # said right after the disclosure when the call's audio is stored
VOICEMAIL = ("Hi, I'm an AI assistant calling for a renter about your listing at {address}. "
             "They'd like to know if it's still available and to book a viewing. "
             "We'll try again later. Thank you.")
# Twilio's AnsweredBy with MachineDetection=DetectMessageEnd: a machine waits for the beep. Detection runs
# alongside the call (AsyncAmd), so nobody waits on it and its waits keep Twilio's defaults, which tell
# machines from people best.
MACHINE = ("machine_end_beep", "machine_end_silence", "machine_end_other", "fax")
# Twilio's CallStatus once a call is over, and what it means if nothing else ended it first.
STATUS_END: dict[str, EndReason] = {"busy": "busy", "no-answer": "no_answer", "failed": "failed",
                                    "canceled": "failed", "completed": "hangup"}


class NotAllowed(Exception):
    """A number not on the allowlist reached dial()."""


@dataclass
class Outbound:
    """One listing call between dialling and its end. Lives in `OutboundCalls`, on app.state."""
    dial: Dial
    report: Report
    twilio_sid: str | None = None
    media: asyncio.Future = field(default_factory=lambda: asyncio.get_running_loop().create_future())
    ended: asyncio.Future = field(default_factory=lambda: asyncio.get_running_loop().create_future())
    answered: asyncio.Event = field(default_factory=asyncio.Event)   # a person: Twilio is opening the stream
    state: CallState = "dialing"
    claimed: bool = False
    hung_up: bool = False   # the line went down as the talk ended, before the Auditor

    @property
    def done(self) -> bool:
        return self.ended.done()


class OutboundCalls:
    """The listing calls on the phone right now, keyed on our call id (hard rule 4: on app.state, not a
    module dict). Webhooks and the media stream find their call here; ids they don't know are refused."""

    def __init__(self) -> None:
        self.live: dict[str, Outbound] = {}
        self.runner: TwilioRunner | None = None   # set by install() when TRANSPORT=voice

    def get(self, call_id: str) -> Outbound | None:
        call = self.live.get(call_id)
        return None if call is None or call.done else call

    async def set_state(self, call_id: str, state: CallState) -> None:
        """Ringing, live or voicemail, said to the Dispatcher once each and never after the end. A call
        that reached a voicemail stays one, even if its stream starts after detection decided."""
        call = self.get(call_id)
        if call is None or call.state in (state, "voicemail"):
            return
        call.state = state
        await call.report(state)

    def claim(self, call_id: str) -> Outbound | None:
        """The call a media stream belongs to. Works once per live call."""
        call = self.get(call_id)
        if call is None or call.claimed:
            return None
        call.claimed = True
        return call

    def finalize(self, call_id: str, reason: EndReason) -> bool:
        """Ends the call. Only the first end for a call does anything; returns whether this was it."""
        call = self.live.get(call_id)
        if call is None or call.done:
            return False
        call.ended.set_result(reason)
        if not call.media.done():
            call.media.cancel()
        log.info("listing call %s ended: %s", call_id, reason)
        return True

    def end_reason(self, call_id: str, status: str) -> EndReason | None:
        """What a final Twilio status means for this call: a call that left a voicemail ended `voicemail`,
        one that was answered and talked ended `hangup`, and "completed" before anyone talked is no answer."""
        reason = STATUS_END.get(status)
        call = self.live.get(call_id)
        if reason == "hangup" and call is not None:
            if call.state == "voicemail":
                return "voicemail"
            if call.state != "live":
                return "no_answer"
        return reason


# What the Caller does once a person is on the line: talk, then say how the call ended.
Converse = Callable[[Dial, Any], Awaitable[CallResult]]


async def no_caller(dial: Dial, transport: Any) -> CallResult:
    log.error("call %s answered, but no Caller is installed", dial.call_id)
    return CallResult("failed")


class TwilioRunner:
    """Places listing calls through Twilio's Calls API, without the SDK."""
    transport = "voice"
    uses_phone = True

    def __init__(self, settings: Settings, calls: OutboundCalls, *, runtime: SessionRuntime | None = None,
                 converse: Converse = no_caller, client: httpx.AsyncClient | None = None):
        self.settings, self.calls, self.runtime, self.converse = settings, calls, runtime, converse
        self.allowlist = frozenset(n for n in (as_e164(p) for p in settings.demo_agent_phones) if n)
        self.client = client or httpx.AsyncClient(timeout=TIMEOUT_S)

    @property
    def ready(self) -> bool:
        s = self.settings
        return bool(s.twilio_account_sid and s.twilio_auth_token and s.twilio_phone_number and s.public_url)

    # Placing one call

    async def place(self, dial: Dial, report: Report) -> CallResult:
        call = Outbound(dial=dial, report=report)
        self.calls.live[dial.call_id] = call
        warm: asyncio.Task | None = None   # the Higgs session opened once a person answered (#148)
        try:
            try:
                call.twilio_sid = await self.dial(dial)
            except NotAllowed:
                log.error("call %s: %s is not on the allowlist; not dialled", dial.call_id, _tail(dial.to))
                self.calls.finalize(dial.call_id, "blocked")
            except Exception:
                log.exception("call %s: Twilio would not place it", dial.call_id)
                self.calls.finalize(dial.call_id, "failed")
            # A person answered: open Higgs while Twilio connects the stream, when the Caller can. It runs in
            # this call's line, so it counts against the Dispatcher's limit like the call itself.
            prepare = getattr(self.converse, "prepare", None)
            if prepare is not None:
                answered = asyncio.ensure_future(call.answered.wait())
                try:
                    await asyncio.wait({answered, call.media, call.ended},
                                       return_when=asyncio.FIRST_COMPLETED)
                    if answered.done() and not call.done:
                        warm = asyncio.create_task(prepare(dial))
                finally:
                    answered.cancel()   # a deadline mid-wait never leaves it waiting on a call that's gone
            # A person answered and the stream connected, or the call ended first.
            done, _ = await asyncio.wait({call.media, call.ended}, return_when=asyncio.FIRST_COMPLETED)
            if call.media in done and not call.media.cancelled() and not call.done:
                higgs, warm = await _ready(warm, dial), None   # the call has it now, and closes it
                result = await self._converse(call, call.media.result(), higgs)
                if call.state == "voicemail":
                    # A machine after all: its voicemail is playing, and Twilio's final status ends the
                    # call `voicemail` once it has, so the line isn't put down on the message.
                    return CallResult(await call.ended)
                self.calls.finalize(dial.call_id, result.end_reason)
                return CallResult(await call.ended, result.outcome)
            return CallResult(await call.ended)
        finally:
            await _discard(warm)   # opened, but no stream came for it: closed, never left open
            # The deadline, a crash or the conversation ending: make sure Twilio's line is down too.
            self.calls.finalize(dial.call_id, "timeout")
            self.calls.live.pop(dial.call_id, None)
            if call.twilio_sid and not call.hung_up:
                with contextlib.suppress(Exception):
                    async with asyncio.timeout(HANG_UP_S):
                        await self._hang_up(call.twilio_sid)

    async def dial(self, dial: Dial) -> str:
        """Asks Twilio to call `dial.to`, and returns Twilio's call sid. The only place a listing call
        reaches the phone network, so it checks the allowlist itself, whoever called it."""
        to = as_e164(dial.to)
        if not to or to not in self.allowlist:
            raise NotAllowed(_tail(dial.to))
        s = self.settings
        if not self.ready:
            raise RuntimeError("Twilio outbound is not configured (account, token, number, PUBLIC_URL)")
        base, cid = s.public_url, dial.call_id
        r = await self.client.post(API.format(sid=s.twilio_account_sid),
                                   auth=(s.twilio_account_sid, s.twilio_auth_token), data={
            "To": to, "From": s.twilio_phone_number,
            "Url": f"{base}/twiml/outbound/{cid}",
            "StatusCallback": f"{base}/twilio/outbound/status/{cid}",
            "StatusCallbackEvent": ["initiated", "ringing", "answered", "completed"],
            "MachineDetection": "DetectMessageEnd",
            "AsyncAmd": "true",   # the Caller talks from the answer; a machine is caught on /amd
            "AsyncAmdStatusCallback": f"{base}/twilio/outbound/amd/{cid}",
            "AsyncAmdStatusCallbackMethod": "POST",
            "Timeout": str(RING_S),
            "TimeLimit": str(int(s.listing_call_max_s)),   # Twilio's own cap, behind our deadline
        })
        if r.status_code >= 300:
            raise RuntimeError(f"Twilio refused the call ({r.status_code}): {r.text[:300]}")
        sid = str(r.json().get("sid") or "")
        log.info("listing call %s dialling …%s (%s)", cid, to[-4:], sid)
        return sid

    async def _hang_up(self, twilio_sid: str) -> None:
        s = self.settings
        await self.client.post(HANG_UP_API.format(sid=s.twilio_account_sid, call_sid=twilio_sid),
                               auth=(s.twilio_account_sid, s.twilio_auth_token), data={"Status": "completed"})

    async def detected(self, call_id: str, answered_by: str) -> None:
        """Machine detection's verdict, posted while the Caller is already talking. A machine (after its
        beep): the call is switched to the voicemail, which hangs up. That ends the media stream, so the
        Caller stops and closes its Higgs session, and Twilio's final status ends the call `voicemail`.
        A person, unknown, or a call that's over: nothing."""
        call = self.calls.get(call_id)
        if call is None or answered_by not in MACHINE or not call.twilio_sid:
            return
        await self.calls.set_state(call_id, "voicemail")   # before the switch, which ends the Caller's stream
        s = self.settings
        try:
            r = await self.client.post(HANG_UP_API.format(sid=s.twilio_account_sid, call_sid=call.twilio_sid),
                                       auth=(s.twilio_account_sid, s.twilio_auth_token),
                                       data={"Twiml": twilio.twiml_say_hangup(self.voicemail(call.dial))})
            r.raise_for_status()
        except Exception as exc:
            # Never left talking to a machine: put the line down, and the final status still ends it.
            log.warning("listing call %s: the voicemail didn't start (%r); hanging up", call_id, exc)
            with contextlib.suppress(Exception):
                async with asyncio.timeout(HANG_UP_S):
                    await self._hang_up(call.twilio_sid)

    async def _converse(self, call: Outbound, transport: Any, higgs: Any = None) -> CallResult:
        kw: dict[str, Any] = {"higgs": higgs} if higgs is not None else {}
        if getattr(self.converse, "takes_hang_up", False):
            kw["hang_up"] = lambda reason: self._talk_over(call, reason)
        try:
            return await self.converse(call.dial, transport, **kw)
        except Exception:
            log.exception("call %s: the conversation failed", call.dial.call_id)
            return CallResult("failed")

    async def _talk_over(self, call: Outbound, reason: EndReason) -> None:
        """The Caller is done talking: the call ends with its reason, then the line goes down at once, while
        the Auditor works out the outcome from what was said. A machine's voicemail plays out instead, and
        Twilio's final status ends that call. A hang-up that fails is logged, and place() tries again."""
        if call.state == "voicemail":
            return
        self.calls.finalize(call.dial.call_id, reason)   # before the hang-up, whose "completed" says "hangup"
        if not call.twilio_sid:
            return
        try:
            async with asyncio.timeout(HANG_UP_S):
                await self._hang_up(call.twilio_sid)
            call.hung_up = True
        except Exception as exc:
            log.warning("listing call %s: hanging up failed (%r)", call.dial.call_id, exc)

    # What the webhooks hand back to Twilio

    def answered_twiml(self, call_id: str, answered_by: str, ws_url: str) -> str:
        """Just answered (no AnsweredBy: detection is still listening), a person, or Twilio couldn't tell:
        the media stream, at once. A machine: the voicemail, then hang up."""
        call = self.calls.get(call_id)
        if call is None:
            return twilio.twiml_hangup()
        if answered_by in MACHINE:
            return twilio.twiml_say_hangup(self.voicemail(call.dial))
        call.answered.set()   # place() opens Higgs while Twilio connects the stream (#148)
        return twilio.twiml_stream(ws_url, call_id)

    def voicemail(self, dial: Dial) -> str:
        return VOICEMAIL.format(address=self._address(dial))

    def _address(self, dial: Dial) -> str:
        listing = self.runtime.catalogue.by_id.get(dial.listing_id) if self.runtime is not None else None
        return (listing.address if listing and listing.address else None) or "your rental listing"

    async def close(self) -> None:
        await self.client.aclose()


ANSWERED = "[The call was just answered.]"


def opening(instructions: str, recorded: bool = False) -> tuple[str, list[dict[str, Any]]]:
    """The Caller's session instructions, with how the call opens (the disclosure word for word, hard rule 6,
    then RECORDED when `recorded`), and the events that start it: a quoted note and a plain
    response.create, as the sim starts a Caller's turn. Never a response object on response.create: its
    instructions replace the session's (#129)."""
    first = f"{DISCLOSURE} {RECORDED}" if recorded else DISCLOSURE
    opens = (f"\n\nHow this call opens: the call has just been answered. Your first words are exactly: "
             f"\"{first}\" Then say which listing you are calling about and ask if they have a minute.")
    note = {"type": "message", "role": "user", "content": [{"type": "input_text", "text": ANSWERED}]}
    return f"{instructions}{opens}", [{"type": "conversation.item.create", "item": note},
                                      {"type": "response.create"}]


class Turns:
    """A listing call's transcript and audio, written in the background so audio never waits on the
    database: each line to call_turns with its place in the audio when known, and chunks of both sides to
    call_audio (A-10, recorded by voice/recorder.py). The call's row already exists: the Dispatcher made it
    before dialling."""

    def __init__(self, db: Any, call_id: str) -> None:
        self.db, self.call_id, self.idx = db, call_id, 0
        self.lines: list[tuple[str, str]] = []   # (speaker, text) in order, for the Auditor
        self._writes: list[asyncio.Task] = []
        self._line_writes: dict[int, asyncio.Task] = {}   # idx -> the latest write of that line
        self._next_seq: dict[str, int] = {}               # track -> the next chunk's seq

    def said(self, speaker: str, text: str, *, start_ms: int | None = None,
             end_ms: int | None = None) -> int | None:
        """Stores a line, with where it is in the call's audio when known. Returns its idx, or None when
        nothing was stored (nothing said, or no database)."""
        text = text.strip()
        if not text:
            return None
        log.info("call %s %s: %s", self.call_id, speaker, text)
        self.lines.append((speaker, text))
        if self.db is None:
            return None
        idx = self.idx
        self.idx += 1
        self._line_writes[idx] = self._write(self.db.turns.append(
            self.call_id, idx, speaker, text, audio_start_ms=start_ms, audio_end_ms=end_ms))
        return idx

    def placed(self, idx: int, start_ms: int | None, end_ms: int | None) -> None:
        """A stored line's place in the audio changed (an agent line a barge-in cut short). The move waits
        for the line's earlier writes, so it never lands before the line does."""
        before, db = self._line_writes.get(idx), self.db
        if before is None or db is None:
            return

        async def move() -> None:
            await asyncio.gather(before, return_exceptions=True)
            await db.turns.place(self.call_id, idx, start_ms, end_ms)
        self._line_writes[idx] = self._write(move())

    def audio(self, track: str, start_ms: int, audio: bytes, *, fmt: str, rate: int) -> None:
        """Stores a chunk of one side of the call, `start_ms` into it. Chunks of a track keep their order."""
        if self.db is None or not audio:
            return
        seq = self._next_seq.get(track, 0)
        self._next_seq[track] = seq + 1
        self._write(self.db.audio.append(self.call_id, track, seq, start_ms, fmt, rate, audio))

    def _write(self, write: Awaitable[object]) -> asyncio.Task:
        task = asyncio.ensure_future(write)
        self._writes.append(task)
        return task

    async def flush(self) -> None:
        for result in await asyncio.gather(*self._writes, return_exceptions=True):
            if isinstance(result, Exception):
                log.warning("call %s: a turn was not saved: %r", self.call_id, result)


def _tail(number: str | None) -> str:
    return f"…{(number or '')[-4:]}"


async def _ready(warm: asyncio.Task | None, dial: Dial) -> Any:
    """The Higgs session opened for this call once a person answered; None when none was (the call opens
    its own) or opening it failed (the call tries again, as it would have)."""
    if warm is None:
        return None
    try:
        return await warm
    except Exception as exc:
        log.warning("listing call %s: the early Higgs session didn't open (%r)", dial.call_id, exc)
        return None


async def _discard(warm: asyncio.Task | None) -> None:
    """Closes an early Higgs session no stream came for (a hang-up between the answer and the stream), or
    stops it opening, so it never outlives its call or holds a Higgs line."""
    if warm is None:
        return
    if not warm.done():
        warm.cancel()
        with contextlib.suppress(BaseException):
            await warm
        return
    if not warm.cancelled() and warm.exception() is None:
        with contextlib.suppress(Exception):
            await warm.result().close()


def install(state: Any, settings: Settings, extractor: Any = None) -> OutboundCalls:
    """The app's hook: the registry the outbound webhooks use, with a Twilio runner for the Dispatcher when
    TRANSPORT=voice and none on other transports. Needs state.runtime and state.connect_higgs."""
    calls = OutboundCalls()
    if settings.transport == "voice":
        runner = TwilioRunner(settings, calls, runtime=state.runtime,
                              converse=_caller(state, settings, extractor))
        if not runner.ready:
            log.warning("TRANSPORT=voice but Twilio outbound isn't configured: every listing call will fail")
        calls.runner = runner
    return calls


def _caller(state: Any, settings: Settings, extractor: Any) -> Converse:
    """The Caller (A-05) on the phone. Imported here so this module loads without agents/caller."""
    try:
        from landline.voice.phone_caller import PhoneCaller
    except ImportError:
        log.exception("TRANSPORT=voice but the Caller can't load: answered calls will end failed")
        return no_caller
    return PhoneCaller(state.runtime, lambda key, cfg: state.connect_higgs(key, cfg),
                       settings.voice_api_key() or "", voice=settings.live_voice(),
                       extractor=extractor, record_audio=settings.record_audio,
                       clips=getattr(state, "clips", None))


async def uninstall(calls: OutboundCalls) -> None:
    if calls.runner is not None:
        await calls.runner.close()
