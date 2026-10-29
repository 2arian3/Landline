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
from datetime import UTC, date, datetime, timedelta
from functools import cache
from typing import Any

from pydantic import BaseModel, ValidationError

from landline.agents.concierge import refs
from landline.agents.concierge.status import listing_status
from landline.contracts import (
    AnchorResolved,
    AreaFocused,
    CallsRequested,
    Card,
    FeedbackGiven,
    Listing,
    ListingFocused,
    PreferencesUpdated,
    SessionView,
)
from landline.contracts.tools import (
    CONCIERGE_TOOLS,
    AnchorRef,
    AskMaps,
    Availability,
    CallListings,
    FocusArea,
    FocusListing,
    GiveFeedback,
    PreferenceDelta,
    concierge_tools,
)
from landline.orchestrator import Refused, SessionRuntime, UnknownSession
from landline.orchestrator.maps_mcp import (
    DISCLOSURE,
    NEARBY_RESULTS,
    WALK_WARNING,
    Nearby,
    Place,
    Route,
    Weather,
    metres,
)
from landline.orchestrator.places import Places, Point, Unplaced, load_stations, place_of
from landline.orchestrator.runtime import PLACE_TIMEOUT_S
from landline.scoring.reasons import to_station
from landline.voice.dispatch import TORONTO, until_open

log = logging.getLogger(__name__)

# remember_me / forget_me wait for profiles (O-05): until then there is nothing for them to change.
DEFERRED = {"remember_me", "forget_me"}
# ask_maps (O-07b) is offered only when the runtime has Google's Maps MCP client (GOOGLE_MAPS_API_KEY).
MAPS_TOOL = "ask_maps"
TOOLS: list[dict[str, Any]] = [t for t in concierge_tools() if t["name"] not in DEFERRED | {MAPS_TOOL}]
_ASK_MAPS = next(t for t in concierge_tools() if t["name"] == MAPS_TOOL)


def offered(runtime: SessionRuntime) -> list[dict[str, Any]]:
    """The tools for this runtime's calls: ask_maps only with a Maps client."""
    return [*TOOLS, _ASK_MAPS] if getattr(runtime, "maps", None) is not None else TOOLS

AREA_ZOOM = 14
PLACE_WAIT_S = 2.5   # how long a turn waits on a place lookup; a slower one moves the map when it lands
MAPS_WAIT_S = 2.5    # how long a turn waits on Google Maps; a slower answer comes between turns (O-07b)
CHECKING_MAP = "Checking the map; tell the renter the answer comes in a moment."
MAP_ANSWER = "[map answer]"   # the note a late ask_maps answer comes in, through the call's Line
TO_CALL = 3   # call_listings without refs: the top three
UNKNOWN = "Sorry, I can't do that yet."
NOT_CAUGHT = "Sorry, I didn't catch that. Could you say it again?"
WHICH = "Which one do you mean? You can say its street or its number on the list."
BROKEN = "Sorry, something went wrong on my side."
# A place no data holds: the agent asks for one it can find, instead of saying "got it" to nothing.
ASK_PLACE = "Ask the renter for a nearby main street, intersection or subway station."
PLACE_LOST = "[place not found]"   # the note a place looked up after the reply comes in, when it isn't placed
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
    # The agent's line just before the renter's latest, so a plain "Yes" answers it (#174): "Should I book
    # tomorrow at 10?" "Yes."
    agent_before: str | None = None
    # ask_maps (O-07b): `text` texts the renter (a phone call's renter; None in the browser), `note` tells the
    # Concierge between turns (the call's Line). Google's disclosure is said once a call, and only the latest
    # ask's late answer is delivered.
    text: Callable[[str], None] | None = None
    note: Callable[[str], Awaitable[None]] | None = None
    maps_disclosed: bool = False
    maps_asks: int = 0
    # Not read: listing_status (A-14) answers from this session only. Kept for evals/sim/driver.py.
    history: Any | None = None


async def handle(name: str, arguments: str, tool_call_id: str, ctx: ToolContext) -> str:
    """Runs one tool call from Higgs and returns what the Concierge should hear back."""
    if name not in CONCIERGE_TOOLS or name in DEFERRED or (name == MAPS_TOOL and ctx.runtime.maps is None):
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
    args, points, lost, asked = _places(args, ctx)
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
    said = " ".join(filter(None, [view.agent_says if view.version != version or not (lost or asked) else None,
                                  _not_found(lost, ctx), *(_unplaced(u.label, u) for u in asked),
                                  _top_three(ctx, view)]))
    _watch(ctx, lost)
    return said


