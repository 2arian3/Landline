"""The function tools the voice agents call: argument models and the Higgs tool specs.

Arguments carry words and choices, never ids, phone numbers, times or totals. Code
resolves `listing_ref` against the board, turns days and spoken slots into Toronto
datetimes, and takes the session and listing from the running Call. A tool's reply
is spoken aloud (at most 25 words, no ids), so it is a plain string, not a model.
"""
from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Reaction = Literal["like", "dislike", "reject"]
Attribute = Literal["light", "location", "size", "cost", "parking"]
Day = Literal[
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "today", "tomorrow",
]
# What a Caller may record: the call-sourced features from docs/DATA.md, plus `available` (still on the
# market, which sets the card to verified or dead) and `other` (the answer to one of the renter's extras).
FactField = Literal[
    "available", "available_from", "rent", "fees", "parking", "parking_cost", "pets", "laundry", "ac",
    "utilities_included", "furnished", "lease_months", "sqft", "floor", "light", "elevator", "step_free",
    "outdoor_space", "other",
]
Clock = Annotated[str, Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$", description="24-hour time, e.g. 13:00")]


class AnchorRef(BaseModel):
    label: str = Field(description="A place as the renter said it, e.g. King and Bay")


class Availability(BaseModel):
    """When the renter can view, as the Concierge heard it. Code makes Toronto datetimes of it."""
    model_config = ConfigDict(populate_by_name=True)

    day: Day
    from_: Clock = Field(alias="from")
    to: Clock


# Concierge


class PreferenceDelta(BaseModel):
    """update_preferences, and the PreferencesUpdated payload. Only what changed; None means unchanged."""
    max_monthly: int | None = Field(None, ge=0, description="Most the renter will pay per month, in CAD")
    min_beds: float | None = Field(None, ge=0, description="0 means a studio is fine")
    areas: list[str] | None = Field(None, description="Neighbourhoods or areas the renter named")
    anchors: list[AnchorRef] | None = Field(None, description="Places the renter wants to be near")
    pets: list[str] | None = Field(None, description="Pets the renter has, e.g. dog")
    must_have: list[str] | None = None
    priorities: list[str] | None = Field(None, description="What matters most, in the renter's order")
    move_in: str | None = Field(None, description="When the renter wants to move in, in their words")
    occupants: int | None = Field(None, ge=1)
    language: str | None = Field(None, description="BCP 47 code of the language the renter speaks, e.g. fa")


class GiveFeedback(BaseModel):
    listing_ref: str | None = Field(
        None, description="Address words or rank number; defaults to the focused listing",
    )
    reaction: Reaction
    reason: str | None = Field(None, description="The renter's reason, in their words")
    attribute: Attribute | None = Field(None, description="What the reason is about, e.g. too dark is light")


class FocusListing(BaseModel):
    listing_ref: str = Field(description="Address words or rank number")


class FocusArea(BaseModel):
    place: str = Field(description="A neighbourhood, intersection or landmark")


class AskMaps(BaseModel):
    """A question about the area around one listing, answered from Google Maps (O-07b). Words only: code
    resolves the listing and its coordinates, and the answer's names and minutes come from Google."""
    kind: Literal["nearby", "walk", "drive", "transit", "weather"] = Field(
        description="nearby: the closest place of a kind; walk, drive or transit: the time to get somewhere; "
                    "weather: the forecast for a day")
    what: str | None = Field(None, description="For nearby: what to find, in the renter's words, like "
                                                "'a grocery store', 'Shoppers' or 'a gym'")
    to: str | None = Field(None, description="For walk, drive or transit: where to, in the renter's words")
    listing_ref: str | None = Field(None, description="Which listing: its number on the list or its street; "
                                                       "leave it out for the one in focus")
    day: str | None = Field(None, description="For weather: the day in the renter's words, like 'Saturday'")


class CallListings(BaseModel):
    listing_refs: list[str] | None = Field(
        None, max_length=5, description="Address words or rank numbers, at most 5; defaults to the top 3",
    )
    extra_questions: list[str] | None = Field(
        None, description="Questions the renter wants asked on every call",
    )
    availability: list[Availability] | None = Field(None, description="When the renter can view")
    may_book: bool | None = Field(None, description="True if the renter allows booking a viewing")


class RememberMe(BaseModel):
    pass


class ForgetMe(BaseModel):
    pass


class EndSession(BaseModel):
    pass


# Caller


class RecordFact(BaseModel):
    field: FactField
    value: str = Field(description="What the landlord said, in short words")
    quote: str = Field(description="The landlord's exact words")


class AvailableFrom(BaseModel):
    """When the place is free, as the landlord said it, in parts (#157). Code makes the calendar date from
    the call's day in Toronto; the model never gives a date."""
    kind: Literal["now", "tomorrow", "weekday", "next_week", "in_weeks", "date", "month", "end_of_month",
                  "unknown"] = Field(description="What kind of day they named")
    weekday: Literal["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"] | None = \
        Field(None, description="kind=weekday: the day they named")
    weeks: int | None = Field(None, ge=1, le=52, description="kind=in_weeks: how many weeks")
    month: int | None = Field(None, ge=1, le=12, description="kind=date or month: the month they named, 1-12")
    day: int | None = Field(None, ge=1, le=31, description="kind=date: the day of the month they named")
    quote: str = Field(description="The landlord's exact words")


class NoteQuestion(BaseModel):
    question: str


class BookViewing(BaseModel):
    slot: str = Field(description="The time the landlord offered, in spoken words, e.g. Saturday at two")


class EndCall(BaseModel):
    reason: Literal["done", "refused", "wrong_number", "voicemail"]


# name → (arguments, description for the model)
CONCIERGE_TOOLS: dict[str, tuple[type[BaseModel], str]] = {
    "update_preferences": (
        PreferenceDelta, "Call it every time the renter's needs change. Send only what changed.",
    ),
    "give_feedback": (
        GiveFeedback,
        "The renter reacted to a listing. Map their reason to its attribute (too dark is light).",
    ),
    "focus_listing": (FocusListing, "Show one listing on the map, by address words or rank number."),
    "focus_area": (FocusArea, "Fly the map to a place."),
    "ask_maps": (
        AskMaps,
        "Answer a question about the area around a listing from Google Maps: the closest place of a kind, "
        "the time to walk, drive or take transit somewhere, or the weather on a day. Say only what the reply "
        "says.",
    ),
    "call_listings": (
        CallListings,
        "Record which listings to call after the renter hangs up, their extra questions and when they can "
        "view. Nothing dials until the session ends. Read the addresses back from the reply.",
    ),
    "remember_me": (RememberMe, "The renter agrees to be remembered by phone number next time."),
    "forget_me": (ForgetMe, "Delete everything remembered about the renter."),
    "end_session": (EndSession, "End the session after you have said goodbye."),
}

CALLER_TOOLS: dict[str, tuple[type[BaseModel], str]] = {
    "record_fact": (
        RecordFact, "Record something the landlord said as soon as they say it, with their exact words.",
    ),
    "record_available_from": (
        AvailableFrom,
        "The landlord said when it's free. Fill in the parts of what they said, never a date you work out; "
        "code makes the date. Read back the day the reply gives.",
    ),
    "note_question": (
        NoteQuestion,
        "The landlord asked something you can't answer. Say you'll check with your client, then call this.",
    ),
    "book_viewing": (
        BookViewing, "Book a viewing at a time the landlord offered. The reply says if it's booked.",
    ),
    "end_call": (EndCall, "End the call. done is accepted only once every checklist item is covered."),
}


def concierge_tools() -> list[dict[str, Any]]:
    """The Concierge's `tools` for session.update."""
    return [tool_spec(name, args, text) for name, (args, text) in CONCIERGE_TOOLS.items()]


def caller_tools() -> list[dict[str, Any]]:
    """The Caller's `tools` for session.update."""
    return [tool_spec(name, args, text) for name, (args, text) in CALLER_TOOLS.items()]


def tool_spec(name: str, args: type[BaseModel], description: str) -> dict[str, Any]:
    """A Higgs function tool: the model's JSON Schema with refs inlined, titles dropped, and an
    optional field shown as its plain type (leaving it out of `required` already makes it optional)."""
    schema = args.model_json_schema()
    defs = schema.pop("$defs", {})
    params = _simplify(schema, defs)
    params.pop("description", None)
    params["type"] = "object"
    params.setdefault("properties", {})
    return {"type": "function", "name": name, "description": description, "parameters": params}


def _simplify(node: Any, defs: dict[str, Any]) -> Any:
    if isinstance(node, list):
        return [_simplify(x, defs) for x in node]
    if not isinstance(node, dict):
        return node
    if "$ref" in node:
        target = _simplify(defs[node["$ref"].rsplit("/", 1)[-1]], defs)
        target.pop("description", None)
        return {**target, **_simplify({k: v for k, v in node.items() if k != "$ref"}, defs)}
    out = {k: _simplify(v, defs) for k, v in node.items() if k != "title"}
    options = out.get("anyOf")
    if options and any(o == {"type": "null"} for o in options):
        rest = [o for o in options if o != {"type": "null"}]
        out.pop("anyOf")
        out = {**rest[0], **out} if len(rest) == 1 else {**out, "anyOf": rest}
    if out.get("default", 0) is None:
        out.pop("default")
    return out
