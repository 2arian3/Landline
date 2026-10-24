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
from functools import cache
from typing import Any

from pydantic import BaseModel, ValidationError

from landline.agents.concierge import refs
from landline.contracts import (
    AnchorResolved,
    AreaFocused,
    CallsRequested,
    Card,
    FeedbackGiven,
    ListingFocused,
    PreferencesUpdated,
)
from landline.contracts.tools import (
    CONCIERGE_TOOLS,
    AnchorRef,
    Availability,
    CallListings,
    FocusArea,
    FocusListing,
    GiveFeedback,
    PreferenceDelta,
    concierge_tools,
)
from landline.orchestrator import Refused, SessionRuntime, UnknownSession
from landline.orchestrator.places import load_stations

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
# A place no data holds: the agent asks for one it can find, instead of saying "got it" to nothing.
ASK_PLACE = "Nothing changed. Ask the renter for a nearby main street, intersection or subway station."
# give_feedback with no listing_ref and nothing focused: the renter usually did say which ("the second
# one"), and the agent left it out. Asking them again would annoy them; the agent can fix it itself.
NO_REF = "Not saved: no listing_ref. Call give_feedback again now with listing_ref set to the number or " \
         "street the renter said; ask them only if they named none."
# end_session's answer when it came before any word: the bridge asks for one more response to say it in.
SAY_GOODBYE = "You haven't said goodbye yet. Say a short goodbye now, in the renter's language; the line " \
              "closes after it."


@dataclass
class ToolContext:
    session_id: str
    call_id: str
    runtime: SessionRuntime
    hang_up: bool = False   # set by end_session; the bridge hangs up once the goodbye has played
    # The response that called end_session without a word. A goodbye was asked for after it, and the call
    # ends when a later response is done.
    bye_response: str | None = None
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
    raw = _object(arguments)
    if raw is None:
        log.warning("call %s: arguments for %s aren't a JSON object: %r", ctx.call_id, name, arguments[:200])
        return NOT_CAUGHT
    raw = _refs_as_words(raw)
    misplaced = [key for key in raw if key not in _fields(model)]
    if misplaced:   # dropping them quietly let the agent think it had saved them
        log.warning("call %s: %s doesn't take %s", ctx.call_id, name, misplaced)
        raw = {k: v for k, v in raw.items() if k not in misplaced}
    try:
        args = model.model_validate(raw)
    except ValidationError as exc:
        log.warning("call %s: bad arguments for %s: %s", ctx.call_id, name, exc.errors()[:2])
        return NOT_CAUGHT
    log.info("tool %s session=%s call=%s args=%s", name, ctx.session_id, ctx.call_id,
             args.model_dump_json(exclude_none=True, by_alias=True))
    hint = _misplaced_hint(name, misplaced)
    if misplaced and _fields(model) and not args.model_dump(exclude_none=True):
        return hint   # nothing left to do: only say where those belong
    meta = {"session_id": ctx.session_id, "at": datetime.now(UTC),
            "idem_key": f"{ctx.session_id}:{ctx.call_id}:{tool_call_id}"}
    try:
        reply = await HANDLERS[name](args, ctx, meta)
    except Refused as exc:
        return str(exc)
    except UnknownSession:
        log.exception("call %s: session %s isn't in the runtime", ctx.call_id, ctx.session_id)
        return BROKEN
    return f"{reply} {hint}" if hint else reply


def _object(arguments: str) -> dict[str, Any] | None:
    try:
        value = json.loads(arguments or "{}")
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def _refs_as_words(raw: dict[str, Any]) -> dict[str, Any]:
    """Rank numbers often come back as JSON numbers (`"listing_refs": [1, 2]`). They mean the same as "1"
    and "2", so they become words before validation instead of failing it."""
    def word(ref: Any) -> Any:
        if isinstance(ref, int | float) and not isinstance(ref, bool):
            return str(int(ref)) if ref == int(ref) else str(ref)
        return ref
    out = dict(raw)
    if "listing_ref" in out:
        out["listing_ref"] = word(out["listing_ref"])
    if isinstance(out.get("listing_refs"), list):
        out["listing_refs"] = [word(r) for r in out["listing_refs"]]
    return out


def _fields(model: type[BaseModel]) -> set[str]:
    return {name for key, f in model.model_fields.items() for name in (key, f.alias) if name}


