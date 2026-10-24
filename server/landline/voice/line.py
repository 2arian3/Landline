"""What the live Concierge hears between turns (A-02): the renter tapping a pin on the map, and the
Narrator's line when the board re-ranks in the background. docs/CONTRACTS.md, "Board message".

Each item goes in as one user `input_text`, quoted as data (`[renter is looking at] 412 Markham …`),
followed by a `response.create`, and only at a turn boundary: never while the renter is speaking, never
while a response is in flight or about to start, and never once the call is hanging up. A tap while the
line is quiet goes in at once; one during a turn waits for that turn's last `response.done`.

Items coalesce by kind: three taps in one turn deliver only the last, and a newer board line replaces an
older one. An item older than STALE_S, or a tap on the place the Concierge was last told about, is dropped.
So is a board line that comes during a tool round (from a tool call to the reply that answers it): the
tool's own commit made it, and its reply already says it (#164: answered again, it was two replies to one
line). And a board line the Concierge was already given.

`Lines` lives on app.state (hard rule 4): it subscribes to the runtime once and hands each event to the
live call of that session, if there is one. A `Line` lives for one call.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from landline.contracts import Card, Event, Listing, ListingFocused, SessionView
from landline.orchestrator import SessionRuntime
from landline.scoring.reasons import to_station

log = logging.getLogger(__name__)

STALE_S = 30.0          # a tap the renter made this long ago is no longer what they're looking at
WAIT_S = 2.0            # after the renter stops, how long a response may take to start (noise gets none)
LOOKING = "[renter is looking at]"
BOARD = "[the board changed]"

Send = Callable[[dict[str, Any]], Awaitable[None]]


@dataclass
class Item:
    kind: str           # "focus" | "board": one of each is kept, the newest
    text: str
    at: float
    key: str = ""       # a focus's listing id, to skip telling the Concierge twice


class Line:
    """One live Concierge call's queue. The bridge feeds it every Higgs event (`seen`); taps and board
    lines arrive through `push`."""

    def __init__(self, send: Send, clock: Callable[[], float] = time.monotonic) -> None:
        self.send, self.clock = send, clock
        self.items: dict[str, Item] = {}
        self.renter_speaking = False
        self.responding = False    # a response is in flight, or one we asked for hasn't started yet
        self.waiting_since: float | None = None   # the renter stopped; a response is about to start
        self.closed = False
        self.told: str | None = None   # the listing the Concierge was last told the renter is looking at
        self.tool_round = False        # a tool was called, and the reply that answers it isn't done yet
        self.spoke = False             # this response already has words: it is the reply (#219)
        self.board_told: str | None = None   # the last board line the Concierge was given
        self.delivered: list[str] = []

    async def push(self, item: Item) -> None:
        if self.closed:
            return
        if item.kind == "focus" and item.key == self.told and "focus" not in self.items:
            return
        if item.kind == "board" and (self.tool_round or item.text == self.board_told):
            log.info("line: dropped a board line the Concierge has%s", " in its reply" * self.tool_round)
            return
        self.items[item.kind] = item
        await self._deliver()

    async def seen(self, event: dict[str, Any]) -> None:
        """Every Higgs event of the call, in order; delivers at the end of a turn."""
        kind = event.get("type")
        if kind == "input_audio_buffer.speech_started":
            self.renter_speaking, self.waiting_since = True, None
        elif kind == "input_audio_buffer.speech_stopped":
            self.renter_speaking, self.waiting_since = False, self.clock()
        elif kind == "response.created":
            self.responding, self.waiting_since, self.spoke = True, None, False
        elif kind in ("response.output_audio.delta", "response.output_text.delta") and event.get("delta"):
            self.spoke = True
        elif kind == "response.function_call_arguments.done":
            self.tool_round = True
        elif kind == "response.done":
            output = (event.get("response") or {}).get("output") or []
            has_tool = any(o.get("type") == "function_call" for o in output)
            has_message = any(o.get("type") == "message" for o in output)
            # A tool response with no words is followed by another. One that already spoke is the reply,
            # and asking again said it twice (#219).
            if self.spoke or has_message or not has_tool:
                self.responding = self.tool_round = self.spoke = False
                await self._deliver()

    def close(self) -> None:
        """The call is ending: nothing more goes in."""
        self.closed = True
        self.items.clear()

    @property
    def quiet(self) -> bool:
        waiting = self.waiting_since is not None and self.clock() - self.waiting_since < WAIT_S
        return not (self.closed or self.renter_speaking or self.responding or waiting)

    async def _deliver(self) -> None:
        now = self.clock()
        for kind in [k for k, i in self.items.items() if now - i.at > STALE_S]:
            log.info("line: dropped a stale %s item", kind)
            del self.items[kind]
        if not self.items or not self.quiet:
            return
        items, self.items = sorted(self.items.values(), key=lambda i: i.at), {}
        self.responding = True     # before the first await, so a second push can't deliver twice
        text = "\n".join(i.text for i in items)
        for i in items:
            if i.kind == "focus":
                self.told = i.key
            elif i.kind == "board":
                self.board_told = i.text
        self.delivered.append(text)
        await self.send({"type": "conversation.item.create", "item": {
            "type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]}})
        await self.send({"type": "response.create"})


class Lines:
    """The live Concierge calls' lines, by session. Subscribes to the runtime once, at boot."""

    def __init__(self, runtime: SessionRuntime, clock: Callable[[], float] = time.monotonic) -> None:
        self.runtime, self.clock = runtime, clock
        self.live: dict[str, Line] = {}
        runtime.on("ListingFocused", self.on_focus)
        runtime.on("AnchorResolved", self.on_board)

    def open(self, session_id: str, send: Send) -> Line:
        line = self.live[session_id] = Line(send, self.clock)
        return line

    def close(self, session_id: str, line: Line) -> None:
        line.close()
        if self.live.get(session_id) is line:
            del self.live[session_id]

    async def on_focus(self, event: Event, view: SessionView) -> None:
        """A pin tapped on the map (origin ui). The Concierge's own focus already said it out loud, so it
        only counts as what the Concierge was last told."""
        line = self.live.get(event.session_id)
        if line is None or not isinstance(event, ListingFocused):
            return
        if event.origin != "ui":
            line.told = event.listing_id
            return
        card = next((c for c in view.board if c.listing_id == event.listing_id), None)
        if card is None:
            return
        said = describe(card, self.runtime.catalogue.by_id.get(card.listing_id))
        await line.push(Item("focus", f"{LOOKING} {said}", self.clock(), key=card.listing_id))

    async def on_board(self, event: Event, view: SessionView) -> None:
        """A background re-rank (a place geocoded after the renter named it): the Narrator's line for it,
        which no tool reply carried."""
        line = self.live.get(event.session_id)
        if line is None or not view.agent_says:
            return
        await line.push(Item("board", f"{BOARD} {view.agent_says}", self.clock()))


def describe(card: Card, listing: Listing | None = None) -> str:
    """The tapped place as the board shows it: the address, then numbers from the card, never a model's, and
    its neighbourhood and the walk to its nearest station, so a TTC question isn't guessed (#166)."""
    where = f"{card.address}, {card.unit_label}" if card.unit_label else card.address
    parts = ["studio" if card.beds == 0 else f"{card.beds:g} bed", f"${card.monthly_cost:,} a month"]
    if card.floor is not None:
        parts.append(f"floor {card.floor}")
    if card.sqft:
        parts.append(f"{card.sqft:,} sq ft")
    if card.neighbourhood:
        parts.append(f"in {card.neighbourhood}")
    if listing and (walk := to_station(listing.transit_walk_min, listing.transit_station)):
        parts.append(walk)
    return f"{where}: {', '.join(parts)}"
