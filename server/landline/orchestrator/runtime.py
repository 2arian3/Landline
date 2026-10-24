"""The session runtime: the one writer for every session (docs/ARCHITECTURE.md, Orchestration).

    runtime = SessionRuntime(catalogue, db)           # db=None keeps sessions in memory only
    view = await runtime.start(SessionStarted(...))   # the session id comes from the caller's code
    view = await runtime.emit(PreferencesUpdated(...))
    async with runtime.session_tx(sid) as tx:         # several events and writes, one commit
        await tx.apply(CallsScheduled(...))
        await db.calls.create(..., conn=tx.conn)
    runtime.on("SessionEnded", dispatcher.on_session_ended)   # runs after the commit
    runtime.opening(sid)                              # what the Concierge remembers about a returning caller

`session_tx` holds the session's lock, applies each event to a working copy, then
re-ranks, appends the events, saves the snapshot and bumps the version in one
Postgres transaction. Only then does the copy become the session, so a handler
that fails or is refused leaves nothing behind. A duplicate `idem_key` is a no-op.
After the commit, SSE subscribers wake on `changed` and each subscriber to the
committed event types runs as a background task, so none of them blocks a turn.

With Postgres, a phone call's number is looked up when its session starts (Profiles, O-05): a new number
gets a user, and a returning one gets back its search, its reactions and its last results. The profile
is saved when the brief ends and again when the calls are done, after those commits.
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager, nullcontext
from dataclasses import dataclass, field
from datetime import UTC, datetime

from psycopg import AsyncConnection

from landline.contracts import AnchorResolved, Event, SessionStarted, SessionView
from landline.db.catalogue import Catalogue
from landline.db.pool import Database
from landline.orchestrator import board, handlers, narrator
from landline.orchestrator.calllog import CallLog
from landline.orchestrator.places import Places
from landline.orchestrator.profiles import Profiles, Remembered, opening
from landline.orchestrator.state import SessionState
from landline.scoring import FeatureMatrix, rank

log = logging.getLogger(__name__)

Subscriber = Callable[[Event, SessionView], Awaitable[None]]
RECALL_TIMEOUT_S = 2.0   # a slow lookup never holds up the greeting: the caller is treated as new
PLACE_TIMEOUT_S = 20.0   # a place lookup off the hot path; past this the place stays unresolved


class UnknownSession(KeyError):
    """No session with that id: sessions are started by code (SessionStarted), never on first sight."""


class DuplicateEvent(Exception):
    """Postgres already holds this idem_key though this process hadn't seen it: the commit rolls back."""


@dataclass
class _Live:
    state: SessionState
    view: SessionView
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    seen: set[str] = field(default_factory=set)   # idem keys committed since boot


class Tx:
    """One `session_tx`: a working copy of the session and, with Postgres, the open transaction."""

    def __init__(self, live: _Live, conn: AsyncConnection | None, db: Database | None,
                 known: handlers.Known):
        self.state = live.state.model_copy(deep=True)
        self.conn = conn
        self.events: list[Event] = []
        self._seen, self._db, self._known = live.seen, db, known

    async def apply(self, event: Event) -> bool:
        """Run the event's handler on the working copy. False when it's a duplicate or changes nothing."""
        if event.session_id != self.state.session_id:
            raise ValueError(f"{event.type} for {event.session_id} applied to {self.state.session_id}")
        key = event.idem_key
        if key in self._seen or any(e.idem_key == key for e in self.events):
            return False
        if self._db is not None and await self._db.events.seen(key, conn=self.conn):
            return False
        if not handlers.apply(self.state, event, self._known):
            return False
        self.events.append(event)
        return True


