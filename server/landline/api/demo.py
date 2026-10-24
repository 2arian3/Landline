"""A demo session so the web app has a live board before the orchestrator exists.

RANKING. The Matcher (server/landline/scoring/, docs/SCORING.md) ranks the board
from the renter's preferences and keeps ties in the previous order; its reasons
and watch-out go on the cards. Reactions and call outcomes don't re-rank yet:
that's O-04, once the orchestrator feeds them in.

SCRIPTED CALLS. The demo follows docs/DEMO.md's flow without dialling anyone:
during the brief `plan_top` records the places to call (the Concierge's
call_listings), `hang_up` ends the brief (SessionEnded), and then four canned
listing agents go queued, dialing, ringing and live, hang up one by one, and the
session ends `done` with a results text. Until the Dispatcher (A-02) and the
Caller (A-05) exist, this is how the board shows every call status.

CAPTIONS. Each answered call has a script, served by /api/calls/{id}/turns as
the call goes: nothing until it's live, then a line every half tick, and the
whole script once it has ended. A fact on a card quotes a landlord's line word
for word, so the quotes below are written once and used by both.
"""
from __future__ import annotations

import asyncio
import os
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from landline.api.judge import ID_CHARS
from landline.contracts import (
    Area,
    CallView,
    Card,
    Fact,
    Fee,
    Listing,
    Phase,
    Preferences,
    SessionView,
    monthly_cost,
)
from landline.contracts.session import CardStatus
from landline.db.catalogue import Catalogue
from landline.db.rows import TurnRow
from landline.scoring import FeatureMatrix, Ranked, explain, rank

BOARD_SIZE = 6
TICK_S = 1.2     # each step of the scripted calls: dialing, ringing, live, then one hang-up per tick
if os.getenv("DEMO_TICK_S"):   # slower scripted calls for a video take, so their captions can be read
    TICK_S = float(os.environ["DEMO_TICK_S"])
PLANNED = 4      # the demo plans the top four places with a pin; the fourth never picks up
TORONTO = ZoneInfo("America/Toronto")
# "I'm free Saturday afternoon, book one if you can": the renter's window, and the slot an agent offers.
WINDOW_DAY, WINDOW_FROM, WINDOW_TO, SLOT = 5, 13, 17, 14   # Saturday (Monday is 0), 13:00–17:00, at 14:00
# A returning renter's welcome before this demo session has any results of its own: the Narrator's (O-06)
# line for that case, not invented results (AGENTS.md rule 3). A phone call's session gets the real ones
# from profiles (O-05).
NO_RESULTS_YET = "Here is your search from last time."
ON_THE_LINE = ("dialing", "ringing", "live")
# Places a renter might name ("show me Christie Pits"), and where the map goes for each. Demo only: the
# Concierge's focus_area tool resolves real places once it exists.
AREAS = {
    "christie pits": Area(label="Christie Pits", lat=43.6646, lng=-79.4206, zoom=15),
    "king and bay": Area(label="King and Bay", lat=43.6485, lng=-79.3802, zoom=15.5),
    "liberty village": Area(label="Liberty Village", lat=43.6377, lng=-79.4210, zoom=15),
    "the annex": Area(label="The Annex", lat=43.6703, lng=-79.4072, zoom=14.5),
    "yorkville": Area(label="Yorkville", lat=43.6710, lng=-79.3933, zoom=15),
}


def area_named(place: str) -> Area | None:
    """The demo's area for a place the renter named, however it's spaced or capitalised."""
    return AREAS.get(" ".join(place.lower().split()))

# What the scripted listing agents say on the record: each is a fact's quote, a fee's or a noted question,
# and the same words in that call's captions (AGENTS.md rule 3: a quote is what was said, verbatim).
PARKING = "Parking's a hundred and eighty a month. It comes with the unit, it's not optional."
DEPOSIT = "I'll need first and last by e-transfer before I can show it to you."
DOGS = "Dogs are fine. There are a couple in the building already."
LEASE = "Would they sign a 12-month lease?"


