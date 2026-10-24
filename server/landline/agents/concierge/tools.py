"""The Concierge's tools on the session runtime (A-04).

Each tool validates the model's arguments with its contract model (`contracts/tools.py`), resolves
whatever they name against the board in code (`refs.py`), sends one event to the runtime, and answers
with a short line built from the session as it now stands. The answer is spoken: at most 25 words, no
ids. The session and call come from `ToolContext`, which the bridge fills from the call it runs, never
from the arguments (hard rule 2). The event's idem_key is the Higgs tool-call id, so a repeat is a no-op.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
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
    SessionView,
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
from landline.voice.dispatch import TORONTO, until_open

log = logging.getLogger(__name__)

# remember_me / forget_me wait for profiles (O-05): until then there is nothing for them to change.
DEFERRED = {"remember_me", "forget_me"}
TOOLS: list[dict[str, Any]] = [t for t in concierge_tools() if t["name"] not in DEFERRED]

AREA_ZOOM = 14
PLACE_WAIT_S = 2.5   # how long a turn waits on a place lookup; a slower one moves the map when it lands
TO_CALL = 3   # call_listings without refs: the top three
UNKNOWN = "Sorry, I can't do that yet."
NOT_CAUGHT = "Sorry, I didn't catch that. Could you say it again?"
WHICH = "Which one do you mean? You can say its street or its number on the list."
BROKEN = "Sorry, something went wrong on my side."
# A place no data holds: the agent asks for one it can find, instead of saying "got it" to nothing.
ASK_PLACE = "Ask the renter for a nearby main street or subway station."
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
    # Place lookups that outlasted their turn (focus_area): each moves the map when it lands.
    lookups: set[asyncio.Task] = field(default_factory=set)
    # The latest lines on the call, from the bridge's transcripts; None off the phone or before any. A
    # booking is only allowed when the renter's line agrees to one (call_listings, #154).
    renter_said: str | None = None
    agent_said: str | None = None
    # Every renter line on the call so far (the bridge's transcripts): a viewing window is kept only when
    # one of them names a day or a time (call_listings, #174).
    renter_lines: list[str] = field(default_factory=list)


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
    commit, so the board has moved by the time the agent hears the reply. (A phone caller's board link is
    texted when the call connects: voice/bridge.py.)"""
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
                                  _not_found(lost, ctx), _top_three(ctx, view)]))
    return said


def _places(args: PreferenceDelta, ctx: ToolContext) -> tuple[PreferenceDelta, list[tuple[str, refs.Spot]],
                                                                list[str]]:
    """Finds each area and anchor the renter named (`refs.area`). A City neighbourhood stays an area, under
    the City's name, which is what the ranker matches. Anything else found (a street, a station, a
    building) becomes an anchor with its point. An area left as said and never matched would do nothing
    while the agent said "got it" ("around Queens"). A place nothing here holds ("King and Bay") is kept
    as an anchor, so the geocoder can still place it, and the reply says the list doesn't use it yet."""
    if not args.areas and not args.anchors:
        return args, [], []
    runtime = ctx.runtime
    before = runtime.state(ctx.session_id).preferences
    known = before.anchors
    placed = {a.label.lower() for a in known if a.lat is not None}
    # A place named now replaces the ones before it ("sorry, near Christie Street"), unless the renter adds
    # it ("and also near my work"): otherwise the old place keeps steering the list as the map moves (#183).
    keep = _adds_place(ctx.renter_said)
    areas: list[str] = list(before.areas) if keep else []
    anchors: list[str] = [a.label for a in known] if keep else []
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
            anchors.append(label)
    update = {"areas": list(dict.fromkeys(areas)),
              "anchors": [AnchorRef(label=x) for x in dict.fromkeys(anchors)]}
    return args.model_copy(update=update), points, lost


_ADDS_PLACE = re.compile(r"\b(also|as well|both|either|in addition|plus)\b|"
                         r"\band (also )?(near|around|close to|by|in)\b", re.I)
# "Show me the options near X" asks for listings there, not just the map (#183).
_WANTS_LISTINGS = re.compile(r"\b(options?|places|listings?|apartments?|rentals?|units?|homes?|condos?|"
                             r"anything)\b", re.I)


# "What are my top options now?": with no tool that reads the board, the agent stalled (#183). A reply that
# changed the board names its top three when the renter's line asks for them.
_ASKS_TOP = re.compile(r"\b(top|best|options?|choices|what do you have|anything good)\b", re.I)


def _top_three(ctx: ToolContext, view: SessionView) -> str | None:
    if not ctx.renter_said or not _ASKS_TOP.search(ctx.renter_said) or not view.board:
        return None
    named = [f"{c.rank}. {c.address}, {_money(c.monthly_cost)}, {_beds(c.beds)}" for c in view.board[:3]]
    return f"Top three: {'; '.join(named)}."


