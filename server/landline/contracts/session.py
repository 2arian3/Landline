"""What the web app renders: a session's ranked board and the catalogue points."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

CardStatus = Literal["pending", "calling", "verified", "suspect", "dead", "no_answer", "booked"]


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


class Preferences(BaseModel):
    max_monthly: int | None = None
    min_beds: float | None = None
    areas: list[str] = Field(default_factory=list)
    pets: list[str] = Field(default_factory=list)
    must_have: list[str] = Field(default_factory=list)
    priorities: list[str] = Field(default_factory=list)


class SessionView(BaseModel):
    session_id: str
    version: int
    user_name: str | None = None
    returning: bool = False
    preferences: Preferences = Field(default_factory=Preferences)
    focus_listing_id: str | None = None
    matches: list[str] = Field(default_factory=list)   # listing ids still matching the filters
    board: list[Card] = Field(default_factory=list)     # ranked; index 0 is the top pick
    agent_says: str = ""