def _places(args: PreferenceDelta, ctx: ToolContext) -> tuple[PreferenceDelta, list[tuple[str, refs.Spot]],
                                                                list[str], list[Unplaced]]:
    """Finds each area and anchor the renter named: one of the big workplaces and campuses Places lists
    ("Cohere", "UofT"), else `refs.area`. A City neighbourhood stays an area, under the City's name, which
    is what the ranker matches. Anything else found (a street, a station, a building) becomes an anchor with
    its point. An area left as said and never matched would do nothing while the agent said "got it"
    ("around Queens"). A place nothing here holds ("King and Bay") is kept as an anchor, so the geocoder can
    still place it, and the reply says the list doesn't use it yet. A chain named alone ("TD Bank") is left
    out, and the reply asks which branch; when it's all they named, the search keeps its places."""
    if not args.areas and not args.anchors:
        return args, [], [], []
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
    asked: list[Unplaced] = []
    named = [(a, True) for a in args.areas or []] + [(a.label, False) for a in args.anchors or []]
    for label, is_area in named:
        if label.lower() in placed:
            anchors.append(label)
            continue
        listed = runtime.places.known(label)
        if isinstance(listed, Unplaced):
            asked.append(listed)
            continue
        # "Starbucks on Ossington" is looked for as Ossington (place_of).
        spot = refs.Spot(listed.lat, listed.lng, listed.how) if listed is not None else \
            refs.area(place_of(label), runtime.matrix.named, _stations(), runtime.catalogue)
        if spot is not None and spot.hoods and is_area:
            areas += spot.hoods
        elif spot is not None:
            anchors.append(label)
            points.append((label, spot))
        else:
            lost.append(label)
            anchors.append(label)
    if asked and len(asked) == len(named):   # only a chain: nothing replaces the places the search has
        areas, anchors = list(before.areas), [a.label for a in known]
    update = {"areas": list(dict.fromkeys(areas)),
              "anchors": [AnchorRef(label=x) for x in dict.fromkeys(anchors)]}
    return args.model_copy(update=update), points, lost, asked


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


def _unplaced(label: str, why: Unplaced | None) -> str:
    """What the agent hears about a place left off the map, and what to ask the renter instead."""
    if why is None:
        return f"Couldn't find {label} on the map, so the list doesn't use it. {ASK_PLACE}"
    if why.reason == "chain":
        return f"{label} has many branches, so it isn't on the map. {why.ask}"
    return f"{label} isn't on the map. {why.ask}"


def _watch(ctx: ToolContext, lost: list[str]) -> None:
    """Each place being looked up in the background (_not_found): if it isn't found, or the map's answer is
    held back (places.hold), the Concierge hears it between turns, to ask the renter."""
    if ctx.note is None or not ctx.runtime.places.contact:
        return
    for label in lost:
        task = asyncio.create_task(_tell_unplaced(ctx, label, ctx.runtime.place(ctx.session_id, label),
                                                  anchor=True))
        ctx.lookups.add(task)
        task.add_done_callback(ctx.lookups.discard)


async def _tell_unplaced(ctx: ToolContext, label: str, lookup: Awaitable[Point | Unplaced | None], *,
                         anchor: bool) -> None:
    """The place's answer, when it lands: nothing when it was placed (AnchorResolved moved the board, and
    the Line says so), else a note through the call's Line. Not once the call is ending, nor when the
    renter has since moved on from the place (`anchor`: it is no longer an unplaced anchor)."""
    try:
        point = await asyncio.wait_for(lookup, PLACE_TIMEOUT_S)
    except TimeoutError:
        point = None
    except (Refused, UnknownSession):
        return
    if isinstance(point, Point) or ctx.note is None or ctx.hang_up or not ctx.runtime.has(ctx.session_id):
        return
    state = ctx.runtime.state(ctx.session_id)
    still = any(a.label == label and a.lat is None for a in state.preferences.anchors)
    if state.phase != "open" or (anchor and not still):
        return
    await ctx.note(f"{PLACE_LOST} {_unplaced(label, point)}")


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
    return f"{card.address}: {', '.join(about(card, ctx.runtime.catalogue.by_id.get(card.listing_id)))}."


def about(card: Card, listing: Listing | None) -> list[str]:
    """What the agent may say about a place, from code: beds, monthly cost, neighbourhood and the walk to the
    nearest station, as the card shows them (#166), so a question about the TTC isn't guessed."""
    parts = [_beds(card.beds), f"{_money(card.monthly_cost)} a month"]
    if card.neighbourhood:
        parts.append(f"in {card.neighbourhood}")
    if listing and (walk := to_station(listing.transit_walk_min, listing.transit_station)):
        parts.append(walk)
    return parts


