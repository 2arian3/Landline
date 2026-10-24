"""The Concierge's tools (docs/CONTRACTS.md, "Concierge tools").

The schemas are what Higgs sees. The handlers are stubs for now: each logs its call and returns "Noted.".
They take the session and call ids from `ToolContext`, which code fills from the call they run under,
never from the model's arguments (hard rule 2). A handler's return value is spoken by the Concierge: at
most 25 words, no ids.
"""
from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)

NOTED = "Noted."
UNKNOWN = "Sorry, I can't do that yet."
ATTRIBUTES = ["light", "location", "size", "cost", "parking"]


@dataclass(frozen=True)
class ToolContext:
    session_id: str
    call_id: str


def _tool(name: str, description: str, properties: dict[str, Any] | None = None,
          required: tuple[str, ...] = ()) -> dict[str, Any]:
    return {"type": "function", "name": name, "description": description,
            "parameters": {"type": "object", "properties": properties or {}, "required": list(required)}}


def _of(kind: str, description: str, **extra: Any) -> dict[str, Any]:
    return {"type": kind, "description": description, **extra}


def _strings(description: str) -> dict[str, Any]:
    return _of("array", description, items={"type": "string"})


LISTING_REF = _of("string", "Address words or the listing's number on the list")
VIEWING_TIME = {"type": "object", "required": ["day", "from", "to"],
                "properties": {"day": _of("string", "e.g. saturday"),
                               "from": _of("string", "24-hour HH:MM"),
                               "to": _of("string", "24-hour HH:MM")}}
ANCHOR = {"type": "object", "required": ["label"], "properties": {"label": {"type": "string"}}}

TOOLS: list[dict[str, Any]] = [
    _tool("update_preferences",
          "Record what the renter wants. Call it every time something changes, "
          "with only the fields that changed.",
          {"max_monthly": _of("integer", "Most they will pay per month, in dollars"),
           "min_beds": _of("number", "Fewest bedrooms; 0 for a studio"),
           "areas": _strings("Neighbourhoods or areas they want"),
           "anchors": _of("array", "Places to live near, e.g. work or a station", items=ANCHOR),
           "pets": _strings("Pets they have, e.g. dog, cat"),
           "must_have": _strings("Things they won't go without, e.g. parking, laundry"),
           "priorities": _strings("What matters most, most important first"),
           "move_in": _of("string", "When they want to move in, in their words"),
           "occupants": _of("integer", "How many people will live there"),
           "language": _of("string", "The language the renter is speaking")}),
    _tool("give_feedback",
          "Record the renter's reaction to a listing. "
          "Without listing_ref it applies to the one they're looking at.",
          {"listing_ref": LISTING_REF,
           "reaction": {"type": "string", "enum": ["like", "dislike", "reject"]},
           "reason": _of("string", "Their reason, in their words"),
           "attribute": _of("string", "What the reason is about, e.g. 'too dark' is light", enum=ATTRIBUTES)},
          required=("reaction",)),
    _tool("focus_listing", "Show one listing on the renter's map.",
          {"listing_ref": LISTING_REF}, required=("listing_ref",)),
    _tool("focus_area", "Move the renter's map to a part of the city.",
          {"place": _of("string", "A neighbourhood, street or landmark")}, required=("place",)),
    _tool("call_listings",
          "Plan calls to listing agents. Nothing is dialled until the renter hangs up. At most 5 listings.",
          {"listing_refs": _strings("Address words or list numbers; leave out for the top 3"),
           "extra_questions": _strings("Anything else the renter wants asked"),
           "availability": _of("array", "When the renter can go to a viewing", items=VIEWING_TIME),
           "may_book": _of("boolean", "True if the renter said you may book a viewing")}),
    _tool("remember_me", "The renter agreed that Landline may remember their preferences for next time."),
    _tool("forget_me", "The renter asked Landline to forget them and delete what it knows."),
    _tool("end_session", "Hang up. Call it right after your goodbye."),
]


# One stub per tool, each filled in by its own task (A-04 and the system services in docs/ARCHITECTURE.md).

async def update_preferences(args: dict[str, Any], ctx: ToolContext) -> str:
    """Will emit PreferencesUpdated; the Matcher re-ranks and the Narrator writes the reply."""
    return NOTED


async def give_feedback(args: dict[str, Any], ctx: ToolContext) -> str:
    """Will emit FeedbackGiven for the focused or named listing."""
    return NOTED


async def focus_listing(args: dict[str, Any], ctx: ToolContext) -> str:
    """Will resolve the ref against the board and emit ListingFocused (origin voice)."""
    return NOTED


async def focus_area(args: dict[str, Any], ctx: ToolContext) -> str:
    """Will geocode the place in the background and emit AreaFocused."""
    return NOTED


async def call_listings(args: dict[str, Any], ctx: ToolContext) -> str:
    """Will emit CallsRequested and read the addresses back. Dials nothing."""
    return NOTED


async def remember_me(args: dict[str, Any], ctx: ToolContext) -> str:
    """Will set consent_memory on the renter's profile."""
    return NOTED


async def forget_me(args: dict[str, Any], ctx: ToolContext) -> str:
    """Will delete the renter's profile and interactions."""
    return NOTED


async def end_session(args: dict[str, Any], ctx: ToolContext) -> str:
    """Will hang up once the goodbye has played (a Twilio mark), which emits SessionEnded."""
    return NOTED


HANDLERS: dict[str, Callable[[dict[str, Any], ToolContext], Awaitable[str]]] = {
    "update_preferences": update_preferences,
    "give_feedback": give_feedback,
    "focus_listing": focus_listing,
    "focus_area": focus_area,
    "call_listings": call_listings,
    "remember_me": remember_me,
    "forget_me": forget_me,
    "end_session": end_session,
}


async def handle(name: str, args: dict[str, Any], ctx: ToolContext) -> str:
    """Runs one tool call from Higgs and returns what the Concierge should hear back."""
    handler = HANDLERS.get(name)
    log.info("tool %s session=%s call=%s args=%s", name, ctx.session_id, ctx.call_id,
             json.dumps(args, ensure_ascii=False))
    if handler is None:
        return UNKNOWN
    return await handler(args, ctx)
