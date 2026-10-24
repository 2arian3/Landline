"""The fixture format for simulated listing calls (E-04): one landlord persona, the renter's plan for the
call, and what a good call should come back with.

    {"id": "linda-honest", "about": "...",
     "listing_id": "livrent:145662",
     "persona": {"name": "Linda", "manner": "...", "knows": {"parking": "..."},
                 "offers": ["Saturday at 11 am"],
                 "asks": ["Does your client have a guarantor?"], "voicemail": false},
     "plan": {"availability": [{"day": "saturday", "from": "10:00", "to": "14:00"}], "may_book": true,
              "questions": ["Is the building quiet at night?"],
              "shareable": {"move_in": "December 1", "occupants": 1, "pets": ["cat"]}},
     "gold": {"facts": {"available_from": ["november"], "parking_cost": ["150"]},
              "booked": "saturday 11:00", "note_question": true, "red_flags": false,
              "status": "booked", "end": ["done", "hangup"]}}

`gold.facts` maps a field to words that must all appear in the value the Caller recorded; only the fields
the call's checklist asks about count. `booked` is the day and 24-hour time a booking should land on, or
null when nothing should be booked. `{tomorrow_date}` (october 25) and `{tomorrow_day}` (sunday) in either
stand for the day after the run, in Toronto. `results_avoid`: words the renter's results text must not say.
Personas in `holdout/personas/` are never used to tune the prompt.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from evals.sim.cases import FIXTURES
from landline.contracts.tools import Availability
from landline.voice.personas import Persona

CALL_SETS = {"tune": FIXTURES / "personas", "holdout": FIXTURES / "holdout" / "personas"}


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class PersonaSpec(_Strict):
    name: str
    role: str = "listing agent"
    manner: str = "friendly and direct"
    knows: dict[str, str] = Field(default_factory=dict)
    offers: list[str] = Field(default_factory=list)
    asks: list[str] = Field(default_factory=list)
    voicemail: bool = False


class ShareSpec(_Strict):
    move_in: str | None = None
    occupants: int | None = None
    pets: list[str] = Field(default_factory=list)


class PlanSpec(_Strict):
    availability: list[Availability] = Field(default_factory=list)
    may_book: bool = False
    questions: list[str] = Field(default_factory=list)
    shareable: ShareSpec = Field(default_factory=ShareSpec)


class Gold(_Strict):
    facts: dict[str, list[str]] = Field(default_factory=dict)
    booked: str | None = None          # "saturday 11:00"
    note_question: bool = False
    red_flags: bool = False
    status: Literal["booked", "verified", "suspect", "dead", "no_answer"]
    end: list[str] = Field(default_factory=lambda: ["done", "hangup"])
    results_avoid: list[str] = Field(default_factory=list)   # words the renter's results text must not say


class CallCase(_Strict):
    id: str
    about: str
    listing_id: str
    persona: PersonaSpec
    plan: PlanSpec
    gold: Gold

    def as_persona(self) -> Persona:
        p = self.persona
        return Persona(id=self.id, name=p.name, role=p.role, manner=p.manner, knows=dict(p.knows),
                       offers=list(p.offers), asks=list(p.asks), voicemail=p.voicemail)


def load_calls(name: str) -> list[CallCase]:
    return [load_call(p) for p in sorted(CALL_SETS[name].glob("*.json"))]


def load_call(path: Path) -> CallCase:
    case = CallCase.model_validate(json.loads(path.read_text(encoding="utf-8")))
    if case.id != path.stem:
        raise ValueError(f"{path.name}: id {case.id!r} must match the file name")
    return case