class SessionRuntime:
    def __init__(self, catalogue: Catalogue, db: Database | None = None, *,
                 matrix: FeatureMatrix | None = None, places: Places | None = None,
                 contact: str | None = None):
        self.catalogue = catalogue
        self.matrix = matrix or FeatureMatrix.build(catalogue.listings, catalogue.hoods)   # once, at boot
        self.db = db
        self.changed = asyncio.Condition()   # SSE subscribers wait on it; notified after every commit
        self._live: dict[str, _Live] = {}
        self._starting = asyncio.Lock()
        self._subscribers: dict[str, list[Subscriber]] = defaultdict(list)
        self._tasks: set[asyncio.Task] = set()
        self.calls = CallLog(db, self._spawn)   # renter calls and their transcripts, for the admin panel
        self.profiles = Profiles(db, live=self.has) if db is not None else None
        if self.profiles is not None:
            self.on("SessionEnded", self._remember)
            self.on("CallsCompleted", self._remember)
        # Places the renter names become points in the background, then AnchorResolved re-ranks (D-04).
        self.places = places or Places(self.matrix.named, contact=contact)
        self.on("PreferencesUpdated", self._resolve_places)
        self.on("SessionStarted", self._resolve_places)   # a returning caller's saved places

    def __len__(self) -> int:
        return len(self._live)

    def has(self, session_id: str) -> bool:
        return session_id in self._live

    def get(self, session_id: str) -> SessionView:
        return self._get(session_id).view

    def state(self, session_id: str) -> SessionState:
        """A copy of the session's state, to read from (the Dispatcher freezes the plan from it)."""
        return self._get(session_id).state.model_copy(deep=True)

    def opening(self, session_id: str) -> str | None:
        """What the Concierge is told about a returning caller (≤ 120 words); None for a new one."""
        return self._get(session_id).state.opening

    def on(self, event_type: str, fn: Subscriber) -> None:
        """Run `fn(event, view)` after every committed event of `event_type`, as a background task."""
        self._subscribers[event_type].append(fn)

    async def start(self, event: SessionStarted) -> SessionView:
        """Create the session at version 1. Starting one that exists is a no-op."""
        sid = event.session_id
        remembered = await self._recall(event) if sid not in self._live else None
        async with self._starting:
            if sid in self._live:
                return self._live[sid].view
            state = SessionState(session_id=sid, channel=event.channel, user_id=event.user_id,
                                 caller_phone=event.caller_phone)
            if remembered is not None:
                self._returning(state, remembered)
            view = self._build(state, 1, events=[event])
            if self.db is not None:
                async with self.db.transaction() as conn:
                    await self.db.sessions.create(sid, event.channel, state.user_id, conn=conn)
                    await self.db.events.append(event, conn=conn)
                    await self.db.sessions.save(sid, expected_version=0, status=state.phase,
                                                snapshot=_snapshot(state, view), conn=conn)
            self._live[sid] = _Live(state, view, seen={event.idem_key})
        await self._published(sid, [event])
        return view

    async def emit(self, event: Event) -> SessionView:
        """Apply one event in its own transaction and return the session as it stands after it."""
        async with self.session_tx(event.session_id) as tx:
            await tx.apply(event)
        return self.get(event.session_id)

    @asynccontextmanager
    async def session_tx(self, session_id: str) -> AsyncIterator[Tx]:
        live = self._get(session_id)
        async with live.lock:
            transaction = self.db.transaction() if self.db is not None else nullcontext(None)
            async with transaction as conn:
                tx = Tx(live, conn, self.db, self.catalogue.by_id.__contains__)
                yield tx
                if not tx.events:
                    return
                view = self._build(tx.state, live.view.version + 1, live.view, tx.events)
                if conn is not None:
                    await self._persist(tx, view, expected_version=live.view.version)
            live.state, live.view = tx.state, view
            live.seen.update(e.idem_key for e in tx.events)
        await self._published(session_id, tx.events)

    def _spawn(self, work) -> asyncio.Task:
        """Run `work` in the background; `idle()` and `close()` keep track of it."""
        task = asyncio.create_task(work)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def idle(self) -> None:
        """Wait for the subscribers already running (tests, and a clean shutdown)."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)
            # gather finishes without yielding when every task is already done (Python 3.12), so give the
            # finished tasks their turn to leave the set.
            await asyncio.sleep(0)

    async def close(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    def _get(self, session_id: str) -> _Live:
        live = self._live.get(session_id)
        if live is None:
            raise UnknownSession(session_id)
        return live

    def _build(self, state: SessionState, version: int, before: SessionView | None = None,
               events: Sequence[Event] = ()) -> SessionView:
        """The board for `state`, with the Narrator's line for what changed since `before`, and the
        results text the first time the calls are all done."""
        view = board.build(state, version, self.catalogue, self.matrix)
        state.board = [c.listing_id for c in view.board]
        if state.phase == "done" and state.results is None:
            state.results = narrator.results(state, view, self.catalogue)
        says = narrator.line(before, view, events, state, self.catalogue)
        if says is None and before is not None:
            says = before.agent_says
        return view.model_copy(update={"agent_says": says or view.agent_says, "results": state.results,
                                       "returning": state.returning, "user_name": state.user_name})

    async def _recall(self, event: SessionStarted) -> Remembered | None:
        """The profile for a phone call's number. Any failure, or a slow database, starts the call fresh."""
        if self.profiles is None or event.channel != "phone" or not event.caller_phone:
            return None
        try:
            async with asyncio.timeout(RECALL_TIMEOUT_S):
                return await self.profiles.recall(event.caller_phone)
        except Exception:
            log.exception("could not recall the caller for %s; starting fresh", event.session_id)
            return None

    def _returning(self, state: SessionState, r: Remembered) -> None:
        """Start the session from what this number told us before."""
        state.user_id = r.user_id
        if not r.returning:
            return
        state.returning, state.user_name = True, r.name
        state.preferences = r.preferences
        state.reactions, state.recalled = dict(r.reactions), dict(r.reactions)
        state.previous_results = None if r.calls_running else r.last_results
        new = 0
        if r.last_seen_at is not None:
            fits, by_id = rank(self.matrix, r.preferences).order, self.catalogue.by_id
            new = sum(by_id[lid].first_seen > r.last_seen_at for lid in fits)
        state.opening = opening(r, new, datetime.now(UTC))

    async def _resolve_places(self, event: Event, view: SessionView) -> None:
        for anchor in view.preferences.anchors:
            if anchor.lat is not None:
                continue
            try:
                point = await asyncio.wait_for(self.places.resolve(anchor.label), PLACE_TIMEOUT_S)
            except TimeoutError:
                point = None
            if point is None:
                log.info("place %r stays unresolved for %s", anchor.label, event.session_id)
                continue
            if point.line and not self.matrix.has_line(point.lat, point.lng):
                # A street (#102): every listing's distance to its nearest point, worked out off the event
                # loop, before the anchor resolves and the board re-ranks around it.
                distances = await asyncio.to_thread(self.matrix.line_distances_km, point.line)
                self.matrix.use_line(point.lat, point.lng, distances)
            key = " ".join(anchor.label.lower().split())
            await self.emit(AnchorResolved(session_id=event.session_id, at=datetime.now(UTC),
                                           idem_key=f"{event.session_id}:place:{key}", label=anchor.label,
                                           lat=point.lat, lng=point.lng))

    async def _remember(self, event: Event, view: SessionView) -> None:
        if self.profiles is not None and self.has(event.session_id):
            state = self.state(event.session_id)
            await self.profiles.remember(state, event.type, self._address)

    def _address(self, listing_id: str) -> str:
        listing = self.catalogue.by_id.get(listing_id)
        return listing.address if listing else listing_id

    async def _persist(self, tx: Tx, view: SessionView, expected_version: int) -> None:
        db, conn, state = self.db, tx.conn, tx.state
        assert db is not None
        for event in tx.events:
            if await db.events.append(event, conn=conn) is None:
                raise DuplicateEvent(event.idem_key)
        await db.sessions.save(state.session_id, expected_version=expected_version, status=state.phase,
                               snapshot=_snapshot(state, view), conn=conn)
        if state.phase in ("done", "closed"):
            await db.sessions.end(state.session_id, state.phase, conn=conn)

    async def _published(self, session_id: str, events: list[Event]) -> None:
        async with self.changed:
            self.changed.notify_all()
        view = self.get(session_id)
        for event in events:
            for fn in self._subscribers.get(event.type, ()):
                self._spawn(self._run(fn, event, view))

    @staticmethod
    async def _run(fn: Subscriber, event: Event, view: SessionView) -> None:
        try:
            await fn(event, view)
        except Exception:
            log.exception("subscriber %s failed on %s %s", getattr(fn, "__qualname__", fn), event.type,
                          event.idem_key)


def _snapshot(state: SessionState, view: SessionView) -> dict:
    """sessions.snapshot: the state to reload on restart, and the view the page reads."""
    return {"state": state.model_dump(mode="json"), "view": view.model_dump(mode="json")}
