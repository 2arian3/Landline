"""A demo session so the web app has a live board before the orchestrator exists.

PLACEHOLDER RANKING. This is not the Matcher: it filters on beds and budget and
orders by a simple cost/size heuristic. Task O-03 replaces `_rank` with the
scoring in docs/SCORING.md; the session shape and the SSE versioning stay.

SCRIPTED CALLS. `call_top` plays three canned listing agents so the board can
show every call status before the Caller (A-05) exists. Nobody is dialled.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime

from landline.contracts import Card, Fact, Fee, Listing, Preferences, SessionView, monthly_cost
from landline.contracts.session import CardStatus
from landline.db.catalogue import Catalogue

BOARD_SIZE = 6
CALL_S = 3.0   # how long a scripted demo call rings before it hangs up


@dataclass(frozen=True)
class DemoCall:
    """One listing's scripted call: "calling" until it hangs up, then what its agent said."""
    status: CardStatus
    facts: tuple[Fact, ...] = ()
    fees: tuple[Fee, ...] = ()
    red_flags: tuple[str, ...] = ()


class DemoSessions:
    """In-memory sessions with a version counter that SSE subscribers wait on."""

    def __init__(self, catalogue: Catalogue, call_s: float = CALL_S):
        self.catalogue = catalogue
        self.views: dict[str, SessionView] = {}
        # What the renter said about a listing, shown on its card ("You: too dark").
        self.reactions: dict[str, dict[str, str]] = {}
        # Scripted calls by listing, and the task that hangs them up.
        self.calls: dict[str, dict[str, DemoCall]] = {}
        self.dialing: dict[str, asyncio.Task] = {}
        self.call_s = call_s
        # Sessions marked as a returning renter, with the name to greet (None: no name on file).
        self.returning: dict[str, str | None] = {}
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
        return await self.update(session_id)

    async def call_top(self, session_id: str) -> SessionView:
        """Demo hook for CallsRequested ("call the top three"): the top three cards show "calling", and
        call_s later each hangs up with a scripted outcome. Calling again restarts the walk."""
        picked = [c.listing_id for c in self.get(session_id).board[:3]]
        if not picked:
            return self.get(session_id)
        if running := self.dialing.pop(session_id, None):
            running.cancel()
        calls = self.calls.setdefault(session_id, {})
        for listing_id in picked:
            calls[listing_id] = DemoCall("calling")
        view = await self.update(session_id)
        self.dialing[session_id] = asyncio.create_task(self._hang_up(session_id, picked))
        return view

    async def _hang_up(self, session_id: str, picked: list[str]) -> None:
        await asyncio.sleep(self.call_s)
        calls = self.calls[session_id]
        for listing_id, outcome in zip(picked, _outcomes(datetime.now(UTC)), strict=False):
            calls[listing_id] = outcome
        if self.dialing.get(session_id) is asyncio.current_task():
            del self.dialing[session_id]
        await self.update(session_id)

    def close(self) -> None:
        for task in self.dialing.values():
            task.cancel()

    async def welcome_back(self, session_id: str, returning: bool, name: str | None) -> SessionView:
        """Demo hook for a known caller: O-05 decides "returning" from the phone number."""
        if returning:
            self.returning[session_id] = name
        else:
            self.returning.pop(session_id, None)
        return await self.update(session_id)

    async def update(self, session_id: str, prefs: Preferences | None = None,
                     focus: str | None = None) -> SessionView:
        cur = self.get(session_id)
        view = self._build(session_id, prefs or cur.preferences, cur.version + 1,
                           focus if focus is not None else cur.focus_listing_id)
        self.views[session_id] = view
        async with self.changed:
            self.changed.notify_all()
        return view

    def _build(self, session_id: str, prefs: Preferences, version: int, focus: str | None) -> SessionView:
        matches = [x for x in self.catalogue.listings if _fits(x, prefs) and x.lat is not None]
        board = _rank(matches, prefs)[:BOARD_SIZE]
        calls = self.calls.get(session_id, {})
        board = [_with_call(c, calls[c.listing_id], self.catalogue.by_id[c.listing_id], prefs)
                 if c.listing_id in calls else c for c in board]
        said = self.reactions.get(session_id, {})
        board = [c.model_copy(update={"reaction": said[c.listing_id]}) if c.listing_id in said else c
                 for c in board]
        top = board[0].address if board else None
        says = (f"{len(matches)} places fit. {top} is on top." if top
                else "Nothing fits yet. Want to widen the budget or the area?")
        return SessionView(session_id=session_id, version=version, preferences=prefs,
                           user_name=self.returning.get(session_id), returning=session_id in self.returning,
                           focus_listing_id=focus, matches=[x.listing_id for x in matches],
                           board=board, agent_says=says)