def _misplaced_hint(name: str, keys: list[str]) -> str:
    """What the agent hears about arguments its tool doesn't take: nothing was saved, and where they go."""
    if not keys:
        return ""
    owner = next((other for other, (m, _) in CONCIERGE_TOOLS.items()
                  if other != name and other not in DEFERRED and set(keys) <= _fields(m)), None)
    where = f"call {owner} with them" if owner else f"{name} doesn't take them"
    return f"Not saved: {', '.join(keys)}; {where}."


async def update_preferences(args: PreferenceDelta, ctx: ToolContext, meta: dict) -> str:
    """The places the renter named are found in code first (`_places`), and their points go in the same
    commit, so the board has moved by the time the agent hears the reply. The first one of a phone call
    also texts the renter their board link. Its result then says so (`texted_link`), and the Concierge
    tells them."""
    args, points, lost = _places(args, ctx)
    sid = ctx.session_id
    version = ctx.runtime.get(sid).version
    async with ctx.runtime.session_tx(sid) as tx:
        await tx.apply(PreferencesUpdated(**meta, delta=args))
        for label, spot in points:
            key = f"{meta['idem_key']}:place:{' '.join(label.lower().split())}"
            await tx.apply(AnchorResolved(session_id=sid, at=meta["at"], idem_key=key, label=label,
                                          lat=spot.lat, lng=spot.lng))
    view = ctx.runtime.get(sid)
    # With nothing changed, agent_says is still the last commit's line; only the missing place is news.
    said = " ".join(filter(None, [view.agent_says if view.version != version or not lost else None,
                                  _not_found(lost, ctx)]))
    first, ctx.preferences_seen = not ctx.preferences_seen, True
    if first and ctx.text_link is not None and ctx.text_link():
        return json.dumps({"result": said, "texted_link": True})
    return said


def _places(args: PreferenceDelta, ctx: ToolContext) -> tuple[PreferenceDelta, list[tuple[str, refs.Spot]],
                                                                list[str]]:
    """Finds each area and anchor the renter named (`refs.area`). A City neighbourhood stays an area, under
    the City's name, which is what the ranker matches. Anything else found (a street, a station, a
    building) becomes an anchor with its point. An area left as said and never matched would do nothing
    while the agent said "got it" ("around Queens"). A place nothing holds is kept for the geocoder when
    there is one; without one it's dropped, and the reply asks the renter for a street or station."""
    if not args.areas and not args.anchors:
        return args, [], []
    runtime = ctx.runtime
    known = runtime.state(ctx.session_id).preferences.anchors
    placed = {a.label.lower() for a in known if a.lat is not None}
    geocoder = bool(runtime.places.contact)
    areas: list[str] = []
    anchors: list[str] = [] if args.anchors is not None else [a.label for a in known]
    points: list[tuple[str, refs.Spot]] = []
    lost: list[str] = []
    named = [(a, True) for a in args.areas or []] + [(a.label, False) for a in args.anchors or []]
    for label, is_area in named:
        if label.lower() in placed:
            anchors.append(label)
            continue
        spot = refs.area(label, runtime.matrix.named, _stations(), runtime.catalogue)
        if spot is not None and spot.hoods and is_area:
            areas += spot.hoods
        elif spot is not None:
            anchors.append(label)
            points.append((label, spot))
        else:
            lost.append(label)
            if geocoder:
                anchors.append(label)
    update: dict[str, Any] = {}
    if args.areas is not None:
        update["areas"] = list(dict.fromkeys(areas))
    if args.anchors is not None or anchors != [a.label for a in known]:
        update["anchors"] = [AnchorRef(label=x) for x in dict.fromkeys(anchors)]
    return args.model_copy(update=update), points, lost


def _not_found(lost: list[str], ctx: ToolContext) -> str | None:
    if not lost:
        return None
    if ctx.runtime.places.contact:
        return f"Looking up {_join(lost)} on the map; the list moves if it's found."
    return f"Couldn't find {_join(lost)} on the map. {ASK_PLACE}"


@cache
def _stations() -> list[dict]:
    return load_stations()


async def give_feedback(args: GiveFeedback, ctx: ToolContext, meta: dict) -> str:
    view = ctx.runtime.get(ctx.session_id)
    if args.listing_ref:
        found = refs.match(args.listing_ref, view.board, view.focus_listing_id)
        if found.card is None:
            return _which(found)
        listing_id = found.card.listing_id
    else:
        listing_id = view.focus_listing_id
    if listing_id is None:
        return NO_REF
    view = await ctx.runtime.emit(FeedbackGiven(**meta, listing_id=listing_id, reaction=args.reaction,
                                                reason=args.reason, attribute=args.attribute))
    return f"Noted for {_address(ctx, listing_id)}. {view.agent_says}"


