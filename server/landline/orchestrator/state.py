"""What the runtime knows about one session: everything the board is built from.

It is stored whole in `sessions.snapshot` next to the `SessionView` it produced, so
a restart can reload it. Handlers change it only inside `session_tx`.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from landline.contracts import (
    Area,
    Availability,
    CallOutcome,
    CallPlan,
    CallView,
    CardStatus,
    Fact,
    Phase,
    Preferences,
    Viewing,
)
from landline.contracts.tools import Attribute, Reaction


class Said(BaseModel):
    """The renter's reaction to one listing, shown on its card ("You: too dark")."""
    reaction: Reaction
    reason: str | None = None
    attribute: Attribute | None = None


class Request(BaseModel):
    """What `call_listings` recorded during the brief. The Dispatcher freezes it into the CallPlan."""
    listing_ids: list[str]
    questions: list[str] = Field(default_factory=list)
    availability: list[Availability] = Field(default_factory=list)
    may_book: bool = False


class SessionState(BaseModel):
    session_id: str
    channel: str
    user_id: int | None = None
    caller_phone: str | None = None      # E.164 from Twilio; the results SMS goes here and nowhere else
    # A caller this number has called before (Profiles, O-05): what was recalled when the call started.
    returning: bool = False
    user_name: str | None = None
    previous_results: str | None = None  # the last session's results, which this call opens with
    recalled: dict[str, Said] = Field(default_factory=dict)   # reactions from earlier calls, by listing
    opening: str | None = None           # what the Concierge remembers, ≤ 120 words
    saved_search: str | None = None      # U-12d: the line for the saved search this call started from
    phase: Phase = "open"
    preferences: Preferences = Field(default_factory=Preferences)
    focus_listing_id: str | None = None
    focus_area: Area | None = None
    reactions: dict[str, Said] = Field(default_factory=dict)        # by listing
    request: Request | None = None
    plan: CallPlan | None = None
    calls: dict[str, CallView] = Field(default_factory=dict)        # by call, in the plan's order
    heard: dict[str, list[Fact]] = Field(default_factory=dict)      # facts recorded live, by listing
    questions: dict[str, list[str]] = Field(default_factory=dict)   # landlord questions, by listing
    viewings: dict[str, Viewing] = Field(default_factory=dict)      # booked, by listing
    outcomes: dict[str, CallOutcome] = Field(default_factory=dict)  # from the Auditor, by listing
    results: str | None = None
    board: list[str] = Field(default_factory=list)   # the last board's order, so ties keep their place
    ranks_at_hangup: dict[str, int] = Field(default_factory=dict)   # the board when the brief ended

    def listing_of(self, call_id: str) -> str | None:
        call = self.calls.get(call_id)
        return call.listing_id if call else None

    def status(self, listing_id: str) -> CardStatus:
        """A card's call status: what the outcome says once there is one, else what the call is doing."""
        if listing_id in self.viewings:
            return "booked"
        if (outcome := self.outcomes.get(listing_id)) is not None:
            return outcome_status(outcome)
        if any(c.listing_id == listing_id and c.state in ON_THE_LINE for c in self.calls.values()):
            return "calling"
        return "pending"

    def statuses(self) -> dict[str, CardStatus]:
        listed = {c.listing_id for c in self.calls.values()} | set(self.viewings) | set(self.outcomes)
        return {lid: st for lid in listed if (st := self.status(lid)) != "pending"}


ON_THE_LINE = ("dialing", "ringing", "live")
NOBODY = ("no_answer", "busy", "failed", "blocked", "voicemail", "wrong_number")
GONE = ("no", "false", "rented", "taken", "not available")


def outcome_status(outcome: CallOutcome) -> CardStatus:
    """docs/SCORING.md §4: a booking pins, any red flag is suspect, `available: no` is dead, and a call
    that reached nobody or learned nothing is no answer."""
    if outcome.viewing is not None:
        return "booked"
    if outcome.red_flags:
        return "suspect"
    if any(f.field == "available" and f.value.strip().lower() in GONE for f in outcome.facts):
        return "dead"
    if outcome.end_reason in NOBODY or not outcome.facts:
        return "no_answer"
    return "verified"
