"""The sim transport (A-03): a listing call with no phone. The Caller (A-05) talks to a landlord persona,
both on Higgs Realtime in text mode, and this plugs into the Dispatcher as its `CallRunner`.

The persona picks up first. Each side's words go to the other as user `input_text`; the Caller's tool
calls run through the bridge's `run_tool` with the Caller's own tools, so the goodbye guard is the same as
on a renter's call. A call ends when the Caller's end_call has played, the persona hangs up, the turn cap
is hit, or the deadline nears; in every case the outcome holds what was heard and grounded so far. The
runner keeps the last calls' turns, tool calls and events (`traces`) for the evals and for logs.

Turned on with TRANSPORT=sim. It never dials: `uses_phone` is False, so the Dispatcher skips the
allowlist and business hours, and nothing reaches the phone network (hard rule 6).
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from difflib import SequenceMatcher
from typing import Any

from landline.agents import auditor
from landline.agents.auditor.extract import Complete
from landline.agents.caller import tools as caller_tools
from landline.agents.caller.brief import brief, instructions, wants
from landline.contracts import Listing, Preferences, Viewing
from landline.orchestrator import SessionRuntime
from landline.voice.bridge import ends_call, run_tool
from landline.voice.dispatch import CallResult, Dial, Report
from landline.voice.higgs import ConnectHiggs, session_config
from landline.voice.outbound import Turns
from landline.voice.personas import DEMO, HANG_UP, Persona

log = logging.getLogger(__name__)

MAX_TURNS = 14          # the landlord's turns; a checklist call needs about 8
TURN_TIMEOUT_S = 30
DEADLINE_MARGIN_S = 5   # return before the Dispatcher's deadline, so the outcome keeps what was heard
KEEP_TRACES = 50
STUCK_REPEATS = 3     # the same Caller line this many turns running ends the call


@dataclass
class Turn:
    speaker: str        # "landlord" | "caller"
    text: str
    tools: list[str] = field(default_factory=list)
    ms: int = 0


@dataclass
class SimTrace:
    call_id: str
    listing_id: str
    persona: str
    instructions: str = ""
    turns: list[Turn] = field(default_factory=list)
    tool_calls: list[dict] = field(default_factory=list)   # turn, name, arguments, output
    end_reason: str = ""
    ungrounded: int = 0
    noted_by_code: int = 0     # questions the Caller promised to check but didn't note; code noted them
    events: dict[str, list[dict]] = field(default_factory=lambda: {"caller": [], "landlord": []})
    captions: Turns | None = field(default=None, repr=False)   # each line to call_turns, for live captions

    def add(self, turn: Turn) -> None:
        self.turns.append(turn)
        if self.captions is not None:
            self.captions.said("landlord" if turn.speaker == "landlord" else "agent", turn.text)


def demo_persona(dial: Dial, listing: Listing) -> Persona:
    """Judge mode: the demo personas in turn, by the listing's place in the renter's plan."""
    n = dial.plan.listing_ids.index(dial.listing_id) if dial.listing_id in dial.plan.listing_ids else 0
    return DEMO[n % len(DEMO)]


class SimRunner:
    transport = "sim"
    uses_phone = False

    def __init__(self, runtime: SessionRuntime, connect_higgs: ConnectHiggs, api_key: str, *,
                 persona_for: Callable[[Dial, Listing], Persona] = demo_persona,
                 clock: Callable[[], datetime] = lambda: datetime.now(UTC),
                 max_turns: int = MAX_TURNS, turn_timeout_s: float = TURN_TIMEOUT_S,
                 extractor: Complete | None = None):
        self.runtime, self.connect, self.api_key = runtime, connect_higgs, api_key
        self.extractor = extractor   # the Auditor's text LLM (A-07); None: rules and the live record only
        self.persona_for, self.clock = persona_for, clock
        self.max_turns, self.turn_timeout_s = max_turns, turn_timeout_s
        self.traces: OrderedDict[str, SimTrace] = OrderedDict()

    def _preferences(self, session_id: str) -> Preferences | None:
        """The renter's preferences, for what the checklist asks; None once the session is gone."""
        return self.runtime.state(session_id).preferences if self.runtime.has(session_id) else None

    async def place(self, dial: Dial, report: Report) -> CallResult:
        listing = self.runtime.catalogue.by_id[dial.listing_id]
        persona = self.persona_for(dial, listing)
        b = brief(listing, dial.plan, wants(self._preferences(dial.session_id)))
        ctx = caller_tools.CallerContext(session_id=dial.session_id, call_id=dial.call_id,
                                         listing_id=dial.listing_id, brief=b, runtime=self.runtime,
                                         clock=self.clock)
        trace = SimTrace(call_id=dial.call_id, listing_id=dial.listing_id, persona=persona.id,
                         instructions=instructions(b), captions=Turns(self.runtime.db, dial.call_id))
        self._keep(trace)
        reason = "failed"
        sessions: list[Any] = []
        try:
            await report("ringing")
            caller = _Side(await self.connect(self.api_key, session_config(
                instructions=trace.instructions, tools=caller_tools.TOOLS, modality="text")),
                trace.events["caller"])
            sessions.append(caller)
            landlord = _Side(await self.connect(self.api_key, session_config(
                instructions=persona.instructions(listing), tools=[], modality="text")),
                trace.events["landlord"])
            sessions.append(landlord)
            left = (dial.deadline - self.clock()).total_seconds() - DEADLINE_MARGIN_S
            try:
                async with asyncio.timeout(max(1.0, left)):
                    reason = await self._talk(caller, landlord, ctx, trace, persona, report)
            except TimeoutError:
                reason = ctx.end_reason or "timeout"
        except Exception:
            log.exception("sim call %s failed", dial.call_id)
            reason = ctx.end_reason or "failed"
        finally:
            for side in sessions:
                with contextlib.suppress(Exception):
                    await side.higgs.close()
            if trace.captions is not None:
                await trace.captions.flush()
        trace.end_reason, trace.ungrounded, trace.noted_by_code = reason, ctx.ungrounded, ctx.noted_by_code
        viewing = Viewing(starts_at=ctx.booked, slot_text=ctx.slot_text or "") if ctx.booked else None
        return CallResult(reason, await self._audit(dial, listing, reason, ctx, trace, viewing))

    async def _audit(self, dial: Dial, listing: Listing, reason: str, ctx: caller_tools.CallerContext,
                     trace: SimTrace, viewing: Viewing | None):
        """The Auditor (A-07) on the transcript, inside what's left of the call's deadline: with no time
        left, or no extractor, it runs on the Caller's grounded record and the rules alone."""
        left = (dial.deadline - self.clock()).total_seconds() - 1
        extractor = self.extractor if left > 2 else None
        record = auditor.CallRecord(
            call_id=dial.call_id, listing_id=dial.listing_id, end_reason=reason,  # type: ignore[arg-type]
            turns=[auditor.Turn(i, "landlord" if t.speaker == "landlord" else "agent", t.text)
                   for i, t in enumerate(trace.turns)],
            listing=auditor.ListingContext.of(listing), ended_at=self.clock(),
            tool_facts=[auditor.ToolFact(f.field, f.value, f.quote) for f in ctx.facts],
            questions=ctx.questions, viewing=viewing)
        return await auditor.outcome(record, complete=extractor, timeout_s=max(1.0, left))

    async def _talk(self, caller: _Side, landlord: _Side, ctx: caller_tools.CallerContext, trace: SimTrace,
                    persona: Persona, report: Report) -> str:
        await landlord.send({"type": "response.create"})          # it picks up first
        heard, hung_up = await self._landlord_turn(landlord, trace)
        await report("voicemail" if persona.voicemail else "live")
        for _ in range(self.max_turns):
            ctx.said.append(heard)
            await _say(caller, heard)
            said = await self._caller_turn(caller, ctx, trace)
            if ctx.hang_up:
                return ctx.end_reason or "done"
            if hung_up:
                return "hangup"
            if caller_tools.over(ctx, said, trace.turns[-1].tools):   # over, with no end_call: code ends it
                return ctx.end_reason or "done"
            if _stuck(trace):
                log.info("sim call %s: the Caller is repeating itself; ending it", trace.call_id)
                return "done"
            await _say(landlord, said)
            heard, hung_up = await self._landlord_turn(landlord, trace)
            if hung_up and not heard:
                return "hangup"
        return "timeout"

    async def _landlord_turn(self, side: _Side, trace: SimTrace) -> tuple[str, bool]:
        started, texts = time.monotonic(), []
        async with asyncio.timeout(self.turn_timeout_s):
            async for event in side.stream:
                kind = event.get("type")
                if kind == "response.output_text.done":
                    texts.append(str(event.get("text") or ""))
                elif kind == "response.done":
                    break
        text = " ".join(t.strip() for t in texts if t.strip())
        hung_up = HANG_UP in text.lower()
        text = " ".join(text.replace(HANG_UP, " ").replace(HANG_UP.title(), " ").split())
        trace.add(Turn("landlord", text, ms=round((time.monotonic() - started) * 1000)))
        return text, hung_up

    async def _caller_turn(self, side: _Side, ctx: caller_tools.CallerContext, trace: SimTrace) -> str:
        """Until the Caller's turn is over: a response.done that asked for no tool, or the one that ends the
        call after end_call. Tool calls run on the way, as on a renter's call."""
        started, texts, names, spoke = time.monotonic(), [], [], False
        async with asyncio.timeout(self.turn_timeout_s):
            async for event in side.stream:
                kind = event.get("type")
                if kind == "response.created":
                    spoke = False
                elif kind == "response.output_text.delta":   # streams before a tool call; .done comes after
                    spoke = spoke or bool(str(event.get("delta") or "").strip())
                elif kind == "response.output_text.done":
                    texts.append(str(event.get("text") or ""))
                elif kind == "response.function_call_arguments.done":
                    await run_tool(side, event, ctx, spoke=spoke, agent=caller_tools)
                    names.append(str(event.get("name", "")))
                    trace.tool_calls.append({"turn": len(trace.turns), "name": names[-1],
                                             "arguments": str(event.get("arguments") or "{}"),
                                             "output": side.last_output()})
                elif kind == "response.done":
                    output = (event.get("response") or {}).get("output") or []
                    if ends_call(event, ctx) or (not ctx.hang_up and
                                                 not any(o.get("type") == "function_call" for o in output)):
                        break
        text = " ".join(t.strip() for t in texts if t.strip())
        trace.add(Turn("caller", text, names, round((time.monotonic() - started) * 1000)))
        await caller_tools.kept_promise(ctx, text, names)
        return text

    def _keep(self, trace: SimTrace) -> None:
        self.traces[trace.call_id] = trace
        while len(self.traces) > KEEP_TRACES:
            self.traces.popitem(last=False)


