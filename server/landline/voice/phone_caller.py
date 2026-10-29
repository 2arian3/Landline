"""The Caller (A-05) on a real phone line: the `converse` the Twilio runner (voice/outbound.py) runs once a
person has answered. The brain is A-05's (agents/caller: brief, instructions, tools, outcome); this is only
the audio loop around it, the phone twin of `SimRunner` in voice/sim.py.

The listing agent's words, as Higgs transcribes them, go into `ctx.said` and nothing else does: grounding
and red flags read only what the other person said. Tool calls run through the bridge's `run_tool` with the
Caller's tools, so the goodbye guard is the renter call's. After each Caller turn, `kept_promise` notes a
question the Caller promised to bring back but didn't. The call returns a little before the Dispatcher's
deadline, so the outcome keeps what was heard.

Both sides are recorded the way a renter call is (A-10, voice/recorder.py): the listing agent's voice on the
`landlord` track, the Caller's on `agent`, and each transcript line at its place in them, stored through
`Turns` every few seconds and once more at the end. With RECORD_AUDIO=0 no audio is stored (the lines still
get their place) and the Caller doesn't say the call is recorded.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from landline.agents import auditor
from landline.agents.auditor.extract import Complete
from landline.agents.caller import tools as caller_tools
from landline.agents.caller.brief import brief, disclosure, instructions, wants
from landline.contracts import CallOutcome, Listing, Preferences, Viewing
from landline.orchestrator import SessionRuntime
from landline.orchestrator.memory import Known, Memory, recall
from landline.voice import bridge
from landline.voice.avatar_clip import Clips, gender_of_voice
from landline.voice.bridge import (
    HANG_UP_GRACE_S,
    KEEP_AUDIO_EVERY_S,
    Playback,
    discard_tool,
    dropping,
    note_speech,
    note_user_turn,
    open_response,
    response_settles,
)
from landline.voice.dispatch import CallResult, Dial
from landline.voice.higgs import PCMU, ConnectHiggs, session_config
from landline.voice.outbound import Turns, opening
from landline.voice.recorder import CallRecorder, Chunk, Placed, format_of

log = logging.getLogger(__name__)

DEADLINE_MARGIN_S = 5   # as in the sim: end before the Dispatcher does, with the outcome
END_AFTER_S = 3.0       # once a call is over (caller_tools.over), how long its last words get for end_call
WRAP_UP_S = 3.0         # once the Caller has what it came for, how long a quiet line waits for it to wrap up
QUIET_EVERY_S = 0.25    # how often the line is checked for quiet
WRAP_UP = "[Wrap up now: thank them in one short sentence, say goodbye, and call end_call.]"
KEEP_KNOWN = 32         # calls whose memory read is kept; one never answered is dropped as newer ones come


class PhoneCaller:
    takes_hang_up = True   # the runner hands `hang_up`, to put the line down as soon as the talk is over

    def __init__(self, runtime: SessionRuntime, connect_higgs: ConnectHiggs, api_key: str,
                 voice: str = "default", clock=lambda: datetime.now(UTC), extractor: Complete | None = None,
                 record_audio: bool = True, clips: Clips | None = None, memory: Memory | None = None):
        self.runtime, self.connect, self.api_key, self.voice, self.clock = (
            runtime, connect_higgs, api_key, voice, clock)
        self.extractor = extractor   # the Auditor's text LLM (A-07); None: rules and the live record only
        self.record_audio = record_audio   # Settings.record_audio: store both sides' audio (A-10)
        self.clips = clips             # the face clip, rendered after this call's audio is stored
        self.memory = memory           # what earlier calls heard, to confirm (O-09b); None: asks cold
        self._known: OrderedDict[str, asyncio.Task[list[Known]]] = OrderedDict()

    def _preferences(self, session_id: str) -> Preferences | None:
        """The renter's preferences, for what the checklist asks; None once the session is gone."""
        return self.runtime.state(session_id).preferences if self.runtime.has(session_id) else None

    def recall(self, dial: Dial) -> asyncio.Task[list[Known]]:
        """What earlier calls heard about the place (O-09b), read once per call. The runner starts it as it
        dials, so it's in before anyone answers; it gives up after RECALL_S and never raises, and the brief
        then asks cold. `prepare` and the call share it."""
        task = self._known.get(dial.call_id)
        if task is None:
            task = asyncio.ensure_future(recall(self.memory, dial.listing_id, now=self.clock()))
            self._known[dial.call_id] = task
            while len(self._known) > KEEP_KNOWN:
                self._known.popitem(last=False)
        return task

    async def _texts_confirm(self, dial: Dial) -> bool:
        """A booking on this call texts the number it dialled a confirm link (O-08b): only once that number
        is stored on the call (calls.to_e164, which A-13a fills), so the Caller never promises a text."""
        db = self.runtime.db
        if db is None:
            return False
        try:
            row = await db.calls.get(dial.call_id)
        except Exception:
            log.exception("call %s: could not read its dialled number", dial.call_id)
            return False
        return bool(row and row.to_e164)

    async def _setup(self, dial: Dial) -> tuple[Listing, Any, dict[str, Any], list[dict[str, Any]]]:
        """The call's listing, its brief, its Higgs session config and the events that open it."""
        known = await asyncio.shield(self.recall(dial))   # a prepare cut short never cancels the read
        listing = self.runtime.catalogue.by_id[dial.listing_id]
        b = brief(listing, dial.plan, wants(self._preferences(dial.session_id)), known=known,
                  now=self.clock(), texts_confirm=await self._texts_confirm(dial), callback=dial.callback)
        text, start = opening(instructions(b), recorded=self.record_audio, first=disclosure(b))
        config = session_config(instructions=text, tools=caller_tools.TOOLS, voice=self.voice,
                                audio_format=PCMU)
        return listing, b, config, start

    async def prepare(self, dial: Dial) -> Any:
        """Opens the call's Higgs session once a person has answered, while Twilio still connects the stream,
        so the connect and session.update no longer wait for it (#148). The runner hands it to the call, or
        closes it if no stream comes."""
        return await self.connect(self.api_key, (await self._setup(dial))[2])

    async def __call__(self, dial: Dial, transport: Any, higgs: Any = None,
                       hang_up: Callable[[str], Awaitable[None]] | None = None) -> CallResult:
        """Talks the call through; `higgs` is a session `prepare` opened for it, else one opens now. The
        call closes it either way. `hang_up` ends the call and puts the line down as soon as the talk is
        over, so the listing agent never waits on the Auditor, which needs only what was said."""
        listing, b, config, start = await self._setup(dial)
        self._known.pop(dial.call_id, None)
        ctx = caller_tools.CallerContext(session_id=dial.session_id, call_id=dial.call_id,
                                         listing_id=dial.listing_id, brief=b, runtime=self.runtime,
                                         clock=self.clock)
        reason = "failed"
        keeper: asyncio.Task | None = None
        turns = Turns(self.runtime.db, dial.call_id)
        # Its clock starts as the stream does, as in the bridge: the listing agent's audio waits on the
        # stream while Higgs connects, so both tracks start together.
        recorder = CallRecorder(format_of(PCMU), caller="landlord")
        try:
            try:
                if higgs is None:
                    higgs = await self.connect(self.api_key, config)
                for event in start:
                    await higgs.send(event)
                # With "answered by" and "stream started" (api/twilio.py) and "first audio out" below, the
                # log's timestamps split the silence after a pickup into its steps (#148).
                log.info("listing call %s: Higgs ready, opening sent", dial.call_id)
                keeper = asyncio.create_task(_keep_every(turns, recorder, self.record_audio))
                left = (dial.deadline - self.clock()).total_seconds() - DEADLINE_MARGIN_S
                try:
                    async with asyncio.timeout(max(1.0, left)):
                        reason = await _talk(higgs, transport, ctx, turns, Playback(8000), recorder)
                except TimeoutError:
                    reason = ctx.end_reason or "timeout"
            except Exception:
                log.exception("phone call %s failed", dial.call_id)
                reason = ctx.end_reason or "failed"
            if hang_up is not None:   # not when cancelled: the runner ends a call cut at its deadline
                await hang_up(reason)
        finally:
            if keeper is not None:
                keeper.cancel()
                await asyncio.gather(keeper, return_exceptions=True)
            if higgs is not None:
                with contextlib.suppress(Exception):
                    await higgs.close()
            try:   # the rest of the audio, never in the way of ending the call
                _keep(turns, recorder, recorder.finish(), self.record_audio)
            except Exception:
                log.exception("could not keep the end of call %s's audio", dial.call_id)
            await turns.flush()
            if self.clips is not None:
                self.clips.schedule(dial.call_id, agent_gender=gender_of_voice(self.voice))
        viewing = Viewing(starts_at=ctx.booked, slot_text=ctx.slot_text or "") if ctx.booked else None
        return CallResult(reason, await self._audit(dial, listing, reason, ctx, turns, viewing))

    async def _audit(self, dial: Dial, listing: Listing, reason: str, ctx: Any, turns: Turns,
                     viewing: Viewing | None) -> CallOutcome:
        """The Auditor (A-07) on the transcript, inside what's left of the call's deadline, as in the sim:
        with no time left, or no extractor, it runs on the Caller's grounded record and the rules alone."""
        left = (dial.deadline - self.clock()).total_seconds() - 1
        extractor = self.extractor if left > 2 else None
        record = auditor.CallRecord(
            call_id=dial.call_id, listing_id=dial.listing_id, end_reason=reason,  # type: ignore[arg-type]
            turns=[auditor.Turn(i, speaker, text) for i, (speaker, text) in enumerate(turns.lines)],
            listing=auditor.ListingContext.of(listing), ended_at=self.clock(),
            tool_facts=[auditor.ToolFact(f.field, f.value, f.quote) for f in ctx.facts],
            questions=ctx.questions, viewing=viewing)
        return await auditor.outcome(record, complete=extractor, timeout_s=max(1.0, left))


