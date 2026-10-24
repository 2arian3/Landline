"""What the web app renders: a session's ranked board and the catalogue points."""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

if TYPE_CHECKING:   # plan.py imports this module; it resolves CallView.outcome once CallOutcome exists
    from landline.contracts.plan import CallOutcome

CardStatus = Literal["pending", "calling", "verified", "suspect", "dead", "no_answer", "booked"]
# sessions.status: open (the brief is live) → calling → done, or closed when the brief ends with no plan.
Phase = Literal["open", "calling", "done", "closed"]
# A listing call's lifecycle (docs/ARCHITECTURE.md, Call lifecycle). The renter's own call starts at live.
CallState = Literal["queued", "blocked", "dialing", "ringing", "live", "voicemail", "ended"]
# Why finalize() ended a call: end_call's reasons, then the voice layer's.
EndReason = Literal[
    "done", "refused", "wrong_number", "voicemail",
    "hangup", "no_answer", "busy", "failed", "blocked", "timeout",
]


class Fact(BaseModel):
    field: str
    value: str
    quote: str
    speaker: str               # "Agent heard" from a call, or the source name
    at: datetime | None = None


class Card(BaseModel):
    listing_id: str
    address: str
    unit_label: str | None = None
    neighbourhood: str | None = None
    lat: float | None = None
    lng: float | None = None
    rent: int
    monthly_cost: int
    beds: float
    baths: float | None = None
    sqft: int | None = None
    floor: int | None = None
    unit_type: str | None = None
    photo: str | None = None
    light_score: float | None = None   # 0–1, always shown as estimated
    source: str
    url: str

    rank: int
    status: CardStatus = "pending"
    reasons: list[str] = Field(default_factory=list)
    watch_out: str | None = None
    reaction: str | None = None
    facts: list[Fact] = Field(default_factory=list)
    red_flags: list[str] = Field(default_factory=list)


class CataloguePoint(BaseModel):
    listing_id: str
    lat: float
    lng: float
    monthly_cost: int
    beds: float


class Anchor(BaseModel):
    label: str                 # as the renter said it: "King and Bay"
    lat: float | None = None   # set by AnchorResolved once the geocoder answers
    lng: float | None = None


class Area(BaseModel):
    label: str
    lat: float
    lng: float
    zoom: float


class Preferences(BaseModel):
    max_monthly: int | None = None
    min_beds: float | None = None
    areas: list[str] = Field(default_factory=list)
    anchors: list[Anchor] = Field(default_factory=list)
    pets: list[str] = Field(default_factory=list)
    must_have: list[str] = Field(default_factory=list)
    priorities: list[str] = Field(default_factory=list)
    move_in: str | None = None     # the renter's words; code turns them into a date
    occupants: int | None = None
    language: str | None = None    # BCP 47, e.g. "fa"; the renter's language for replies and results


class CallView(BaseModel):
    call_id: str
    listing_id: str
    state: CallState
    started_at: datetime | None = None
    outcome: CallOutcome | None = None   # what this call settled (OutcomeReady), once it has ended


class Turn(BaseModel):
    """One caption line of a call (a call_turns row): who said what, in order. GET /api/calls/{id}/turns."""
    idx: int
    speaker: Literal["agent", "landlord", "renter"]
    text: str
    lang: str | None = None
    t_ms: int | None = None   # milliseconds into the call, when known


class SessionView(BaseModel):
    session_id: str
    version: int
    phase: Phase = "open"
    user_name: str | None = None
    returning: bool = False
    preferences: Preferences = Field(default_factory=Preferences)
    focus_listing_id: str | None = None
    focus_area: Area | None = None
    matches: list[str] = Field(default_factory=list)   # listing ids still matching the filters
    board: list[Card] = Field(default_factory=list)     # ranked; index 0 is the top pick
    agent_says: str = ""
    plan: list[str] = Field(default_factory=list)       # listing ids the renter picked to call, in order
    calls: list[CallView] = Field(default_factory=list)
    results: str | None = None   # the Narrator's results text, once the phase is done