def _outcomes(at: datetime) -> list[DemoCall]:
    """What the three scripted listing agents say, in rank order. Demo only: A-05's Caller records real
    facts in the agent's own words, and A-07 decides what's suspect."""
    def heard(field: str, value: str, quote: str) -> Fact:
        return Fact(field=field, value=value, quote=quote, speaker="Agent heard", at=at)

    parking = "Parking's a hundred and eighty a month. It comes with the unit, it's not optional."
    return [
        DemoCall("verified", facts=(heard("parking_cost", "Parking is $180 a month, required", parking),),
                 fees=(Fee(name="parking", amount=180, period="monthly", mandatory=True, quote=parking),)),
        DemoCall("suspect",
                 facts=(heard("red_flags", "First and last month by e-transfer before a viewing",
                              "I'll need first and last by e-transfer before I can show it to you."),),
                 red_flags=("Wants a deposit by e-transfer before anyone sees the unit",)),
        DemoCall("verified", facts=(heard("pets", "Dogs are fine",
                                          "Dogs are fine. There are a couple in the building already."),)),
    ]


def _with_call(card: Card, call: DemoCall, listing: Listing, p: Preferences) -> Card:
    if call.fees:
        # A fee the agent mentioned changes what the card says (price, budget), not its place on the
        # board: re-ranking from what calls found is O-04's job.
        fees = [*listing.fees, *call.fees]
        paid = listing.model_copy(update={"fees": fees, "monthly_cost": monthly_cost(listing.rent, fees)})
        card = _card(paid, card.rank, _budget(p))
    return card.model_copy(update={"status": call.status, "facts": list(call.facts),
                                   "red_flags": [*card.red_flags, *call.red_flags]})


def _budget(p: Preferences) -> int:
    return p.max_monthly or 3000


def _fits(x: Listing, p: Preferences) -> bool:
    if p.min_beds is not None and x.beds < p.min_beds:
        return False
    if p.max_monthly is not None and x.monthly_cost > p.max_monthly * 1.10:
        return False
    return x.unit_type != "room"


def _rank(matches: list[Listing], p: Preferences) -> list[Card]:
    budget = _budget(p)

    def score(x: Listing) -> float:
        headroom = min(max(budget - x.monthly_cost, -300), 700) / 700
        size = min((x.sqft or 0) / 900, 1)
        return 0.6 * headroom + 0.4 * size

    ordered = sorted(matches, key=lambda x: (-score(x), x.listing_id))
    return [_card(x, rank, budget) for rank, x in enumerate(ordered, start=1)]


def _card(x: Listing, rank: int, budget: int) -> Card:
    reasons = []
    if x.monthly_cost <= budget:
        reasons.append(f"${budget - x.monthly_cost:,} under budget")
    if x.sqft:
        reasons.append(f"{x.sqft:,} sq ft")
    if x.utilities_included:
        reasons.append("utilities included")
    watch = f"${x.monthly_cost - budget:,} over budget" if x.monthly_cost > budget else None
    return Card(
        listing_id=x.listing_id, address=x.address, unit_label=x.unit_label,
        neighbourhood=x.neighbourhood or x.building_name, lat=x.lat, lng=x.lng, rent=x.rent,
        monthly_cost=x.monthly_cost, beds=x.beds, baths=x.baths, sqft=x.sqft, floor=x.floor,
        unit_type=x.unit_type, photo=x.photos[0] if x.photos else None, source=x.source, url=x.url,
        rank=rank, reasons=reasons[:3], watch_out=watch,
    )