async def _talk(higgs: Any, transport: Any, ctx: Any, turns: Turns, playback: Playback,
                recorder: CallRecorder) -> str:
    """Audio both ways until the Caller's goodbye has played (end_call), the agent hangs up, or the call is
    over with no end_call (ends_itself). A Caller that has what it came for on a line gone quiet is told to
    wrap up (wraps_up). The recorder hears both sides, as the bridge's does, through a guard: a recorder
    error never ends the call."""
    rec = _Guard(ctx.call_id)

    async def listen() -> str:
        while (audio := await transport.receive_audio()) is not None:
            rec(recorder.heard, audio)
            await higgs.send({"type": "input_audio_buffer.append", "audio": audio})
        return "hangup"

    async def speak() -> str:
        spoke, said, called, sent_any = False, "", [], False
        # The opening (the disclosure, hard rule 6) isn't cut: a "Hello?" over it doesn't clear Twilio's
        # audio until its response is done and has played. Higgs answers that speech once it has.
        opening, opened = True, False
        async for event in higgs.events():
            kind = event.get("type")
            if kind == "response.created":
                open_response(ctx)
                spoke = False
                busy.add("reply")
            elif dropping(ctx):
                if kind == "response.output_audio.delta":
                    pass
                elif kind == "response.function_call_arguments.done":
                    await discard_tool(higgs, event, ctx)
                elif kind == "response.done":
                    ctx.dropping = False
                    busy.discard("reply")
                    if not ctx.hang_up:
                        await caller_tools.kept_promise(ctx, said, called)
                        if caller_tools.over(ctx, said, called):
                            over.set()
                        said, called = "", []
            elif kind == "response.output_audio.delta":
                spoke = True
                note_speech(ctx)
                await transport.send_audio(event["delta"])
                playback.sent(event["delta"])
                rec(recorder.played, event["delta"], event.get("item_id"))
                if not sent_any:
                    sent_any = True
                    log.info("listing call %s: first audio out", ctx.call_id)
            elif kind == "input_audio_buffer.speech_started":
                # Their line starts here either way; only a barge-in cuts what the Caller was saying.
                note_user_turn(ctx)
                busy.add("them")
                rec(recorder.speech_started, event.get("item_id"), event.get("audio_start_ms"))
                if opening and (not opened or playback.remaining() > 0):
                    log.info("listing call %s: speech over the opening; it plays on", ctx.call_id)
                    continue
                opening = False
                await transport.send_clear()
                playback.clear()
                rec(recorder.cut)
            elif kind == "input_audio_buffer.speech_stopped":
                busy.discard("them")
                rec(recorder.speech_stopped, event.get("item_id"), event.get("audio_end_ms"))
            elif kind == "conversation.item.input_audio_transcription.completed":
                heard = str(event.get("transcript") or "").strip()
                if heard:
                    ctx.said.append(heard)
                    _said(turns, recorder, "landlord", heard, event, rec)
            elif kind == "response.output_audio_transcript.done":
                heard_line = str(event.get("transcript") or "")
                if heard_line.strip():
                    note_speech(ctx)
                said = " ".join(f"{said} {heard_line}".split())
                _said(turns, recorder, "agent", heard_line, event, rec)
            elif kind == "response.function_call_arguments.done":
                await bridge.run_tool(higgs, event, ctx, spoke=spoke, agent=caller_tools)
                called.append(str(event.get("name", "")))
            elif kind == "response.done":
                busy.discard("reply")
                opened = True   # the opening's response is over; its audio may still be playing
                if bridge.ends_call(event, ctx):
                    await asyncio.sleep(playback.remaining() + HANG_UP_GRACE_S)
                    return ctx.end_reason or "done"
                if not ctx.hang_up and response_settles(ctx, event):
                    await caller_tools.kept_promise(ctx, said, called)   # the Caller's turn is over
                    if caller_tools.over(ctx, said, called):
                        over.set()
                    said, called = "", []
            elif kind == "error":
                log.warning("call %s Higgs error: %s", ctx.call_id, event.get("error"))
        return "hangup"

    over = asyncio.Event()
    busy: set[str] = set()   # "them" while the listing agent speaks, "reply" while the Caller's answer comes

    async def ends_itself() -> str:
        """A call that is over (caller_tools.over) and still open ends here: once everything the Caller said
        has played, nobody is speaking or about to, and END_AFTER_S more have passed with no end_call. Its
        last words, and theirs after a goodbye, are never cut."""
        await over.wait()
        while True:
            await asyncio.sleep(playback.remaining() + END_AFTER_S)
            if playback.remaining() <= 0 and not busy:
                log.info("listing call %s: over with no end_call; hanging up", ctx.call_id)
                return ctx.end_reason or "done"

    async def wraps_up() -> str:
        """The Caller has what it came for (_has_it) and the line has been quiet WRAP_UP_S, with nobody
        speaking, no answer coming and nothing left playing, yet no goodbye: a listing agent who just goes
        quiet would otherwise hold the line open. It's told once, as a quoted note and a plain
        response.create (as the call opens), to thank them, say goodbye and call end_call; if it says
        goodbye without end_call, ends_itself ends the call. Never ends the call itself."""
        clock = asyncio.get_running_loop().time
        quiet_since = clock()
        while True:
            await asyncio.sleep(QUIET_EVERY_S)
            if busy or playback.remaining() > 0:
                quiet_since = clock()
            elif (clock() - quiet_since >= WRAP_UP_S and not ctx.hang_up and not over.is_set()
                  and _has_it(ctx)):
                break
        log.info("listing call %s: done and quiet; wrapping up", ctx.call_id)
        note = {"type": "message", "role": "user", "content": [{"type": "input_text", "text": WRAP_UP}]}
        await higgs.send({"type": "conversation.item.create", "item": note})
        await higgs.send({"type": "response.create"})
        await asyncio.Event().wait()   # once only; end_call or ends_itself ends the call

    pumps = {asyncio.create_task(listen()), asyncio.create_task(speak()), asyncio.create_task(ends_itself()),
             asyncio.create_task(wraps_up())}
    try:
        done, _ = await asyncio.wait(pumps, return_when=asyncio.FIRST_COMPLETED)
        return next(iter(done)).result()
    finally:
        for task in pumps:
            task.cancel()
        await asyncio.gather(*pumps, return_exceptions=True)