async def focus_area(args: FocusArea, ctx: ToolContext, meta: dict) -> str:
    """Found in code first (Places' landmarks, then `refs.area`: a City neighbourhood, a street, a station, a
    building). Anything else goes to Places (D-04), whose intersection and geocoder steps use the network:
    the turn waits PLACE_WAIT_S at most, and a slower lookup carries on and moves the map when it lands. A
    chain named alone, or a far geocoder answer (places.hold), changes nothing and asks the renter."""
    runtime = ctx.runtime
    listed = runtime.places.known(args.place)
    if isinstance(listed, Unplaced):
        return f"{_unplaced(args.place, listed)} Nothing changed."
    spot = listed or refs.area(place_of(args.place), runtime.matrix.named, _stations(), runtime.catalogue)
    if spot is not None:
        await _show_area(ctx, meta, args.place, spot.lat, spot.lng)
        return await _showing(ctx, meta, args.place, spot.lat, spot.lng)
    lookup = asyncio.ensure_future(runtime.place(ctx.session_id, args.place))
    try:
        point = await asyncio.wait_for(asyncio.shield(lookup), PLACE_WAIT_S)
    except TimeoutError:
        late = asyncio.create_task(_show_when_found(ctx, meta, args.place, lookup))
        ctx.lookups.add(late)
        late.add_done_callback(ctx.lookups.discard)
        return f"Finding {args.place} on the map. It'll show in a moment."
    if point is None:
        return f"Couldn't find {args.place} on the map. Nothing changed. {ASK_PLACE}"
    if isinstance(point, Unplaced):
        return f"{_unplaced(args.place, point)} Nothing changed."
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
        if isinstance(point := await lookup, Point):
            await _show_area(ctx, meta, place, point.lat, point.lng)
            return
    except (Refused, UnknownSession) as exc:   # the session ended while the lookup ran
        log.info("call %s: %r was found too late (%s)", ctx.call_id, place, exc)
        return
    await _tell_unplaced(ctx, place, lookup, anchor=False)


async def _show_area(ctx: ToolContext, meta: dict, place: str, lat: float, lng: float) -> None:
    await ctx.runtime.emit(AreaFocused(**meta, label=place, lat=lat, lng=lng, zoom=AREA_ZOOM))


# ── ask_maps (O-07b): the area around a listing, from Google's Maps MCP server ──────────────────────────────
#
# Option B on Google's terms (#275): Google's place is never put on our map. The answer is spoken, its names
# and minutes only as Google gave them (hard rule 3); the first answer in a call starts with Google's
# disclosure; a walking answer says Google's walking routes are in beta; and a phone caller is texted the
# answer with its "Google Maps" source link and the full warning. The listing and its coordinates come from
# the board and the catalogue, never from the model (hard rule 2). A slow answer doesn't hold the turn: after
# MAPS_WAIT_S the reply says it's coming, and it arrives through the call's Line unless the call has ended or
# a newer question replaced it.
#
# How far, how close and how many: the walks to the nearest subway station (D-10a) and to the nearest
# grocery store and pharmacy (D-10b) are stored on the listing, so those answers ask Google nothing. A place
# the renter names is one of our landmarks or TTC stations when Places knows it (Union, U of T, Cohere),
# routed to by its point; a chain named alone is its closest branch; anything else is found on Google first
# (place(), around downtown), then routed to. The verdict ("very close", "walkable", "not walking distance"),
# the km and the count within 1 km are computed here, from Google's numbers.

_MODE = {"walk": "WALK", "drive": "DRIVE", "transit": "TRANSIT"}
_BY = {"WALK": "on foot", "DRIVE": "by car", "TRANSIT": "by TTC"}
_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
VERY_CLOSE_MIN, WALKABLE_MIN = 10, 20   # a walk's verdict
WALK_TOO_MIN = 30      # a walk this long or longer isn't worth saying next to the TTC
WALK_TOO_KM = 2.5      # farther than this in a straight line, a walk takes 30 minutes or more: not asked for
WALK_FAR_KM = 2.0      # with no walking time, this far in a straight line is not walking distance
# Words that mean the TTC, the subway or the nearest station ("TTC station", "the subway", "closest station").
_TTC = {"ttc", "subway", "metro", "station", "transit"}
_TTC_FILLER = {"the", "a", "an", "nearest", "closest", "nearby", "near", "local", "public", "stop", "line",
               "underground", "to", "from"}
# The stores stored on each listing (D-10b), in the words a renter uses for them. A brand ("Shoppers") isn't
# one of them, and a plural ("grocery stores") asks how many, which only Google's search answers.
_STORES = {"grocery": {"grocery", "supermarket", "grocer", "food"},
           "pharmacy": {"pharmacy", "drugstore", "drug", "chemist"}}
_STORE_FILLER = {"a", "an", "the", "any", "nearest", "closest", "nearby", "near", "local", "store", "shop",
                 "place", "one"}


