"""Orchestrator input: everything that happens to a session, as one typed event.

Every event carries `type`, `session_id`, `at` and `idem_key`. Handlers are
idempotent on `idem_key`, so a duplicate webhook or a retry is a no-op. Ids, times
and amounts in an event come from code, never from a model. Parse any event with
`EVENT.validate_python(...)` or `EVENT.validate_json(...)`.
"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field, TypeAdapter

from landline.contracts.plan import CallOutcome, CallPlan
from landline.contracts.session import CallState, EndReason
from landline.contracts.tools import Attribute, Availability, FactField, PreferenceDelta, Reaction


class EventBase(BaseModel):
    session_id: str
    at: datetime
    idem_key: str


# Flow 1: the brief


class SessionStarted(EventBase):
    type: Literal["SessionStarted"] = "SessionStarted"
    channel: Literal["phone", "browser", "judge"]
    caller_phone: str | None = None    # E.164, from Twilio
    user_id: int | None = None


class PreferencesUpdated(EventBase):
    type: Literal["PreferencesUpdated"] = "PreferencesUpdated"
    delta: PreferenceDelta


class FeedbackGiven(EventBase):
    type: Literal["FeedbackGiven"] = "FeedbackGiven"
    listing_id: str
    reaction: Reaction
    reason: str | None = None
    attribute: Attribute | None = None


class ListingFocused(EventBase):
    type: Literal["ListingFocused"] = "ListingFocused"
    listing_id: str
    origin: Literal["ui", "voice"]


class AreaFocused(EventBase):
    type: Literal["AreaFocused"] = "AreaFocused"
    label: str
    lat: float
    lng: float
    zoom: float


class AnchorResolved(EventBase):
    type: Literal["AnchorResolved"] = "AnchorResolved"
    label: str
    lat: float
    lng: float


class CommuteResolved(EventBase):
    """TTC minutes from the top listings to an anchor are in the feature matrix's cache (D-10c): re-rank.
    Emitted in the background after AnchorResolved; it changes no session state, only the board built from it.
    The anchor is named as AnchorResolved named it."""
    type: Literal["CommuteResolved"] = "CommuteResolved"
    label: str
    lat: float
    lng: float


class CallsRequested(EventBase):
    """Records the plan; dials nothing. The listing ids are the refs code resolved against the board."""
    type: Literal["CallsRequested"] = "CallsRequested"
    listing_ids: list[str] = Field(min_length=1, max_length=5)
    extra_questions: list[str] = Field(default_factory=list)
    availability: list[Availability] = Field(default_factory=list)
    may_book: bool = False


class SessionEnded(EventBase):
    type: Literal["SessionEnded"] = "SessionEnded"
    reason: Literal["hangup", "end_session", "closed", "timeout"]


# Hand-off


class CallsScheduled(EventBase):
    type: Literal["CallsScheduled"] = "CallsScheduled"
    plan: CallPlan
    call_ids: list[str]


# Flow 2: the calls


class CallStateChanged(EventBase):
    type: Literal["CallStateChanged"] = "CallStateChanged"
    call_id: str
    state: CallState
    twilio_status: str | None = None


class FactRecorded(EventBase):
    type: Literal["FactRecorded"] = "FactRecorded"
    call_id: str
    field: FactField
    value: str
    quote: str
    turn_idx: int
    confirmed: bool = False            # read back to the landlord and agreed


class QuestionNoted(EventBase):
    type: Literal["QuestionNoted"] = "QuestionNoted"
    call_id: str
    question: str


class ViewingBooked(EventBase):
    type: Literal["ViewingBooked"] = "ViewingBooked"
    call_id: str
    listing_id: str
    starts_at: datetime
    slot_text: str


class CallEnded(EventBase):
    type: Literal["CallEnded"] = "CallEnded"
    call_id: str
    reason: EndReason


class OutcomeReady(EventBase):
    type: Literal["OutcomeReady"] = "OutcomeReady"
    outcome: CallOutcome


class CallsCompleted(EventBase):
    type: Literal["CallsCompleted"] = "CallsCompleted"
    call_ids: list[str]


Event = Annotated[
    SessionStarted | PreferencesUpdated | FeedbackGiven | ListingFocused | AreaFocused | AnchorResolved
    | CommuteResolved | CallsRequested | SessionEnded | CallsScheduled | CallStateChanged | FactRecorded
    | QuestionNoted | ViewingBooked | CallEnded | OutcomeReady | CallsCompleted,
    Field(discriminator="type"),
]
EVENT: TypeAdapter[Event] = TypeAdapter(Event)