def _has_it(ctx: Any) -> bool:
    """Whether the Caller has what it came for: its checklist covered (the viewing booked or settled with
    it), or a scam warning heard, after which it asks nothing more."""
    return not ctx.brief.missing() or bool(ctx.flags())


class _Guard:
    """Runs the recorder's calls inside a live call's audio loop. The first one that fails is logged and the
    recorder stays off for the rest of the call: the recording is a record (A-10), the call is the job."""

    def __init__(self, call_id: str) -> None:
        self.call_id, self.off = call_id, False

    def __call__(self, fn: Any, *args: Any) -> Any:
        if self.off:
            return None
        try:
            return fn(*args)
        except Exception:
            self.off = True
            log.exception("listing call %s: the recorder failed; the call goes on without it", self.call_id)
            return None


def _said(turns: Turns, recorder: CallRecorder, speaker: str, text: str, event: dict[str, Any],
          rec: _Guard) -> None:
    """Stores a transcript line at its place in the call's audio; with the recorder off, without its place,
    so the line itself (which the Auditor reads) is never lost."""
    item_id = event.get("item_id")
    start_ms, end_ms = rec(recorder.span, item_id) or (None, None)
    rec(recorder.stored, item_id, turns.said(speaker, text, start_ms=start_ms, end_ms=end_ms))


async def _keep_every(turns: Turns, recorder: CallRecorder, record: bool) -> None:
    """Stores what the recorder has ready every few seconds, so a crash loses only the last few."""
    while True:
        await asyncio.sleep(KEEP_AUDIO_EVERY_S)
        try:
            _keep(turns, recorder, recorder.take(), record)
        except Exception:
            log.exception("could not keep call %s's audio", turns.call_id)


def _keep(turns: Turns, recorder: CallRecorder, taken: tuple[list[Chunk], list[Placed]],
          record: bool) -> None:
    chunks, moved = taken
    if record:
        fmt = recorder.fmt
        for chunk in chunks:
            turns.audio(chunk.track, chunk.start_ms, chunk.audio, fmt=fmt.name, rate=fmt.rate)
    for line in moved:
        turns.placed(line.idx, line.start_ms, line.end_ms)
