"""The hand-off between the two flows, and what each listing call returns.

The `CallPlan` is the only thing the calls read from the brief. Code builds it on
`SessionEnded`, stores it in the session snapshot, and never edits it afterwards.
There is at most one per session, so it has no id of its own.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from landline.contracts.session import EndReason, Fact


class Window(BaseModel):
    start: datetime            # Toronto time, computed in code from the renter's availability
    end: datetime


class Shareable(BaseModel):
    """What a Caller may tell a landlord: only the fields listed in user_profiles.shareable."""
    name: str | None = None
    move_in: str | None = None
    occupants: int | None = None
    pets: list[str] = Field(default_factory=list)


class CallPlan(BaseModel):
    model_config = ConfigDict(frozen=True)

    session_id: str
    user_id: int | None = None
    listing_ids: list[str] = Field(min_length=1, max_length=5)   # in the renter's order
    questions: list[str] = Field(default_factory=list)           # the renter's extras
    windows: list[Window] = Field(default_factory=list)
    may_book: bool = False
    shareable: Shareable = Field(default_factory=Shareable)
    language: str = "en"       # for the results
    created_at: datetime


class Conflict(BaseModel):
    """A call said something different from the listing or the City record."""
    field: str
    listed: str                # what the listing or the City record says
    heard: str                 # what the landlord said
    quote: str                 # the landlord's exact words
    against: Literal["listing", "city"] = "listing"


class Viewing(BaseModel):
    starts_at: datetime
    slot_text: str             # as the landlord said it


class CallOutcome(BaseModel):
    """One per listing call, from the Auditor."""
    call_id: str
    listing_id: str
    end_reason: EndReason
    facts: list[Fact] = Field(default_factory=list)
    conflicts: list[Conflict] = Field(default_factory=list)
    red_flags: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)   # landlord questions the brief couldn't answer
    viewing: Viewing | None = None