async def focus_listing(args: FocusListing, ctx: ToolContext, meta: dict) -> str:
    view = ctx.runtime.get(ctx.session_id)
    found = refs.match(args.listing_ref, view.board, view.focus_listing_id)
    card = found.card
    if card is None:
        return _which(found)
    await ctx.runtime.emit(ListingFocused(**meta, listing_id=card.listing_id, origin="voice"))
    return f"{card.address}: {_beds(card.beds)}, {_money(card.monthly_cost)} a month."


async def focus_area(args: FocusArea, ctx: ToolContext, meta: dict) -> str:
    runtime = ctx.runtime
    spot = refs.area(args.place, runtime.matrix.named, _stations(), runtime.catalogue)
    if spot is None:
        return f"Couldn't find {args.place} on the map. {ASK_PLACE}"
    await runtime.emit(AreaFocused(**meta, label=args.place, lat=spot.lat, lng=spot.lng, zoom=AREA_ZOOM))
    return f"Showing {args.place}."


async def call_listings(args: CallListings, ctx: ToolContext, meta: dict) -> str:
    """Records the plan, or updates the one already recorded: what this call leaves out (the listings,
    the viewing times, the permission to book) stays as it was, and extra questions add up. The reply
    says the whole plan as it now stands, so the agent reads back what was saved."""
    view = ctx.runtime.get(ctx.session_id)
    board = view.board
    before = ctx.runtime.state(ctx.session_id).request
    if args.listing_refs:
        found = [refs.match(ref, board, view.focus_listing_id) for ref in args.listing_refs]
        if unclear := next((f for f in found if f.card is None), None):
            return _which(unclear)
        ids = list(dict.fromkeys(f.card.listing_id for f in found if f.card))
    elif before is not None:
        ids = list(before.listing_ids)
    else:
        ids = [c.listing_id for c in refs.top(board, TO_CALL)]
    if not ids:
        return "There's nothing on the list to call yet."
    questions = list(dict.fromkeys([*(before.questions if before else []), *(args.extra_questions or [])]))
    availability = args.availability or (before.availability if before else [])
    may_book = args.may_book if args.may_book is not None else bool(before and before.may_book)
    await ctx.runtime.emit(CallsRequested(**meta, listing_ids=ids, extra_questions=questions,
                                          availability=availability, may_book=may_book))
    addresses = [_told_apart(ctx, lid, board) for lid in ids]
    times = f"Viewings {_join([_window(a) for a in availability])}." if availability \
        else "No viewing times yet."
    booking = "I may book one." if may_book else "I won't book."
    return f"I'll call {_join(addresses)} after you hang up, and text you what they say. {times} {booking}"


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


def _which(found: refs.Match) -> str:
    """"Which one?", naming the cards a shared address fits by their number and price, so the agent can
    ask the renter and then answer with a number."""
    options = found.options
    if len(options) < 2 or len({c.address for c in options}) > 1:
        return WHICH
    named = _join([f"number {c.rank} at {_money(c.monthly_cost)}" for c in options[:3]])
    more = f" and {len(options) - 3} more" if len(options) > 3 else ""
    return f"Which one? {len(options)} places share {options[0].address}: {named}{more}. Ask the renter."


def _told_apart(ctx: ToolContext, listing_id: str, board: list[Card]) -> str:
    """The address, plus its number on the list when another card on the board has the same one."""
    address = _address(ctx, listing_id)
    card = next((c for c in board if c.listing_id == listing_id), None)
    twins = card is not None and sum(c.address == address for c in board) > 1
    return f"{address}, number {card.rank}," if twins and card else address


def _address(ctx: ToolContext, listing_id: str) -> str:
    return ctx.runtime.catalogue.by_id[listing_id].address


def _beds(beds: float) -> str:
    if beds == 0:
        return "a studio"
    n = int(beds) if beds == int(beds) else beds
    return f"{n} bedroom" if n == 1 else f"{n} bedrooms"


def _money(amount: int) -> str:
    return f"${amount:,}"


def _window(a: Availability) -> str:
    return f"{a.day.capitalize()} {_clock(a.from_)} to {_clock(a.to)}"


def _clock(hhmm: str) -> str:
    """'13:00' → '1 pm', '09:30' → '9:30 am'."""
    h, m = (int(x) for x in hhmm.split(":"))
    suffix, h12 = ("am" if h < 12 else "pm"), (h % 12 or 12)
    return f"{h12} {suffix}" if m == 0 else f"{h12}:{m:02d} {suffix}"


def _join(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]
