"""The Concierge's tools on the session runtime (A-04).

Each tool validates the model's arguments with its contract model (`contracts/tools.py`), resolves
whatever they name against the board in code (`refs.py`), sends one event to the runtime, and answers
with a short line built from the session as it now stands. The answer is spoken: at most 25 words, no
ids. The session and call come from `ToolContext`, which the bridge fills from the call it runs, never
from the arguments (hard rule 2). The event's idem_key is the Higgs tool-call id, so a repeat is a no-op.
"""
from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ValidationError

from landline.agents.concierge import refs
from landline.contracts import (
    AreaFocused,
    CallsRequested,
    FeedbackGiven,
    ListingFocused,
    PreferencesUpdated,
)
from landline.contracts.tools import (
    CONCIERGE_TOOLS,
    CallListings,
    FocusArea,
    FocusListing,
    GiveFeedback,
    PreferenceDelta,
    concierge_tools,
)
from landline.orchestrator import Refused, SessionRuntime, UnknownSession

log = logging.getLogger(__name__)

# remember_me / forget_me wait for profiles (O-05): until then there is nothing for them to change.
DEFERRED = {"remember_me", "forget_me"}
TOOLS: list[dict[str, Any]] = [t for t in concierge_tools() if t["name"] not in DEFERRED]

AREA_ZOOM = 14
TO_CALL = 3   # call_listings without refs: the top three
UNKNOWN = "Sorry, I can't do that yet."
NOT_CAUGHT = "Sorry, I didn't catch that. Could you say it again?"
WHICH = "Which one do you mean? You can say its street or its number on the list."
BROKEN = "Sorry, something went wrong on my side."


@dataclass
class ToolContext:
    session_id: str
    call_id: str
    runtime: SessionRuntime
    hang_up: bool = False   # set by end_session; the bridge hangs up once the goodbye has played
    # Phone calls only: texts the renter the link to their board, and says whether a text went out. The
    # first update_preferences of the call uses it, so the renter can watch the listings as they talk.
    text_link: Callable[[], bool] | None = None
    preferences_seen: bool = False


async def handle(name: str, arguments: str, tool_call_id: str, ctx: ToolContext) -> str:
    """Runs one tool call from Higgs and returns what the Concierge should hear back."""
    if name not in CONCIERGE_TOOLS or name in DEFERRED:
        log.warning("call %s: unknown tool %r", ctx.call_id, name)
        return UNKNOWN
    model, _ = CONCIERGE_TOOLS[name]
    try:
        args = model.model_validate_json(arguments or "{}")
    except ValidationError as exc:
        log.warning("call %s: bad arguments for %s: %s", ctx.call_id, name, exc.errors()[:2])
        return NOT_CAUGHT
    log.info("tool %s session=%s call=%s args=%s", name, ctx.session_id, ctx.call_id,
             args.model_dump_json(exclude_none=True, by_alias=True))
    meta = {"session_id": ctx.session_id, "at": datetime.now(UTC),
            "idem_key": f"{ctx.session_id}:{ctx.call_id}:{tool_call_id}"}
    try:
        return await HANDLERS[name](args, ctx, meta)
    except Refused as exc:
        return str(exc)
    except UnknownSession:
        log.exception("call %s: session %s isn't in the runtime", ctx.call_id, ctx.session_id)
        return BROKEN


async def update_preferences(args: PreferenceDelta, ctx: ToolContext, meta: dict) -> str:
    """The first one of a phone call also texts the renter their board link. Its result then says so
    (`texted_link`), and the Concierge tells them."""
    view = await ctx.runtime.emit(PreferencesUpdated(**meta, delta=args))
    first, ctx.preferences_seen = not ctx.preferences_seen, True
    if first and ctx.text_link is not None and ctx.text_link():
        return json.dumps({"result": view.agent_says, "texted_link": True})
    return view.agent_says


async def give_feedback(args: GiveFeedback, ctx: ToolContext, meta: dict) -> str:
    view = ctx.runtime.get(ctx.session_id)
    if args.listing_ref:
        card = refs.resolve(args.listing_ref, view.board)
        listing_id = card.listing_id if card else None
    else:
        listing_id = view.focus_listing_id
    if listing_id is None:
        return WHICH
    view = await ctx.runtime.emit(FeedbackGiven(**meta, listing_id=listing_id, reaction=args.reaction,
                                                reason=args.reason, attribute=args.attribute))
    return f"Noted for {_address(ctx, listing_id)}. {view.agent_says}"


async def focus_listing(args: FocusListing, ctx: ToolContext, meta: dict) -> str:
    card = refs.resolve(args.listing_ref, ctx.runtime.get(ctx.session_id).board)
    if card is None:
        return WHICH
    await ctx.runtime.emit(ListingFocused(**meta, listing_id=card.listing_id, origin="voice"))
    return f"{card.address}: {_beds(card.beds)}, {_money(card.monthly_cost)} a month."


async def focus_area(args: FocusArea, ctx: ToolContext, meta: dict) -> str:
    point = refs.place(args.place, ctx.runtime.catalogue)
    if point is None:
        return "I can't place that on the map yet."
    await ctx.runtime.emit(AreaFocused(**meta, label=args.place, lat=point[0], lng=point[1], zoom=AREA_ZOOM))
    return f"Showing {args.place}."


async def call_listings(args: CallListings, ctx: ToolContext, meta: dict) -> str:
    board = ctx.runtime.get(ctx.session_id).board
    if args.listing_refs:
        cards = [refs.resolve(ref, board) for ref in args.listing_refs]
        if any(c is None for c in cards):
            return WHICH
    else:
        cards = refs.top(board, TO_CALL)
    if not cards:
        return "There's nothing on the list to call yet."
    ids = list(dict.fromkeys(c.listing_id for c in cards))
    await ctx.runtime.emit(CallsRequested(**meta, listing_ids=ids, extra_questions=args.extra_questions or [],
                                          availability=args.availability or [], may_book=bool(args.may_book)))
    addresses = [_address(ctx, lid) for lid in ids]
    return f"I'll call {_join(addresses)} after you hang up, and text you what they say."


async def end_session(args: BaseModel, ctx: ToolContext, meta: dict) -> str:
    ctx.hang_up = True
    return "Goodbye."


HANDLERS: dict[str, Callable[[Any, ToolContext, dict], Awaitable[str]]] = {
    "update_preferences": update_preferences,
    "give_feedback": give_feedback,
    "focus_listing": focus_listing,
    "focus_area": focus_area,
    "call_listings": call_listings,
    "end_session": end_session,
}


def _address(ctx: ToolContext, listing_id: str) -> str:
    return ctx.runtime.catalogue.by_id[listing_id].address


def _beds(beds: float) -> str:
    if beds == 0:
        return "a studio"
    n = int(beds) if beds == int(beds) else beds
    return f"{n} bedroom" if n == 1 else f"{n} bedrooms"


def _money(amount: int) -> str:
    return f"${amount:,}"


def _join(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]