def _adds_place(line: str | None) -> bool:
    """Whether the renter's latest line adds a place to the ones already in the search."""
    return bool(line) and _ADDS_PLACE.search(line) is not None


def _not_found(lost: list[str], ctx: ToolContext) -> str | None:
    if not lost:
        return None
    if ctx.runtime.places.contact:   # the geocoder tries it in the background (D-04)
        return f"Looking up {_join(lost)} on the map; the list moves if it's found."
    return f"Couldn't find {_join(lost)} on the map, so the list doesn't use it yet. {ASK_PLACE}"


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
    return " ".join(filter(None, [f"Noted for {_address(ctx, listing_id)}. {view.agent_says}",
                                  _top_three(ctx, view)]))


async def focus_listing(args: FocusListing, ctx: ToolContext, meta: dict) -> str:
    view = ctx.runtime.get(ctx.session_id)
    found = refs.match(args.listing_ref, view.board, view.focus_listing_id)
    card = found.card
    if card is None:
        return _which(found)
    await ctx.runtime.emit(ListingFocused(**meta, listing_id=card.listing_id, origin="voice"))
    return f"{card.address}: {_beds(card.beds)}, {_money(card.monthly_cost)} a month."


async def focus_area(args: FocusArea, ctx: ToolContext, meta: dict) -> str:
    """Found in code first (`refs.area`: a City neighbourhood, a street, a station, a building). Anything
    else goes to Places (D-04), whose intersection and geocoder steps use the network: the turn waits
    PLACE_WAIT_S at most, and a slower lookup carries on and moves the map when it lands."""
    runtime = ctx.runtime
    spot = refs.area(args.place, runtime.matrix.named, _stations(), runtime.catalogue)
    if spot is not None:
        await _show_area(ctx, meta, args.place, spot.lat, spot.lng)
        return await _showing(ctx, meta, args.place, spot.lat, spot.lng)
    lookup = asyncio.ensure_future(runtime.places.resolve(args.place))
    try:
        point = await asyncio.wait_for(asyncio.shield(lookup), PLACE_WAIT_S)
    except TimeoutError:
        late = asyncio.create_task(_show_when_found(ctx, meta, args.place, lookup))
        ctx.lookups.add(late)
        late.add_done_callback(ctx.lookups.discard)
        return f"Finding {args.place} on the map. It'll show in a moment."
    if point is None:
        return f"Couldn't find {args.place} on the map. Nothing changed. {ASK_PLACE}"
    await _show_area(ctx, meta, args.place, point.lat, point.lng)
    return await _showing(ctx, meta, args.place, point.lat, point.lng)


async def _showing(ctx: ToolContext, meta: dict, place: str, lat: float, lng: float) -> str:
    """The map is on `place`. When the renter asked for listings there ("show me the options near X"),
    the list ranks by it too, at that same point, so the map and the list agree (#183)."""
    if not ctx.renter_said or not _WANTS_LISTINGS.search(ctx.renter_said):
        return f"Showing {place}."
    sid = ctx.session_id
    before = ctx.runtime.state(sid).preferences
    keep = _adds_place(ctx.renter_said)
    anchors = [AnchorRef(label=a.label) for a in before.anchors] if keep else []
    delta = PreferenceDelta(areas=list(before.areas) if keep else [],
                            anchors=[*anchors, AnchorRef(label=place)])
    key = f"{meta['idem_key']}:rank"
    async with ctx.runtime.session_tx(sid) as tx:
        await tx.apply(PreferencesUpdated(**{**meta, "idem_key": key}, delta=delta))
        await tx.apply(AnchorResolved(session_id=sid, at=meta["at"], idem_key=f"{key}:place", label=place,
                                      lat=lat, lng=lng))
    view = ctx.runtime.get(sid)
    return " ".join(filter(None, [f"Showing {place}, and the list now ranks by it. {view.agent_says}",
                                  _top_three(ctx, view)]))


async def _show_when_found(ctx: ToolContext, meta: dict, place: str, lookup: asyncio.Future) -> None:
    try:
        if (point := await lookup) is not None:
            await _show_area(ctx, meta, place, point.lat, point.lng)
    except (Refused, UnknownSession) as exc:   # the session ended while the lookup ran
        log.info("call %s: %r was found too late (%s)", ctx.call_id, place, exc)


