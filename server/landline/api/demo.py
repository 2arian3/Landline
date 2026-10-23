"""A demo session so the web app has a live board before the orchestrator exists.

PLACEHOLDER RANKING. This is not the Matcher: it filters on beds and budget and
orders by a simple cost/size heuristic. Task O-03 replaces `_rank` with the
scoring in docs/SCORING.md; the session shape and the SSE versioning stay.
"""
from __future__ import annotations

import asyncio

from landline.contracts import Card, Listing, Preferences, SessionView
from landline.db.catalogue import Catalogue

BOARD_SIZE = 6


class DemoSessions:
    """In-memory sessions with a version counter that SSE subscribers wait on."""

    def __init__(self, catalogue: Catalogue):
        self.catalogue = catalogue
        self.views: dict[str, SessionView] = {}
        # What the renter said about a listing, shown on its card ("You: too dark").
        self.reactions: dict[str, dict[str, str]] = {}
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
        said = self.reactions.get(session_id, {})
        board = [c.model_copy(update={"reaction": said[c.listing_id]}) if c.listing_id in said else c
                 for c in board]
        top = board[0].address if board else None
        says = (f"{len(matches)} places fit. {top} is on top." if top
                else "Nothing fits yet. Want to widen the budget or the area?")
        return SessionView(session_id=session_id, version=version, preferences=prefs,
                           focus_listing_id=focus, matches=[x.listing_id for x in matches],
                           board=board, agent_says=says)


def _fits(x: Listing, p: Preferences) -> bool:
    if p.min_beds is not None and x.beds < p.min_beds:
        return False
    if p.max_monthly is not None and x.monthly_cost > p.max_monthly * 1.10:
        return False
    return x.unit_type != "room"


def _rank(matches: list[Listing], p: Preferences) -> list[Card]:
    budget = p.max_monthly or 3000

    def score(x: Listing) -> float:
        headroom = min(max(budget - x.monthly_cost, -300), 700) / 700
        size = min((x.sqft or 0) / 900, 1)
        return 0.6 * headroom + 0.4 * size

    cards = []
    for rank, x in enumerate(sorted(matches, key=lambda x: (-score(x), x.listing_id)), start=1):
        reasons = []
        if x.monthly_cost <= budget:
            reasons.append(f"${budget - x.monthly_cost:,} under budget")
        if x.sqft:
            reasons.append(f"{x.sqft:,} sq ft")
        if x.utilities_included:
            reasons.append("utilities included")
        watch = f"${x.monthly_cost - budget:,} over budget" if x.monthly_cost > budget else None
        cards.append(Card(
            listing_id=x.listing_id, address=x.address, unit_label=x.unit_label,
            neighbourhood=x.neighbourhood or x.building_name, lat=x.lat, lng=x.lng, rent=x.rent,
            monthly_cost=x.monthly_cost, beds=x.beds, baths=x.baths, sqft=x.sqft, floor=x.floor,
            unit_type=x.unit_type, photo=x.photos[0] if x.photos else None, source=x.source, url=x.url,
            rank=rank, reasons=reasons[:3], watch_out=watch,
        ))
    return cards
