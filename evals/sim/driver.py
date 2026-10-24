"""A simulated conversation with a real agent, in text (E-01; the scripted sim for A-04).

The agent is a Higgs Realtime session opened with its real instructions and tools, in text mode
(`output_modalities: ["text"]`): the other side's lines go in as `input_text` and the agent answers in
`response.output_text.done`. Its tool calls go through the bridge's own `run_tool`, so they run the same
handlers against the same session runtime (in memory) as a phone call. The other side is an Opponent: a
scripted renter for the Concierge now, a landlord persona for the Caller later.

A run returns a Trace: every turn, every tool call with the board it saw, the session state after each
line, why it ended, and every event both ways, so `replay.py` can play it back without Higgs.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from landline.agents.concierge import backstop, tools
from landline.contracts import ListingFocused, PreferencesUpdated, SessionStarted
from landline.contracts.tools import PreferenceDelta
from landline.db.catalogue import Catalogue
from landline.orchestrator import SessionRuntime
from landline.voice.bridge import ends_call, opening, run_tool
from landline.voice.higgs import ConnectHiggs, session_config
from landline.voice.line import LOOKING, describe

TURN_TIMEOUT_S = 30   # one agent turn, tools included
MAX_LINES = 14        # the other side's lines; a script is shorter, so this only stops a runaway


class Opponent(Protocol):
    async def reply(self, agent_said: str) -> str | None:
        """The other side's next line after the agent said `agent_said`. None hangs up."""


class ScriptedRenter:
    """Says the case's lines in order whatever the agent says, so a failure is the agent's."""

    def __init__(self, lines: list[str]) -> None:
        self.lines = list(lines)
        self.next = 0

    async def reply(self, agent_said: str) -> str | None:
        if self.next >= len(self.lines):
            return None
        self.next += 1
        return self.lines[self.next - 1]


@dataclass
class ToolCall:
    line: int            # the line it answered; -1 for the greeting
    name: str
    arguments: str
    output: str          # what the agent heard back
    board: list[str]     # the board's listing ids just before the tool ran


@dataclass
class AgentTurn:
    line: int
    text: str
    tools: list[str]     # tools called in this turn, in order
    ms: int              # from the line (or the greeting) to the end of the agent's turn


@dataclass
class Trace:
    case_id: str
    run: int
    session_id: str
    lines: list[str] = field(default_factory=list)          # what the other side said
    agent: list[AgentTurn] = field(default_factory=list)    # agent[0] is the greeting
    tools: list[ToolCall] = field(default_factory=list)
    boards: list[list[str]] = field(default_factory=list)   # the board before each line
    states: list[dict] = field(default_factory=list)        # the session state after each line
    final: dict = field(default_factory=dict)               # the session state at the end
    end: str = "error"   # end_session | script_done | max_lines | timeout | higgs_closed | error
    errors: list[str] = field(default_factory=list)         # Higgs `error` events
    events: list[dict] = field(default_factory=list)        # {"dir": "in" | "out", "event": ...}
    caught: list[dict] = field(default_factory=list)        # code's catch-ups (agents/concierge/backstop.py)

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, data: dict) -> Trace:
        return cls(**{**data, "agent": [AgentTurn(**a) for a in data.get("agent", [])],
                      "tools": [ToolCall(**t) for t in data.get("tools", [])]})


class HiggsClosed(Exception):
    """Higgs closed the socket mid-conversation (or a replay ran out of events)."""


class Recorded:
    """A Higgs session that keeps every event both ways, in the order the driver saw them."""

    def __init__(self, higgs: Any) -> None:
        self.higgs = higgs
        self.log: list[dict] = []

    async def send(self, event: dict[str, Any]) -> None:
        self.log.append({"dir": "out", "event": event})
        await self.higgs.send(event)

    async def events(self):
        async for event in self.higgs.events():
            self.log.append({"dir": "in", "event": event})
            yield event

    async def close(self) -> None:
        await self.higgs.close()