async def _show_area(ctx: ToolContext, meta: dict, place: str, lat: float, lng: float) -> None:
    await ctx.runtime.emit(AreaFocused(**meta, label=place, lat=lat, lng=lng, zoom=AREA_ZOOM))


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
    if args.listing_refs and len(ids) < TO_CALL and asks_for_the_best(ctx.renter_said):
        ids = [c.listing_id for c in refs.top(board, TO_CALL)]   # "call the best ones": the top three
    if not ids:
        return "There's nothing on the list to call yet."
    questions = list(dict.fromkeys([*(before.questions if before else []), *(args.extra_questions or [])]))
    invented = bool(args.availability) and not _named_when(ctx)
    if invented:   # times the renter never gave: with may_book, the Caller would book them (#174)
        log.info("call %s: call_listings had viewing times the renter never said; not kept", ctx.call_id)
    availability = (None if invented else args.availability) or (before.availability if before else [])
    may_book = args.may_book if args.may_book is not None else bool(before and before.may_book)
    unasked = args.may_book is True and not _agrees_to_book(ctx.renter_said, ctx.agent_said)
    if unasked:   # the model set it, but the renter didn't say yes to a booking (#154)
        may_book = bool(before and before.may_book)
    await ctx.runtime.emit(CallsRequested(**meta, listing_ids=ids, extra_questions=questions,
                                          availability=availability, may_book=may_book))
    addresses = [_told_apart(ctx, lid, board) for lid in ids]
    # The call's timing comes first, so a viewing window is never read back as when the calls go out.
    times = f"Viewing window: {_join([_window(a) for a in availability])}." if availability \
        else f"No viewing times yet.{' Ask when they are free for a viewing.' if invented else ''}"
    # New viewing times and nothing said about booking (#189): the landlord said "yes, tomorrow" and the
    # Caller couldn't take a time, so the Concierge asks. Not after a "don't book", nor for the same times.
    ask = not may_book and args.may_book is None and bool(availability) and \
        availability != (before.availability if before else []) and books(ctx.renter_said or "") is not False
    booking = ("Booking: not allowed yet. Ask whether you may book a viewing." if unasked and not may_book
               else "Booking: not allowed yet. Ask whether you may book a viewing in that window." if ask
               else "Booking: I may book one." if may_book else "Booking: I won't book.")
    return (f"Calling {_join(addresses)} {_calls_go_out(ctx, meta['at'])}, and texting you what they say. "
            f"{times} {booking}")


# A renter's line that agrees to a booking, or turns one down. Checked in code: the model set may_book on
# "see if it's available tomorrow for a check" (#154).
_NO_BOOKING = re.compile(r"\b(don'?t|do not|no need to|not|without)\b[^.?!]{0,25}\b(book|reserv|schedul)",
                         re.I)
_BOOKING = re.compile(r"\b(book|reserv)\w*|\bgo ahead\b|"
                      r"\b(schedule|arrange|set up)\b[^.?!]{0,12}\b(viewing|visit|tour|showing)", re.I)
_PLAIN_YES = re.compile(r"^\W*(yes|yeah|yep|yup|sure|please|ok(ay)?|go ahead|do it|sounds good|s[ií]|oui)\b",
                        re.I)


def _agrees_to_book(renter: str | None, agent: str | None) -> bool:
    """Whether the renter's latest line says yes to booking a viewing: it asks for one ("book a viewing",
    "go ahead and book it"), or it's a plain yes right after the agent asked about booking. With no line to
    go by (not on the phone, or the transcript isn't in yet), or a line in another script, the model's
    word stands, rather than asking the same thing in a loop."""
    from landline.agents.concierge.backstop import foreign  # backstop imports this module
    if not renter or foreign(renter):
        return True
    if _NO_BOOKING.search(renter):
        return False
    if _BOOKING.search(renter):
        return True
    asked = bool(agent) and "?" in agent and re.search(r"\bbook|\bviewing", agent, re.I) is not None
    return asked and _PLAIN_YES.search(renter) is not None


# A line that asks for a booking itself, for the call catch-up (#174). Not "go ahead and call", which says
# yes only right after the agent asked about booking (_agrees_to_book).
_ASKS_TO_BOOK = re.compile(r"\b(book|reserv)\w*|"
                           r"\b(schedule|arrange|set up)\b[^.?!]{0,12}\b(viewing|visit|tour|showing)", re.I)


def books(line: str) -> bool | None:
    """What a renter's line says about booking: True when it asks for a viewing ("set up a visit"), False
    when it says not to book, else None."""
    if _NO_BOOKING.search(line):
        return False
    return True if _ASKS_TO_BOOK.search(line) else None


# "Call the best ones" or "the top ones", with no number: the top three (f2's holdout,
# too-expensive-then-call-default called one).
_THE_BEST = re.compile(r"\b(?:best|top)\s+(?:ones|places|listings|options)\b", re.I)
_A_COUNT = re.compile(r"\b(?:two|three|four|five|2|3|4|5)\b", re.I)


