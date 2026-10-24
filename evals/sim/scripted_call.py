"""A scripted listing agent for listing calls: the other end of the call is lines you write instead of a Higgs
persona, so one known conversation goes through the real outbound path and you can see what the Caller does
with it. The Caller is the real one (its prompt, tools and the Auditor); only the landlord is mocked.
`evals/run_mock_call.py` runs these.

    {"id": "linda-scripted", "about": "...", "listing_id": "livrent:145662", "name": "Linda",
     "lines": ["Hello, Linda speaking.",
               {"when": ["available"], "say": "Yes, it's still available, from November 1st."},
               {"when": ["pet", "cat"], "say": "Cats are fine, but no dogs, sorry."},
               "Okay, thanks for calling. Bye! [hang up]"],
     "plan": {...as in a persona case, evals/sim/calls.py...},
     "gold": {...optional, as in a persona case...}}

Each landlord turn says the first unsaid line whose `when` words (any of them, any case) are in what the
Caller just said, and when none match, the next unsaid line in order. A plain string is a line with no
`when`, said only in its turn. A line with [hang up] in it ends the call after it. Once every line is said,
the landlord says goodbye and hangs up. Write amounts in digits, as in a persona.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from evals.sim.calls import CallCase, Gold, PersonaSpec, PlanSpec
from evals.sim.cases import FIXTURES
from landline.voice.higgs import ConnectHiggs
from landline.voice.personas import HANG_UP, Persona

MOCK_CALLS = FIXTURES / "mock_calls"
GOODBYE = f"Okay, that's everything from me. Thanks for calling, bye! {HANG_UP}"


class Line(BaseModel):
    model_config = ConfigDict(extra="forbid")

    say: str
    when: list[str] = Field(default_factory=list)


class MockCall(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    about: str = ""
    listing_id: str
    name: str = "Alex"
    lines: list[Line | str] = Field(min_length=1)
    plan: PlanSpec = Field(default_factory=PlanSpec)
    gold: Gold | None = None

    def script(self) -> list[Line]:
        return [Line(say=x) if isinstance(x, str) else x for x in self.lines]

    def persona(self) -> Persona:
        """What the sim runner needs: an id and a name. The script does the talking."""
        return Persona(id=self.id, name=self.name)

    def as_case(self) -> CallCase:
        """The persona-case shape the call scorer reads (evals/sim/score_calls.py). Needs `gold`."""
        if self.gold is None:
            raise ValueError(f"{self.id} has no gold to score against")
        return CallCase(id=self.id, about=self.about, listing_id=self.listing_id,
                        persona=PersonaSpec(name=self.name), plan=self.plan, gold=self.gold)


class ScriptedLandlord:
    """Stands in for the landlord's Higgs session in voice/sim.py: each `response.create` gets the next
    line of the script back as the text events a persona's session sends."""

    def __init__(self, script: list[Line]):
        self.left = list(script)
        self.heard = ""   # the Caller's last words, which pick the next line
        self._out: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    def next_line(self) -> str:
        if not self.left:
            return GOODBYE
        heard = self.heard.lower()
        line = next((x for x in self.left if any(w.lower() in heard for w in x.when)), self.left[0])
        self.left.remove(line)
        return line.say

    async def send(self, event: dict[str, Any]) -> None:
        kind = event.get("type")
        if kind == "conversation.item.create":
            content = (event.get("item") or {}).get("content") or []
            self.heard = " ".join(str(c.get("text") or "") for c in content)
        elif kind == "response.create":
            for e in ({"type": "response.created"},
                      {"type": "response.output_text.done", "text": self.next_line()},
                      {"type": "response.done", "response": {"output": [{"type": "message"}]}}):
                self._out.put_nowait(e)

    async def events(self):
        while True:
            yield await self._out.get()

    async def close(self) -> None:
        pass


def with_script(script: list[Line], connect: ConnectHiggs) -> ConnectHiggs:
    """The sim runner's `connect_higgs`: the Caller's session (the one with tools) goes to `connect`, the
    landlord's (no tools) to the script."""
    async def connect_(key: str, cfg: dict[str, Any]) -> Any:
        if cfg["session"].get("tools"):
            return await connect(key, cfg)
        return ScriptedLandlord(script)
    return connect_


def load_mock_calls() -> list[MockCall]:
    return [load_mock_call(p) for p in sorted(MOCK_CALLS.glob("*.json"))]


def load_mock_call(path: Path) -> MockCall:
    return MockCall.model_validate(json.loads(path.read_text(encoding="utf-8")))