@dataclass(frozen=True)
class _Said:
    """One answer: the words to speak, the text with its source for the renter's phone, and whether it gave
    walking minutes."""
    spoken: str
    texted: str | None
    walking: bool = False


async def ask_maps(args: AskMaps, ctx: ToolContext, meta: dict) -> str:
    maps = ctx.runtime.maps
    if maps is None:
        return UNKNOWN
    if args.kind == "nearby" and not (args.what or "").strip():
        return "Ask the renter what kind of place to look for."
    if args.kind in _MODE and not (args.to or "").strip():
        return "Ask the renter where to."
    view = ctx.runtime.get(ctx.session_id)
    found = refs.match(args.listing_ref, view.board, view.focus_listing_id) if args.listing_ref else None
    focused = next((c for c in view.board if c.listing_id == view.focus_listing_id), None)
    card = found.card if found else focused
    if card is None:
        return _which(found) if found else "Which listing? Ask the renter for its number or street."
    listing = ctx.runtime.catalogue.by_id.get(card.listing_id)
    if listing is None or listing.lat is None or listing.lng is None:
        return f"I don't have {card.address} on the map."
    ctx.maps_asks += 1
    ask = ctx.maps_asks
    work = asyncio.ensure_future(_answer(maps, ctx.runtime.places, args, listing, card.address))
    done, _ = await asyncio.wait({work}, timeout=MAPS_WAIT_S)
    if done:
        return _deliver(ctx, work.result())
    ctx.runtime.spawn(_deliver_late(ctx, ask, work))   # runtime-tracked: close() cancels it, idle() waits
    return CHECKING_MAP


async def _answer(maps: Any, places: Places, args: AskMaps, listing: Listing, address: str) -> _Said:
    """The answer in words: from the listing's stored walks when they answer it, else from Google. Nothing but
    Google's names, minutes and summaries, the stored fields, and code's verdicts and counts goes in it."""
    if args.kind == "weather":
        return await _weather(maps, args.day, address, listing.lat, listing.lng)
    words = ((args.what if args.kind == "nearby" else args.to) or "").strip()
    if _means_ttc(words, listing.transit_station):
        return _stored_station(listing, address) or await _nearby(maps, "subway station", listing, address)
    store = _store(words)   # "how far is the nearest pharmacy?" is the closest one, however it's asked
    if store and not store[1] and (said := _stored_store(listing, store[0], words, address)):
        return said
    known = places.known(words)   # a landmark's point, or a chain named alone (Unplaced), with no network
    if args.kind == "nearby" or store or isinstance(known, Unplaced):   # a chain: its closest branch
        return await _nearby(maps, words, listing, address, store)
    ours = known if isinstance(known, Point) else places.station(words)
    return await _trip(maps, _MODE[args.kind], words, address, listing.lat, listing.lng, ours)


async def _nearby(maps: Any, what: str, listing: Listing, address: str,
                  store: tuple[str, bool] | None = None) -> _Said:
    """The closest place of a kind, the walk to it, and how many Google found within 1 km."""
    n: Nearby | None = await maps.nearby(listing.lat, listing.lng, what)
    one = _kind(what)
    if n is None:
        if store and (said := _stored_store(listing, store[0], what, address)):
            return said
        return _Said(f"I couldn't find {_a(one)} near {address} on Google Maps.", None)
    walk = _walk(n.minutes) if n.minutes is not None else None
    if n.name and walk:
        spoken = f"The closest {one} to {address} is {n.name}, {walk}."
    elif n.name:
        spoken = f"The closest {one} to {address} is {n.name}."
    elif walk:
        spoken = f"There's {_a(one)} {walk} from {address}."
    else:
        spoken = f"Google Maps shows {_a(one)} near {address}."
    near = n.within_1km
    count = f"{near} or more" if near >= NEARBY_RESULTS else str(near) if near else "none"
    spoken += f" Google Maps shows {count} within 1 km."
    return _Said(spoken, _text(spoken, n.attribution, n.maps_url or n.attribution_url, n.warning),
                 walking=n.minutes is not None)


