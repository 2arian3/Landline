"""The Caller (A-05) on a real phone line: the `converse` the Twilio runner (voice/outbound.py) runs once a
person has answered. The brain is A-05's (agents/caller: brief, instructions, tools, outcome); this is only
the audio loop around it, the phone twin of `SimRunner` in voice/sim.py.

The listing agent's words, as Higgs transcribes them, go into `ctx.said` and nothing else does: grounding
and red flags read only what the other person said. Tool calls run through the bridge's `run_tool` with the
Caller's tools, so the goodbye guard is the renter call's. After each Caller turn, `kept_promise` notes a
question the Caller promised to bring back but didn't. The call returns a little before the Dispatcher's
deadline, so the outcome keeps what was heard.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import UTC, datetime
from typing import Any

from landline.agents import auditor
from landline.agents.auditor.extract import Complete
from landline.agents.caller import tools as caller_tools
from landline.agents.caller.brief import brief, instructions
from landline.contracts import CallOutcome, Listing, Viewing
from landline.orchestrator import SessionRuntime
from landline.voice import bridge
from landline.voice.bridge import HANG_UP_GRACE_S, Playback
from landline.voice.dispatch import CallResult, Dial
from landline.voice.higgs import PCMU, ConnectHiggs, session_config
from landline.voice.outbound import Turns, opening

log = logging.getLogger(__name__)

DEADLINE_MARGIN_S = 5   # as in the sim: end before the Dispatcher does, with the outcome


class PhoneCaller:
    def __init__(self, runtime: SessionRuntime, connect_higgs: ConnectHiggs, api_key: str,
                 voice: str = "default", clock=lambda: datetime.now(UTC), extractor: Complete | None = None):
        self.runtime, self.connect, self.api_key, self.voice, self.clock = (
            runtime, connect_higgs, api_key, voice, clock)
        self.extractor = extractor   # the Auditor's text LLM (A-07); None: rules and the live record only

    async def __call__(self, dial: Dial, transport: Any) -> CallResult:
        listing = self.runtime.catalogue.by_id[dial.listing_id]
        b = brief(listing, dial.plan)
        ctx = caller_tools.CallerContext(session_id=dial.session_id, call_id=dial.call_id,
                                         listing_id=dial.listing_id, brief=b, runtime=self.runtime,
                                         clock=self.clock)
        text, start = opening(instructions(b))
        reason = "failed"
        higgs = None
        turns = Turns(self.runtime.db, dial.call_id)
        try:
            higgs = await self.connect(self.api_key, session_config(
                instructions=text, tools=caller_tools.TOOLS, voice=self.voice, audio_format=PCMU))
            for event in start:
                await higgs.send(event)
            left = (dial.deadline - self.clock()).total_seconds() - DEADLINE_MARGIN_S
            try:
                async with asyncio.timeout(max(1.0, left)):
                    reason = await _talk(higgs, transport, ctx, turns, Playback(8000))
            except TimeoutError:
                reason = ctx.end_reason or "timeout"
        except Exception:
            log.exception("phone call %s failed", dial.call_id)
            reason = ctx.end_reason or "failed"
        finally:
            if higgs is not None:
                with contextlib.suppress(Exception):
                    await higgs.close()
            await turns.flush()
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


async def _talk(higgs: Any, transport: Any, ctx: Any, turns: Turns, playback: Playback) -> str:
    """Audio both ways until the Caller's goodbye has played (end_call) or the agent hangs up."""
    async def listen() -> str:
        while (audio := await transport.receive_audio()) is not None:
            await higgs.send({"type": "input_audio_buffer.append", "audio": audio})
        return "hangup"

    async def speak() -> str:
        spoke, said, called = False, "", []
        async for event in higgs.events():
            kind = event.get("type")
            if kind == "response.created":
                spoke = False
            elif kind == "response.output_audio.delta":
                spoke = True
                await transport.send_audio(event["delta"])
                playback.sent(event["delta"])
            elif kind == "input_audio_buffer.speech_started":
                await transport.send_clear()
                playback.clear()
            elif kind == "conversation.item.input_audio_transcription.completed":
                heard = str(event.get("transcript") or "").strip()
                if heard:
                    ctx.said.append(heard)
                    turns.said("landlord", heard)
            elif kind == "response.output_audio_transcript.done":
                said = " ".join(f"{said} {event.get('transcript') or ''}".split())
                turns.said("agent", str(event.get("transcript") or ""))
            elif kind == "response.function_call_arguments.done":
                await bridge.run_tool(higgs, event, ctx, spoke=spoke, agent=caller_tools)
                called.append(str(event.get("name", "")))
            elif kind == "response.done":
                if bridge.ends_call(event, ctx):
                    await asyncio.sleep(playback.remaining() + HANG_UP_GRACE_S)
                    return ctx.end_reason or "done"
                output = (event.get("response") or {}).get("output") or []
                if not ctx.hang_up and not any(o.get("type") == "function_call" for o in output):
                    await caller_tools.kept_promise(ctx, said, called)   # the Caller's turn is over
                    said, called = "", []
            elif kind == "error":
                log.warning("call %s Higgs error: %s", ctx.call_id, event.get("error"))
        return "hangup"

    pumps = {asyncio.create_task(listen()), asyncio.create_task(speak())}
    try:
        done, _ = await asyncio.wait(pumps, return_when=asyncio.FIRST_COMPLETED)
        return next(iter(done)).result()
    finally:
        for task in pumps:
            task.cancel()
        await asyncio.gather(*pumps, return_exceptions=True)