async def simulate(case_id: str, run: int, opponent: Opponent, *, catalogue: Catalogue,
                   connect_higgs: ConnectHiggs, api_key: str = "", max_lines: int = MAX_LINES,
                   turn_timeout_s: float = TURN_TIMEOUT_S, remembered: str | None = None,
                   prefs: dict[str, Any] | None = None, taps: dict[int, int] | None = None,
                   reader: Any | None = None) -> Trace:
    """One conversation between the Concierge and `opponent`, to its end. Never raises for what the
    agent or Higgs did: that goes in the trace. With `remembered` (what Profiles would say about this
    number) and `prefs` (the search it would restore), the caller is a returning one. `taps` maps a line
    to the rank of the pin the renter taps instead of speaking it. `reader` (a text model) turns on the
    bridge's catch-up for a line in another script answered with no tool; replays leave it off."""
    sid = f"s_sim_{case_id}_{run}"          # ids come from code, never from the model
    runtime = SessionRuntime(catalogue)
    await runtime.start(SessionStarted(session_id=sid, at=datetime.now(UTC), idem_key=f"{sid}:started",
                                       channel="judge"))
    if prefs:   # Profiles restores a returning caller's search before the call opens
        await runtime.emit(PreferencesUpdated(session_id=sid, at=datetime.now(UTC),
                                              idem_key=f"{sid}:remembered", delta=PreferenceDelta(**prefs)))
    trace = Trace(case_id=case_id, run=run, session_id=sid)
    ctx = tools.ToolContext(session_id=sid, call_id=f"c_sim_{case_id}_{run}", runtime=runtime)
    higgs: Recorded | None = None
    try:
        instructions, start = opening(remembered)   # the bridge's own: greeting or welcome back
        higgs = Recorded(await connect_higgs(api_key, session_config(
            instructions=instructions, tools=tools.TOOLS, modality="text")))
        stream = higgs.events().__aiter__()
        for event in start:
            await higgs.send(event)
        said = await _agent_turn(stream, higgs, ctx, trace, -1, turn_timeout_s)
        while not ctx.hang_up:
            if len(trace.lines) >= max_lines:
                trace.end = "max_lines"
                break
            line = await opponent.reply(said)
            if line is None:
                trace.end = "script_done"
                break
            trace.boards.append(_board(runtime, sid))
            if taps and len(trace.lines) in taps:   # a pin tap: the map focuses it, the agent gets a note
                card = runtime.get(sid).board[taps[len(trace.lines)] - 1]
                await runtime.emit(ListingFocused(session_id=sid, at=datetime.now(UTC), origin="ui",
                                                  idem_key=f"{sid}:tap{len(trace.lines)}",
                                                  listing_id=card.listing_id))
                line = f"{LOOKING} {describe(card)}"
            trace.lines.append(line)
            await higgs.send({"type": "conversation.item.create", "item": {
                "type": "message", "role": "user", "content": [{"type": "input_text", "text": line}]}})
            await higgs.send({"type": "response.create"})
            said = await _agent_turn(stream, higgs, ctx, trace, len(trace.lines) - 1, turn_timeout_s)
            if reader is not None and not trace.agent[-1].tools and not ctx.hang_up:   # as the bridge does
                caught = await backstop.catch_up(reader, line, ctx,
                                                 claimed=backstop.claims_save(trace.agent[-1].text))
                if caught is not None:
                    trace.caught.append({"line": len(trace.lines) - 1, "output": caught})
            if "call_listings" not in trace.agent[-1].tools and not ctx.hang_up:   # #127, as the bridge does
                caught = await backstop.catch_up_calls(line, ctx)
                if caught is not None:
                    trace.caught.append({"line": len(trace.lines) - 1, "output": caught})
            trace.states.append(runtime.state(sid).model_dump(mode="json"))
        if ctx.hang_up:
            trace.end = "end_session"
    except TimeoutError:
        trace.end = "timeout"
    except HiggsClosed:
        trace.end = "higgs_closed"
    except Exception as exc:   # a broken connect or driver bug: recorded, so one case can't sink a run
        trace.end = "error"
        trace.errors.append(f"{type(exc).__name__}: {exc}")
    finally:
        trace.final = runtime.state(sid).model_dump(mode="json")
        if higgs is not None:
            trace.events = higgs.log
            await higgs.close()
        await runtime.close()
    return trace


async def _agent_turn(stream, higgs: Recorded, ctx: tools.ToolContext, trace: Trace, line: int,
                      timeout_s: float) -> str:
    """Reads Higgs until the agent's turn is over: a `response.done` that asked for no tool, or the one
    that ends the call after `end_session`. Runs each tool call on the way, the way the bridge does."""
    started, texts, names = time.monotonic(), [], []
    spoke = False   # whether the response under way has said anything yet
    async with asyncio.timeout(timeout_s):
        while True:
            try:
                event = await anext(stream)
            except StopAsyncIteration:
                raise HiggsClosed() from None
            kind = event.get("type")
            if kind == "response.created":
                spoke = False
            elif kind == "response.output_text.delta":   # streams before a tool call in the same response;
                spoke = spoke or bool(str(event.get("delta") or "").strip())   # its .done comes after it
            elif kind == "response.output_text.done":
                texts.append(str(event.get("text") or ""))
                spoke = spoke or bool(texts[-1].strip())
            elif kind == "response.function_call_arguments.done":
                board = _board(ctx.runtime, ctx.session_id)
                await run_tool(higgs, event, ctx, spoke=spoke)
                names.append(str(event.get("name", "")))
                trace.tools.append(ToolCall(line=line, name=names[-1], board=board,
                                            arguments=str(event.get("arguments") or "{}"),
                                            output=_output_for(higgs, str(event.get("call_id", "")))))
            elif kind == "error":
                trace.errors.append(str(event.get("error")))
            elif kind == "response.done":
                output = (event.get("response") or {}).get("output") or []
                if ends_call(event, ctx) or (not ctx.hang_up
                                             and not any(o.get("type") == "function_call" for o in output)):
                    break
    text = " ".join(t.strip() for t in texts if t.strip())
    trace.agent.append(AgentTurn(line=line, text=text, tools=names,
                                 ms=round((time.monotonic() - started) * 1000)))
    return text


def _board(runtime: SessionRuntime, sid: str) -> list[str]:
    return [c.listing_id for c in runtime.get(sid).board]


def _output_for(higgs: Recorded, tool_call_id: str) -> str:
    """What `run_tool` just sent back for this tool call."""
    for entry in reversed(higgs.log):
        item = entry["event"].get("item") or {}
        if entry["dir"] == "out" and item.get("type") == "function_call_output" \
                and item.get("call_id") == tool_call_id:
            return str(item.get("output", ""))
    return ""