async def _trip(maps: Any, mode: str, to: str, address: str, lat: float, lng: float,
                ours: Point | None) -> _Said:
    """How far a place the renter named is from the listing. A landmark or a TTC station from our own lists
    (`ours`: Places' landmarks and stations) is routed to by its point, one call to Google; anything else is
    found on Google first (place(), looked for around downtown), then routed to by its id. Transit is also
    how a question that doesn't say how comes, so it gets the walk too when the place is within WALK_TOO_KM
    in a straight line."""
    place: Place | None = None
    if ours is not None:
        name = ours.name if ours.how != "station" else f"{ours.name} station"
        name, at = name or to, (ours.lat, ours.lng)
    else:
        place = await maps.place(to)
        if place is None:
            return _Said(f"I couldn't find {to} on Google Maps.", None)
        name = place.name or f"the place Google found for {to}"
        at = (place.lat, place.lng) if place.lat is not None and place.lng is not None else None
    km = metres(lat, lng, *at) / 1000 if at is not None else None
    modes = [mode] + (["WALK"] if mode == "TRANSIT" and (km is None or km < WALK_TOO_KM) else [])
    routes: dict[str, Route | None] = dict(zip(modes, await asyncio.gather(
        *(maps.route((lat, lng), place or at, m) for m in modes)), strict=True))
    walk, ride = routes.get("WALK"), routes.get(mode) if mode != "WALK" else None
    spoken = _how_far(address, name, walk, ride, km)
    if spoken is None:
        return _Said(f"I couldn't get a time {_BY[mode]} to {name} from Google Maps.", None)
    said_walk = walk is not None and (ride is None or walk.minutes < WALK_TOO_MIN)
    source = place or ride or walk   # our own place has no Google page: the route's attribution is the source
    texted = _text(spoken, source.attribution, place.maps_url or place.attribution_url if place
                   else source.attribution_url, WALK_WARNING if said_walk else None)
    return _Said(spoken, texted, walking=said_walk)


def _how_far(address: str, name: str, walk: Route | None, ride: Route | None, km: float | None) -> str | None:
    """How far, with code's verdict. A walk of up to WALKABLE_MIN comes first ("very close", "walkable");
    otherwise the ride comes first, with the walk when it's under WALK_TOO_MIN, or else "not walking distance"
    when the walk is longer or the place is WALK_FAR_KM or more away in a straight line."""
    if walk is not None and (ride is None or walk.minutes <= WALKABLE_MIN):
        said = f"{address} is {_walk(walk.minutes)} from {name} ({_distance(walk)}){_verdict(walk.minutes)}."
        if ride is not None:
            by = _BY[ride.mode]
            said += f" {by[:1].upper()}{by[1:]}, it's about {ride.minutes} minutes."
        return said
    if ride is None:
        return None
    said = f"{address} is about {ride.minutes} minutes {_BY[ride.mode]} ({_distance(ride)}) from {name}"
    if walk is not None and walk.minutes < WALK_TOO_MIN:
        return f"{said}, or {_walk(walk.minutes)}."
    if walk is not None or (km is not None and km >= WALK_FAR_KM):
        return f"{said}; that's not walking distance."
    return f"{said}."


def _means_ttc(words: str, station: str | None) -> bool:
    """Whether the words mean the TTC, the subway or the nearest station, however they're put ("TTC station",
    "the subway", "closest station", "public transit"), or name the listing's own nearest station."""
    said = [w for w in _singular(words) if w not in _TTC_FILLER]
    rest = [w for w in said if w not in _TTC]
    if not rest:
        return bool(said)
    own = [w for w in _singular(station or "") if w not in _TTC]
    return bool(own) and rest == own


def _singular(text: str) -> list[str]:
    """The words, lower case, each without a plural "s" ("stations", "stops")."""
    return [w.removesuffix("s") for w in re.findall(r"[a-z0-9]+", text.lower())]


def _stored_station(listing: Listing, address: str) -> _Said | None:
    """The walk to the nearest subway station stored on the listing (D-10a), or None when it has none."""
    station, minutes = listing.transit_station, listing.transit_walk_min
    if not station or minutes is None:
        return None
    if minutes < 1:
        return _Said(f"{address} is right next to {station} station.", None)
    m = _whole(minutes)
    return _Said(f"The nearest subway station to {address} is {station}, {_walk(m)}{_verdict(m)}.", None)


def _store(what: str) -> tuple[str, bool] | None:
    """The stored store the words ask for, "grocery" or "pharmacy", and whether they're plural; None for
    anything else, a brand among them."""
    kinds: set[str | None] = set()
    plural = False
    for w in re.findall(r"[a-z]+", what.lower()):
        one = w[:-3] + "y" if w.endswith("ies") else w[:-1] if w.endswith("s") and not w.endswith("ss") else w
        plural |= one != w
        if one not in _STORE_FILLER:
            kinds.add(next((k for k, words in _STORES.items() if one in words), None))
    if len(kinds) != 1 or None in kinds:
        return None
    return kinds.pop(), plural


