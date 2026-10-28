"""Typed rows the repositories return, one per table (db/migrations/).

Field names match the columns, so psycopg's `class_row` builds them directly.
Geography columns come back as `lat` and `lng`.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from landline.contracts import EVENT, Event


@dataclass(frozen=True)
class SessionRow:
    session_id: str
    user_id: int | None
    channel: str
    status: str                 # open → calling → done, or closed
    version: int
    snapshot: dict[str, Any]
    started_at: datetime
    ended_at: datetime | None


@dataclass(frozen=True)
class EventRow:
    seq: int
    session_id: str | None
    call_id: str | None
    type: str
    idem_key: str | None
    payload: dict[str, Any]
    at: datetime

    def event(self) -> Event:
        """The contract event this row stores."""
        return EVENT.validate_python(self.payload)


@dataclass(frozen=True)
class CallRow:
    call_id: str
    session_id: str | None
    listing_id: str | None
    role: str                   # renter | listing
    transport: str              # voice | sim | browser | stub
    twilio_sid: str | None
    state: str
    end_reason: str | None
    created_at: datetime
    answered_at: datetime | None
    ended_at: datetime | None
    deadline_at: datetime | None
    attempt: int
    to_e164: str | None = None  # 0004: the number a listing call dialled (A-13, O-08b)


@dataclass(frozen=True)
class TurnRow:
    call_id: str
    idx: int
    speaker: str                # renter | landlord | agent
    text: str
    lang: str | None
    t_ms: int | None
    audio_start_ms: int | None = None   # where the line is in the call's audio (call_audio), when known
    audio_end_ms: int | None = None


@dataclass(frozen=True)
class ViewingConfirmationRow:
    """A booked viewing waiting for the listing agent to confirm it (viewing_confirmations, 0004, O-08)."""
    token: str
    call_id: str
    session_id: str
    listing_id: str
    starts_at: datetime
    status: str                 # pending | confirmed | declined
    agent_email: str | None
    calendar_event_id: str | None
    created_at: datetime
    decided_at: datetime | None


@dataclass(frozen=True)
class AvatarRow:
    """One call's Higgs Avatar clip (call_avatar), without the MP4 bytes."""
    call_id: str
    status: str                 # queued | rendering | ready | failed | skipped
    boson_video_id: str | None
    error: str | None
    updated_at: datetime


@dataclass(frozen=True)
class AvatarFile:
    """A clip's status and its MP4, for the player."""
    status: str
    video: bytes | None


@dataclass(frozen=True)
class AvatarClip:
    """A clip as the board lists it: which call, and whether the MP4 is ready. No bytes."""
    call_id: str
    role: str                   # renter | listing
    listing_id: str | None
    status: str


@dataclass(frozen=True)
class AudioRow:
    """A chunk of one side of a call's audio (call_audio)."""
    call_id: str
    track: str                  # renter | landlord | agent
    seq: int
    start_ms: int               # on the call's clock, as call_turns.audio_start_ms
    format: str                 # pcmu | pcm16
    rate: int                   # samples a second
    audio: bytes


@dataclass(frozen=True)
class FactRow:
    fact_id: int
    call_id: str | None
    listing_id: str | None
    field: str
    value: Any
    quote: str
    turn_idx: int | None
    confirmed: bool
    source: str                 # tool | transcript | both
    created_at: datetime


@dataclass(frozen=True)
class BoardEntry:
    """One card's place on a session's board (session_listings)."""
    listing_id: str
    rank: int | None = None
    score: float | None = None
    contributions: dict[str, float] | None = None
    status: str = "pending"
    reaction: str | None = None
    reaction_reason: str | None = None


@dataclass(frozen=True)
class BoardRow:
    session_id: str
    listing_id: str
    rank: int | None
    score: float | None
    contributions: dict[str, float] | None
    status: str
    reaction: str | None
    reaction_reason: str | None
    updated_at: datetime


@dataclass(frozen=True)
class UserRow:
    user_id: int
    phone_e164: str
    name: str | None
    language: str | None
    consent_memory: bool
    created_at: datetime
    last_seen_at: datetime | None
    email: str | None = None           # 0004: U-12, O-08 (invites go to it)
    use_saved_search: bool = False     # 0004: U-12d
    push_token: str | None = None      # 0004: U-13c


@dataclass(frozen=True)
class ProfileRow:
    user_id: int
    hard: dict[str, Any]        # {max_monthly, min_beds, pets[], must_have[]}
    priorities: list[Any]       # [{feature, weight}], most recent first
    anchors: list[Any]          # [{label, lat, lng}]
    move_in: date | None
    occupants: int | None
    shareable: list[str]
    memory_summary: str | None
    updated_at: datetime


@dataclass(frozen=True)
class InteractionRow:
    user_id: int
    listing_id: str
    session_id: str | None
    action: str                 # viewed | liked | rejected | called | booked
    reason: str | None
    attribute: str | None
    at: datetime


@dataclass(frozen=True)
class IngestRun:
    run_id: int
    source: str
    started_at: datetime
    finished_at: datetime | None
    status: str
    pages: int
    fetched: int
    inserted: int
    updated: int
    marked_stale: int
    error: str | None


@dataclass(frozen=True)
class Neighbourhood:
    id: int
    name: str
    adjacent: list[int]
    median_rent: dict[str, int]   # beds → CAD, e.g. {"1": 2350}


@dataclass(frozen=True)
class Station:
    id: str
    name: str
    line: str | None
    lat: float
    lng: float
    distance_m: float | None = None   # set by nearest()


@dataclass(frozen=True)
class Building:
    building_id: int
    rsn: int | None
    address_norm: str | None
    lat: float | None
    lng: float | None
    year_built: int | None
    storeys: int | None
    units: int | None
    pets_allowed: str | None
    pet_restrictions: str | None
    ac_type: str | None
    laundry: str | None
    parking_type: str | None
    visitor_parking: str | None
    locker: bool | None
    non_smoking: bool | None
    separate_hydro: bool | None
    elevators: int | None
    barrier_free_entrance: bool | None
    mgmt_company: str | None
    eval_score: float | None
    eval_date: date | None
    eval_detail: dict[str, Any]
    distance_m: float | None = None   # set by nearest()