@dataclass(frozen=True)
class DemoCall:
    """What a scripted listing agent said, applied to its card when its call ends."""
    status: CardStatus
    facts: tuple[Fact, ...] = ()
    fees: tuple[Fee, ...] = ()
    red_flags: tuple[str, ...] = ()
    questions: tuple[str, ...] = ()      # noted for the renter (note_question); they come back in the results
    viewing: datetime | None = None      # a booked viewing, inside the renter's window
    said: tuple[tuple[str, str], ...] = ()   # (speaker, words) after the Caller's opener, as captions


@dataclass
class DemoRun:
    """One session's calls, from the places the renter picked to the results."""
    phase: Phase = "open"
    plan: list[str] = field(default_factory=list)
    calls: dict[str, CallView] = field(default_factory=dict)      # by listing, in plan order
    outcomes: dict[str, DemoCall] = field(default_factory=dict)   # by listing, as each call ends
    results: str | None = None
    captions: dict[str, tuple[TurnRow, ...]] = field(default_factory=dict)   # by listing: its whole script
    live_at: dict[str, float] = field(default_factory=dict)       # by listing: when it went live (clock)


class DemoSessions:
    """In-memory sessions with a version counter that SSE subscribers wait on."""

    def __init__(self, catalogue: Catalogue, tick_s: float = TICK_S,
                 clock: Callable[[], float] = time.monotonic):
        self.catalogue = catalogue
        self.clock = clock   # when calls went live, for how far their captions have got
        self.matrix = FeatureMatrix.build(catalogue.listings)   # once, at boot
        self.views: dict[str, SessionView] = {}
        # What the renter said about a listing, shown on its card ("You: too dark").
        self.reactions: dict[str, dict[str, str]] = {}
        # Each session's scripted calls, and the task that walks them.
        self.runs: dict[str, DemoRun] = {}
        self.dialing: dict[str, asyncio.Task] = {}
        # Each session's last results, kept through "Start over": what a returning renter hears first.
        self.last_results: dict[str, str] = {}
        self.tick_s = tick_s
        # Sessions marked as a returning renter, with the name to greet (None: no name on file).
        self.returning: dict[str, str | None] = {}
        # Returning sessions whose renter has said something since the welcome: like the Narrator, the
        # welcome opens the call and then gives way to what Landline says back.
        self.moved_on: set[str] = set()
        # The area the renter last asked to see, per session.
        self.areas: dict[str, Area] = {}
        self.changed = asyncio.Condition()

    def get(self, session_id: str) -> SessionView:
        if session_id not in self.views:
            prefs = Preferences(max_monthly=2800, min_beds=1, priorities=["cost", "size"])
            self.views[session_id] = self._build(session_id, prefs, version=1, focus=None)
        return self.views[session_id]

    async def react(self, session_id: str, listing_id: str, reaction: str, reason: str | None) -> SessionView:
        """Demo hook for FeedbackGiven: records the reaction on the card. Re-ranking is O-04's job."""
        said = reason or ("like it" if reaction == "like" else "not for me")
        self.reactions.setdefault(session_id, {})[listing_id] = said
        self.moved_on.add(session_id)
        return await self.update(session_id)

    async def plan_top(self, session_id: str) -> SessionView:
        """Demo hook for CallsRequested ("call the top four, I'm free Saturday afternoon"): the top
        places with a map pin become the plan. Nothing dials until the renter hangs up."""
        run = self.runs.setdefault(session_id, DemoRun())
        if run.phase != "open":
            return self.get(session_id)
        run.plan = [c.listing_id for c in self.get(session_id).board if _pinned(c)][:PLANNED]
        return await self.update(session_id)

    async def hang_up(self, session_id: str) -> SessionView:
        """Demo hook for SessionEnded: with a plan, every call in it is queued and the scripted calls
        start; without one, the brief closes."""
        run = self.runs.setdefault(session_id, DemoRun())
        if run.phase != "open":
            return self.get(session_id)
        if not run.plan:
            run.phase = "closed"
            return await self.update(session_id)
        run.phase = "calling"
        run.calls = {lid: CallView(call_id=_call_id(), listing_id=lid, state="queued") for lid in run.plan}
        view = await self.update(session_id)
        self.dialing[session_id] = asyncio.create_task(self._dial(session_id, run))
        return view

    async def reset(self, session_id: str) -> SessionView:
        """Demo hook: back to an open brief with nothing planned, to run the demo again."""
        if task := self.dialing.pop(session_id, None):
            task.cancel()
        self.runs.pop(session_id, None)
        self.moved_on.discard(session_id)   # the renter calls again: a returning one hears the welcome
        return await self.update(session_id)

    def turns(self, call_id: str, after: int = -1) -> list[TurnRow] | None:
        """A demo call's captions after line `after`, as far as the call has got: none until it's live,
        then line i from live_at + i × tick_s/2, and all of them once it has ended. None for a call this
        store didn't dial (or forgot on reset)."""
        for run in self.runs.values():
            for lid, call in run.calls.items():
                if call.call_id == call_id:
                    return [t for t in self._said_so_far(run, lid) if t.idx > after]
        return None

    def _said_so_far(self, run: DemoRun, lid: str) -> tuple[TurnRow, ...]:
        lines = run.captions.get(lid, ())
        if run.calls[lid].state == "ended":
            return lines
        went_live = run.live_at.get(lid)
        if went_live is None:
            return ()
        now = self.clock()
        return tuple(t for i, t in enumerate(lines) if now >= went_live + i * self.tick_s / 2)

    def _captions(self, call_id: str, lid: str, call: DemoCall) -> tuple[TurnRow, ...]:
        """The call's whole script: the Caller's opener (AGENTS.md rule 6), then what was said."""
        if call.status == "no_answer":
            return ()
        address = self.catalogue.by_id[lid].address
        opener = (f"Hi, I'm an AI assistant calling for a renter about the place at {address}. "
                  "Is it still available?")
        lines = (("agent", opener), *call.said)
        return tuple(TurnRow(call_id=call_id, idx=i, speaker=speaker, text=text, lang="en",
                             t_ms=round(i * self.tick_s / 2 * 1000))
                     for i, (speaker, text) in enumerate(lines))

    async def _dial(self, session_id: str, run: DemoRun) -> None:
        outcomes = dict(zip(run.plan, _outcomes(datetime.now(UTC)), strict=False))
        run.captions = {lid: self._captions(run.calls[lid].call_id, lid, outcomes[lid]) for lid in run.plan}
        answered = [lid for lid in run.plan if outcomes[lid].status != "no_answer"]
        for state, who in (("dialing", run.plan), ("ringing", run.plan), ("live", answered)):
            await asyncio.sleep(self.tick_s)
            for lid in who:
                call = run.calls[lid]
                run.calls[lid] = call.model_copy(update={"state": state,
                                                         "started_at": call.started_at or datetime.now(UTC)})
                if state == "live":
                    run.live_at[lid] = self.clock()
            await self.update(session_id)
        for lid in run.plan:   # one hang-up a tick, as each outcome comes in
            await asyncio.sleep(self.tick_s)
            run.outcomes[lid] = outcomes[lid]
            run.calls[lid] = run.calls[lid].model_copy(update={"state": "ended"})
            await self.update(session_id)
        run.phase, run.results = "done", self._results(run)
        self.last_results[session_id] = run.results
        if self.dialing.get(session_id) is asyncio.current_task():
            del self.dialing[session_id]
        await self.update(session_id)

    def _results(self, run: DemoRun) -> str:
        """The Narrator's results text, written by code from the outcomes: the booking first, then each
        call in the renter's order."""
        lines = []
        for lid in sorted(run.plan, key=lambda lid: run.outcomes[lid].viewing is None):
            x, out = self.catalogue.by_id[lid], run.outcomes[lid]
            if out.viewing:
                when = _when(out.viewing)
                lines.append(f"I booked {x.address} for {when}, inside your Saturday afternoon.")
            elif out.status == "suspect" and out.red_flags:
                lines.append(f"{x.address} {_lower(out.red_flags[0])}, so I'd skip it.")
            elif out.status == "no_answer":
                lines.append(f"Nobody picked up at {x.address}.")
            elif out.fees:
                cost = monthly_cost(x.rent, [*x.fees, *out.fees])
                lines.append(f"{x.address} is really ${cost:,} a month with parking.")
            for q in out.questions:
                lines.append(f"The agent at {x.address} asked, “{q}” I said I'd check with you.")
        return " ".join(lines)

    def close(self) -> None:
        for task in self.dialing.values():
            task.cancel()

    async def welcome_back(self, session_id: str, returning: bool, name: str | None) -> SessionView:
        """Demo hook for a known caller: O-05 decides "returning" from the phone number."""
        if returning:
            self.returning[session_id] = name
        else:
            self.returning.pop(session_id, None)
        self.moved_on.discard(session_id)
        return await self.update(session_id)

    async def focus_area(self, session_id: str, area: Area) -> SessionView:
        """Demo hook for AreaFocused ("show me Christie Pits"): the map flies there."""
        self.areas[session_id] = area
        return await self.update(session_id)

    async def update(self, session_id: str, prefs: Preferences | None = None,
                     focus: str | None = None) -> SessionView:
        cur = self.get(session_id)
        if prefs is not None:
            self.moved_on.add(session_id)
        view = self._build(session_id, prefs or cur.preferences, cur.version + 1,
                           focus if focus is not None else cur.focus_listing_id)
        self.views[session_id] = view
        async with self.changed:
            self.changed.notify_all()
        return view

    def _build(self, session_id: str, prefs: Preferences, version: int, focus: str | None) -> SessionView:
        before = self.views.get(session_id)
        ranking = rank(self.matrix, prefs, previous=[c.listing_id for c in before.board] if before else ())
        by_id = self.catalogue.by_id
        matches = [lid for lid in ranking.order if by_id[lid].lat is not None]
        board = [_card(by_id[lid], n, ranking.explain(lid))
                 for n, lid in enumerate(matches[:BOARD_SIZE], start=1)]
        run = self.runs.get(session_id) or DemoRun()
        board = [_with_call(c, run.outcomes[c.listing_id], by_id[c.listing_id], prefs)
                 if c.listing_id in run.outcomes
                 else c.model_copy(update={"status": "calling"})
                 if c.listing_id in run.calls and run.calls[c.listing_id].state in ON_THE_LINE
                 else c for c in board]
        said = self.reactions.get(session_id, {})
        board = [c.model_copy(update={"reaction": said[c.listing_id]}) if c.listing_id in said else c
                 for c in board]
        top = board[0].address if board else None
        says = (f"{len(matches)} places fit. {top} is on top." if top
                else "Nothing fits yet. Want to widen the budget or the area?")
        if (session_id in self.returning and session_id not in self.moved_on and run.phase == "open"
                and not run.plan):
            # A returning call opens with the welcome and what the last call found (docs/DEMO.md, 2:00).
            name = self.returning[session_id]
            last = self.last_results.get(session_id, NO_RESULTS_YET)
            says = f"Welcome back{f', {name}' if name else ''}. {last}"
        return SessionView(session_id=session_id, version=version, phase=run.phase, preferences=prefs,
                           user_name=self.returning.get(session_id), returning=session_id in self.returning,
                           focus_listing_id=focus, focus_area=self.areas.get(session_id),
                           matches=matches, board=board, agent_says=says,
                           plan=list(run.plan), calls=list(run.calls.values()), results=run.results)