def _stored_store(listing: Listing, store: str, what: str, address: str) -> _Said | None:
    """The nearest grocery store or pharmacy stored on the listing (D-10b), or None when it has none."""
    name, minutes = ((listing.grocery_name, listing.grocery_walk_min) if store == "grocery"
                     else (listing.pharmacy_name, listing.pharmacy_walk_min))
    if minutes is None:
        return None
    m, one = _whole(minutes), _kind(what)
    if name:
        return _Said(f"The closest {one} to {address} is {name}, {_walk(m)}{_verdict(m)}.", None)
    return _Said(f"There's {_a(one)} {_walk(m)} from {address}{_verdict(m)}.", None)


async def _weather(maps: Any, words: str | None, address: str, lat: float, lng: float) -> _Said:
    day = _day(words, datetime.now(TORONTO).date())
    w: Weather | None = await maps.weather(lat, lng, day)
    named = (words or "today").strip()
    if w is None:
        return _Said(f"I couldn't get the forecast for {named} from Google Maps.", None)
    spoken = f"{named[:1].upper()}{named[1:]} near {address}: {w.summary}"
    return _Said(spoken, _text(spoken, w.attribution, w.attribution_url, None))


def _deliver(ctx: ToolContext, said: _Said) -> str:
    """The reply the Concierge speaks, with Google's disclosure on the first answer of the call; and the text
    with the source link, when there's a renter's phone to send it to."""
    spoken = said.spoken + (" Google says its walking routes are in beta." if said.walking else "")
    if not ctx.maps_disclosed and said.texted is not None:
        spoken, ctx.maps_disclosed = f"{DISCLOSURE}. {spoken}", True
    if said.texted is not None and ctx.text is not None:
        ctx.text(said.texted)
        spoken += " I've texted you the Google Maps link."
    return spoken


async def _deliver_late(ctx: ToolContext, ask: int, work: asyncio.Future) -> None:
    """A slow answer, delivered between turns, unless the call has ended or a newer question replaced it."""
    try:
        said = await work
    except Exception:
        log.exception("call %s: ask_maps failed", ctx.call_id)
        return
    open_ = ctx.runtime.has(ctx.session_id) and ctx.runtime.state(ctx.session_id).phase == "open"
    if ctx.hang_up or ask != ctx.maps_asks or not open_:
        log.info("call %s: a map answer came too late and was dropped", ctx.call_id)
        return
    reply = _deliver(ctx, said)
    if ctx.note is not None:
        await ctx.note(f"{MAP_ANSWER} {reply}")


def _text(spoken: str, attribution: str, url: str | None, warning: str | None) -> str:
    """What the renter is texted: the answer, then its source exactly as Google titles it, with the link,
    and Google's walking warning when it has one."""
    source = f"Source: {attribution}" + (f" {url}" if url else "")
    return " ".join(x for x in (f"Landline: {spoken}", source, warning, f"{DISCLOSURE}.") if x)


def _day(words: str | None, today: date) -> date:
    """The day the renter named for a forecast: today, tomorrow or the coming weekday; today otherwise."""
    t = (words or "").lower()
    if "tomorrow" in t:
        return today + timedelta(days=1)
    for i, name in enumerate(_WEEKDAYS):
        if name in t:
            return today + timedelta(days=(i - today.weekday()) % 7)
    return today


def _a(what: str) -> str:
    """"a grocery store", "an ATM", or a name as it is ("Shoppers")."""
    if not what or what[:1].isupper():
        return what
    return f"{'an' if what[:1].lower() in 'aeiou' else 'a'} {what}"


def _kind(what: str) -> str:
    """The kind of place in the renter's words, as one: "a grocery store" and "grocery stores" are "grocery
    store"; a name stays as it is ("Shoppers", "No Frills")."""
    words = what.split()
    while len(words) > 1 and words[0].lower() in {"a", "an", "the", "any", "nearest", "closest", "nearby"}:
        words.pop(0)
    last = words[-1] if words else ""
    if last[:1].islower() and last.endswith("ies"):
        words[-1] = last[:-3] + "y"
    elif last[:1].islower() and last.endswith("s") and not last.endswith("ss"):
        words[-1] = last[:-1]
    return " ".join(words)


def _whole(minutes: float) -> int:
    """Stored minutes as whole minutes, rounded half up, and at least 1."""
    return max(1, int(minutes + 0.5))


def _walk(minutes: int) -> str:
    return f"about {minutes} minute's walk" if minutes == 1 else f"about {minutes} minutes' walk"


def _verdict(walk_min: int) -> str:
    """Code's verdict on a walk, said after it: very close, walkable, or nothing."""
    if walk_min <= VERY_CLOSE_MIN:
        return "; that's very close"
    return "; that's walkable" if walk_min <= WALKABLE_MIN else ""


def _distance(route: Route) -> str:
    """A route's length as said: metres to the nearest 10 under 1 km, else km to one decimal."""
    if route.distance_m < 1000:
        return f"{round(route.distance_m, -1)} m"
    return f"{route.km:g} km"