def asks_for_the_best(line: str | None) -> bool:
    return bool(line and _THE_BEST.search(line) and not _A_COUNT.search(line))


# When a renter says they can view (#174): a day, and maybe a part of it. A day alone is 09:00 to 18:00.
_DAY_WORD = re.compile(r"\b(today|tonight|tomorrow|monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
                       r"weekend)\b", re.I)
_PARTS = {"morning": ("09:00", "12:00"), "afternoon": ("12:00", "17:00"), "evening": ("17:00", "20:00"),
          "tonight": ("17:00", "20:00")}
_SPAN = re.compile(r"\b(?:between|from)\s+(\d{1,2})(?::(\d\d))?\s*(am|pm)?\s+(?:and|to|until|-)\s+"
                   r"(\d{1,2})(?::(\d\d))?\s*(am|pm)?", re.I)
_AT = re.compile(r"\bat\s+(\d{1,2})(?::(\d\d))?\s*(am|pm)?\b", re.I)
_WHEN = re.compile(r"\b(today|tonight|tomorrow|monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
                   r"weekend|morning|afternoon|evening|noon|ma[ñn]ana|lunes|martes|mi[ée]rcoles|jueves|viernes|"
                   r"s[áa]bado|domingo|demain|lundi|mardi|mercredi|jeudi|vendredi|samedi|dimanche)\b|"
                   r"\b\d{1,2}(?::\d\d)?\s*(?:am|pm)\b", re.I)


def _hour(h: str, m: str | None, half: str | None) -> str:
    """"1" with no am or pm reads as 1 pm (1 to 7 is the afternoon), "9" as 9 am, as viewing times go."""
    n = int(h) % 12 if half else int(h)
    if half and half.lower() == "pm":
        n += 12
    elif not half and 1 <= n <= 7:
        n += 12
    return f"{min(n, 23):02d}:{m or '00'}"


def windows_in(text: str) -> list[Availability]:
    """The viewing windows a line names: "tomorrow" is 09:00 to 18:00, "Saturday afternoon" 12:00 to 17:00,
    "tomorrow between 1 and 5" 13:00 to 17:00, "tomorrow at 10" 10:00 to 11:00, "this weekend" both days.
    None without a day."""
    t = text.lower()
    days: list[str] = []
    for m in _DAY_WORD.finditer(t):
        word = m.group(1)
        named = ["saturday", "sunday"] if word == "weekend" else ["today"] if word == "tonight" else [word]
        days += [d for d in named if d not in days]
    if not days:
        return []
    start, end = "09:00", "18:00"
    if span := _SPAN.search(t):
        start = _hour(span.group(1), span.group(2), span.group(3) or span.group(6))
        end = _hour(span.group(4), span.group(5), span.group(6))
    elif at := _AT.search(t):
        start = _hour(at.group(1), at.group(2), at.group(3))
        end = f"{min(int(start[:2]) + 1, 23):02d}:{start[3:]}"
    else:
        part = next((p for p in _PARTS if p in t), None)
        if part:
            start, end = _PARTS[part]
    if end <= start:
        return []
    return [Availability(day=d, **{"from": start}, to=end) for d in days]


def _named_when(ctx: ToolContext) -> bool:
    """Whether the renter named a day or a time on this call, so viewing times aren't the model's guess.
    With no line to go by, or a line in another script, the model's word stands, as in _agrees_to_book."""
    from landline.agents.concierge.backstop import foreign  # backstop imports this module
    lines = ctx.renter_lines or ([ctx.renter_said] if ctx.renter_said else [])
    if not lines or any(foreign(line) for line in lines):
        return True
    return any(_WHEN.search(line) for line in lines)


def _calls_go_out(ctx: ToolContext, now: datetime) -> str:
    """When the listing calls go out, in words. A phone renter's calls wait for business hours
    (voice/dispatch.py); every other session's go out right away."""
    wait = until_open(now) if ctx.runtime.state(ctx.session_id).channel == "phone" else 0
    if not wait:
        return "right after you hang up"
    opens = (now + timedelta(seconds=wait)).astimezone(TORONTO)
    day = "today" if opens.date() == now.astimezone(TORONTO).date() else "tomorrow"
    return f"at 9 am {day}, when calling hours start"


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
    day = a.day if a.day in ("today", "tomorrow") else a.day.capitalize()
    return f"{day} {_clock(a.from_)} to {_clock(a.to)}"


def _clock(hhmm: str) -> str:
    """'13:00' → '1 pm', '09:30' → '9:30 am'."""
    h, m = (int(x) for x in hhmm.split(":"))
    suffix, h12 = ("am" if h < 12 else "pm"), (h % 12 or 12)
    return f"{h12} {suffix}" if m == 0 else f"{h12}:{m:02d} {suffix}"


def _join(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]