def window(now: datetime) -> tuple[datetime, datetime]:
    """The renter's next Saturday afternoon, Toronto time: the window a viewing may be booked in."""
    local = now.astimezone(TORONTO)
    day = local + timedelta(days=(WINDOW_DAY - local.weekday()) % 7)
    start = day.replace(hour=WINDOW_FROM, minute=0, second=0, microsecond=0)
    if start.date() == local.date() and local.hour >= WINDOW_TO:
        start += timedelta(days=7)
    return start, start.replace(hour=WINDOW_TO)


def _outcomes(at: datetime) -> list[DemoCall]:
    """What the scripted listing agents say, in the plan's order; the fourth never picks up. Demo only:
    A-05's Caller records real facts in the agent's own words, and A-07 decides what's suspect."""
    def heard(field: str, value: str, quote: str) -> Fact:
        return Fact(field=field, value=value, quote=quote, speaker="Agent heard", at=at)

    start, _ = window(at)
    viewing = start.replace(hour=SLOT)
    return [
        DemoCall("verified", facts=(heard("parking_cost", "Parking is $180 a month, required", PARKING),),
                 fees=(Fee(name="parking", amount=180, period="monthly", mandatory=True, quote=PARKING),),
                 questions=(LEASE,),
                 said=(("landlord", "Yes, it is."),
                       ("agent", "What does parking cost?"),
                       ("landlord", PARKING),
                       ("landlord", LEASE),
                       ("agent", "I'll check with my client and get back to you."))),
        DemoCall("suspect",
                 facts=(heard("red_flags", "First and last month by e-transfer before a viewing", DEPOSIT),),
                 red_flags=("Wants a deposit by e-transfer before anyone sees the unit",),
                 said=(("landlord", DEPOSIT),
                       ("agent", "Thanks. I'll pass that on."))),
        DemoCall("booked", facts=(heard("pets", "Dogs are fine", DOGS),), viewing=viewing,
                 said=(("landlord", "Yes."),
                       ("agent", "Are pets okay? My client has a dog."),
                       ("landlord", DOGS),
                       ("agent", "Could they see it Saturday afternoon?"),
                       ("landlord", "Saturday at two works."),
                       ("agent", f"{_when(viewing)}, then. I'll let my client know."))),
        DemoCall("no_answer"),
    ]