async def call_listings(args: CallListings, ctx: ToolContext, meta: dict) -> str:
    """Records the plan, or updates the one already recorded: what this call leaves out (the listings,
    the viewing times) stays as it was, and extra questions add up. Every plan books a viewing unless the
    renter said not to (_may_book). The reply says the whole plan as it now stands, so the agent reads back
    what was saved."""
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
    lines = _when_lines(ctx)
    parsed = _windows_from(lines) if lines is not None else []
    # A day alone ("tomorrow") is 09:00–18:00, whatever clock the model sent (#218). Those bounds accept a
    # booking; they are not hours the renter gave, so the reply below never reads them out.
    day_only = bool(args.availability and parsed and lines is not None
                    and not any(_hours_in(line) for line in lines))
    invented = bool(args.availability) and lines is not None and not any(_WHEN.search(line) for line in lines)
    if day_only:
        log.info("call %s: a day alone, so the model's hours were replaced with the default bounds",
                 ctx.call_id)
        availability = parsed
    elif invented:   # times the renter never gave: with may_book, the Caller would book them (#174)
        log.info("call %s: call_listings had viewing times the renter never said; not kept", ctx.call_id)
        availability = list(before.availability) if before else []
    else:
        availability = args.availability or (list(before.availability) if before else [])
    may_book = _may_book(args, ctx, before)
    defaults = default_windows(meta["at"])
    if availability == defaults:
        availability = []   # an earlier reply's defaults: the renter still hasn't given times
    # No times from the renter: the plan still gets a window, so the Caller can book (the 14:35 run
    # recorded none and booked nothing).
    windows = availability or (defaults if may_book else [])
    await ctx.runtime.emit(CallsRequested(**meta, listing_ids=ids, extra_questions=questions,
                                          availability=windows, may_book=may_book))
    addresses = [_told_apart(ctx, lid, board) for lid in ids]
    # The call's timing comes first, so a viewing window is never read back as when the calls go out.
    # A day alone names the day and no clock (#218): the default bounds stay in the plan, unsaid.
    if availability and day_only:
        times = (f"Viewing: {_join([_day_label(a) for a in availability])}. "
                 "They gave no hours, so don't name any.")
    elif availability:
        times = f"Viewing window: {_join([_window(a) for a in availability])}."
    elif may_book:
        times = (f"No viewing times from them yet: ask when they could go to a viewing. If they give none, "
                 f"I'll book one in the next {DEFAULT_DAYS} days, {_clock(DEFAULT_HOURS[0])} to "
                 f"{_clock(DEFAULT_HOURS[1])}.")
    else:
        times = "No viewing times yet."
    booking = "" if may_book else " Booking: I won't book, as they asked."
    go = _calls_go_out(ctx, meta["at"])
    # Listings and viewing times, or booking turned down: nothing is left to ask, so the agent ends the
    # call instead of waiting for the renter's bye (Mahyar's test).
    done = not invented and (bool(availability) or not may_book)
    when = "as soon as you hang up" if go.startswith("right") else go
    book = ", book a viewing in their times," if may_book else ""
    end = (f" The plan is complete: tell them you'll call {when}{book} and text the results, say goodbye, "
           "and call end_session.") if done else ""
    also = ", booking a viewing in your times," if may_book else ","
    return f"Calling {_join(addresses)} {go}{also} and texting you what they say. {times}{booking}{end}"


# When the renter gave no viewing times, the plan books in these: the next DEFAULT_DAYS days from
# tomorrow, DEFAULT_HOURS Toronto time.
DEFAULT_DAYS = 3
DEFAULT_HOURS = ("10:00", "18:00")


def default_windows(now: datetime) -> list[Availability]:
    today = now.astimezone(TORONTO).weekday()
    days = ["tomorrow", *(_WEEKDAYS[(today + i) % 7] for i in range(2, DEFAULT_DAYS + 1))]
    return [Availability(day=d, **{"from": DEFAULT_HOURS[0]}, to=DEFAULT_HOURS[1]) for d in days]


def _may_book(args: CallListings, ctx: ToolContext, before: Any) -> bool:
    """Landline books a viewing whenever the renter asks for calls, whatever the model sent: only the
    renter saying not to book ("don't book", "just ask, don't book") turns it off, and asking for a booking
    later on the call turns it back on. The renter's latest word on booking counts. With no transcript, an
    earlier "don't book" stays; in another script, which code can't read, the model's false stands."""
    from landline.agents.concierge.backstop import foreign  # backstop imports this module
    lines = list(ctx.renter_lines or ([ctx.renter_said] if ctx.renter_said else []))
    for line in reversed(lines):
        if (said := books(line)) is not None:
            return said
    if lines and foreign(lines[-1]) and args.may_book is False:
        return False
    if before is not None and not before.may_book and args.may_book is not True:
        return False
    return True