class _Side:
    """One side's Higgs session, keeping every event to and from it in the order seen, and one iterator
    over its events for the whole call, so each turn reads on from where the last one stopped."""

    def __init__(self, higgs: Any, log_to: list[dict]):
        self.higgs, self.log = higgs, log_to
        self.stream = self._events().__aiter__()

    async def send(self, event: dict[str, Any]) -> None:
        self.log.append({"dir": "out", "event": event})
        await self.higgs.send(event)

    async def _events(self):
        async for event in self.higgs.events():
            self.log.append({"dir": "in", "event": event})
            yield event

    def last_output(self) -> str:
        """What run_tool just sent back for the tool call."""
        for entry in reversed(self.log):
            item = entry["event"].get("item") or {}
            if entry["dir"] == "out" and item.get("type") == "function_call_output":
                return str(item.get("output", ""))
        return ""


def _stuck(trace: SimTrace, repeats: int = STUCK_REPEATS) -> bool:
    """The Caller said nearly the same thing in each of its last `repeats` turns: nothing new will come."""
    said = [t.text.lower() for t in trace.turns if t.speaker == "caller"][-repeats:]
    return len(said) == repeats and all(SequenceMatcher(None, said[0], s).ratio() > 0.9 for s in said[1:])


async def _say(side: _Side, text: str) -> None:
    """The other side's words, as this side hears them, then its turn."""
    await side.send({"type": "conversation.item.create", "item": {
        "type": "message", "role": "user", "content": [{"type": "input_text", "text": text or "..."}]}})
    await side.send({"type": "response.create"})