def _call_id() -> str:
    """`c_` + 10 url-safe characters (docs/CONTRACTS.md), as CallManager.create() mints them."""
    return "c_" + "".join(secrets.choice(ID_CHARS) for _ in range(10))


def _when(at: datetime) -> str:
    local = at.astimezone(TORONTO)
    minutes = f":{local:%M}" if local.minute else ""
    return f"{local:%A} at {local.hour % 12 or 12}{minutes} {'pm' if local.hour >= 12 else 'am'}"


def _lower(s: str) -> str:
    return s[:1].lower() + s[1:]


def _pinned(c: Card) -> bool:
    """Inside the box MapView.tsx draws pins in (the parser nulls points outside it since #17). A place
    without a pin stays in the list, so the demo doesn't plan it: its status couldn't show."""
    return c.lat is not None and c.lng is not None and 43.3 < c.lat < 44.4 and -80.3 < c.lng < -78.6


def _with_call(card: Card, call: DemoCall, listing: Listing, p: Preferences) -> Card:
    if call.fees:
        # A fee the agent mentioned changes what the card says (price, reasons), not its place on the
        # board: re-ranking from what calls found is O-04's job.
        fees = [*listing.fees, *call.fees]
        paid = listing.model_copy(update={"fees": fees, "monthly_cost": monthly_cost(listing.rent, fees)})
        card = _card(paid, card.rank, explain(paid, p))
    return card.model_copy(update={"status": call.status, "facts": list(call.facts),
                                   "red_flags": [*card.red_flags, *call.red_flags]})


def _card(x: Listing, rank: int, why: Ranked) -> Card:
    return Card(
        listing_id=x.listing_id, address=x.address, unit_label=x.unit_label,
        neighbourhood=x.neighbourhood or x.building_name, lat=x.lat, lng=x.lng, rent=x.rent,
        monthly_cost=x.monthly_cost, beds=x.beds, baths=x.baths, sqft=x.sqft, floor=x.floor,
        unit_type=x.unit_type, photo=x.photos[0] if x.photos else None, light_score=x.light_score,
        source=x.source, url=x.url, rank=rank, reasons=why.reasons, watch_out=why.watch_out,
    )