# A renter's line that turns a booking down ("don't book", "just ask, don't book"), or a plain yes.
_NO_BOOKING = re.compile(r"\b(don'?t|do not|no need to|not|without)\b[^.?!]{0,25}\b(book|reserv|schedul)",
                         re.I)
_PLAIN_YES = re.compile(r"^\W*(yes|yeah|yep|yup|sure|please|ok(ay)?|go ahead|do it|sounds good|s[ií]|oui)\b",
                        re.I)


# A line that asks for a booking itself (#174). Not "go ahead and call", which is only a yes to a question.
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


# When a renter says they can view (#174): a day, and maybe a part of it. A day alone is 09:00 to 18:00,
# a bound for accepting a booking. It is never spoken as hours the renter gave (#218).
_DAY_WORD = re.compile(r"\b(today|tonight|tomorrow|monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
                       r"weekend)\b", re.I)
_PARTS = {"morning": ("09:00", "12:00"), "afternoon": ("12:00", "17:00"), "evening": ("17:00", "20:00"),
          "tonight": ("17:00", "20:00")}
_SPAN = re.compile(r"\b(?:between|from)\s+(\d{1,2})(?::(\d\d))?\s*(am|pm)?\s+(?:and|to|until|-)\s+"
                   r"(\d{1,2})(?::(\d\d))?\s*(am|pm)?", re.I)
_AT = re.compile(r"\bat\s+(\d{1,2})(?::(\d\d))?\s*(am|pm)?\b", re.I)
# A clock, a part of the day, or a span. "tomorrow" and "Saturday" have none; "9 to 5" and "afternoon" do.
_HOURS = re.compile(r"\b(?:noon|midnight|morning|afternoon|evening|tonight)\b|"
                    r"\b\d{1,2}(?::\d\d)?\s*(?:a\.?m\.?|p\.?m\.?)\b|"
                    r"\b\d{1,2}(?::\d\d)?\s*(?:to|until|-|and)\s*\d{1,2}\b|"
                    r"\bat\s+\d{1,2}\b", re.I)
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
    start, end = "09:00", "18:00"   # a day alone: stored, and not said back as their hours (#218)
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


def _hours_in(text: str) -> bool:
    """Whether a line names a clock or a part of the day, not only a day (#218)."""
    return _HOURS.search(text) is not None


def _when_lines(ctx: ToolContext) -> list[str] | None:
    """Lines a viewing time is read from, or None when the model's times stand: no transcript, or a line
    in another script. A plain yes includes the question it answers ("Yes" to "tomorrow at 10?")."""
    from landline.agents.concierge.backstop import foreign  # backstop imports this module
    lines = list(ctx.renter_lines or ([ctx.renter_said] if ctx.renter_said else []))
    if not lines or any(foreign(line) for line in lines):
        return None
    if ctx.agent_before and plain_yes(lines[-1]):
        lines.append(ctx.agent_before)
    return lines


def _windows_from(lines: list[str]) -> list[Availability]:
    """Days the lines name. A later line wins for the same day, and a day alone is 09:00 to 18:00."""
    by_day: dict[str, Availability] = {}
    order: list[str] = []
    for line in lines:
        for window in windows_in(line):
            if window.day not in by_day:
                order.append(window.day)
            by_day[window.day] = window
    return [by_day[day] for day in order]


def _named_when(ctx: ToolContext) -> bool:
    """Whether the renter named a day or a time on this call, so viewing times aren't the model's guess.
    With no line to go by, or a line in another script, the model's word stands."""
    lines = _when_lines(ctx)
    if lines is None:
        return True
    return any(_WHEN.search(line) for line in lines)


def plain_yes(line: str | None) -> bool:
    return bool(line and _PLAIN_YES.search(line) and not _NO_BOOKING.search(line))


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
    MAPS_TOOL: ask_maps,
    "call_listings": call_listings,
    "listing_status": listing_status,   # A-14: agents/concierge/status.py
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


def _day_label(a: Availability) -> str:
    return a.day if a.day in ("today", "tomorrow") else a.day.capitalize()


def _window(a: Availability) -> str:
    return f"{_day_label(a)} {_clock(a.from_)} to {_clock(a.to)}"


def _clock(hhmm: str) -> str:
    """'13:00' → '1 pm', '09:30' → '9:30 am'."""
    h, m = (int(x) for x in hhmm.split(":"))
    suffix, h12 = ("am" if h < 12 else "pm"), (h % 12 or 12)
    return f"{h12} {suffix}" if m == 0 else f"{h12}:{m:02d} {suffix}"


def _join(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]
