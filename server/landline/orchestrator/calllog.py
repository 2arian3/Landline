"""The stored record of each renter call: its `calls` row and its transcript in `call_turns`.

    runtime.calls.started(call_id, session_id, "voice")   # when the call connects
    runtime.calls.said(call_id, "renter", "A one-bedroom near Bloor")
    runtime.calls.ended(call_id, "hangup")

The voice layer reports; this writes. Writes run in the background, one after another for each call,
so a slow database never holds up the audio and a line is never stored before its call. Without a
database it does nothing. The admin panel and the live captions read these rows back.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

from landline.db.pool import Database

log = logging.getLogger(__name__)

Spawn = Callable[[Awaitable[None]], asyncio.Task]
SPEAKERS = ("renter", "landlord", "agent")


class CallLog:
    def __init__(self, db: Database | None, spawn: Spawn):
        self.db, self._spawn = db, spawn
        self._last: dict[str, asyncio.Task] = {}     # each call's latest write, which the next one waits for
        self._next_idx: dict[str, int] = {}
        self._started_at: dict[str, float] = {}

    def started(self, call_id: str, session_id: str, transport: str) -> None:
        self._next_idx[call_id], self._started_at[call_id] = 0, time.monotonic()
        self._then(call_id, lambda db: db.calls.create(call_id, session_id=session_id, role="renter",
                                                       transport=transport, state="live"))

    def said(self, call_id: str, speaker: str, text: str) -> None:
        text = text.strip()
        if call_id not in self._next_idx or speaker not in SPEAKERS or not text:
            return
        idx = self._next_idx[call_id]
        self._next_idx[call_id] = idx + 1
        t_ms = int((time.monotonic() - self._started_at[call_id]) * 1000)
        self._then(call_id, lambda db: db.turns.append(call_id, idx, speaker, text, t_ms=t_ms))

    def ended(self, call_id: str, reason: str) -> None:
        if call_id not in self._next_idx:
            return
        self._then(call_id, lambda db: db.calls.finalize(call_id, reason), last=True)

    def _then(self, call_id: str, write: Callable[[Database], Awaitable[object]], last: bool = False) -> None:
        if self.db is None:
            return
        before, db = self._last.get(call_id), self.db

        async def run() -> None:
            if before is not None:
                await asyncio.gather(before, return_exceptions=True)
            try:
                await write(db)
            except Exception:
                log.exception("could not store call %s", call_id)
            finally:
                if last:
                    self._forget(call_id)

        self._last[call_id] = self._spawn(run())

    def _forget(self, call_id: str) -> None:
        for book in (self._last, self._next_idx, self._started_at):
            book.pop(call_id, None)
