"""The stored record of each renter call: its `calls` row, its transcript in `call_turns`, and its audio in
`call_audio` (A-10, recorded by voice/recorder.py).

    runtime.calls.started(call_id, session_id, "voice", deadline_at=...)   # when the call connects
    idx = runtime.calls.said(call_id, "renter", "A one-bedroom near Bloor", start_ms=1236, end_ms=4500)
    runtime.calls.audio(call_id, "renter", 0, b"...", fmt="pcmu", rate=8000)
    runtime.calls.placed(call_id, idx, 1236, 4100)   # a line cut short after it was stored
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
from datetime import datetime

from landline.db.pool import Database

log = logging.getLogger(__name__)

Spawn = Callable[[Awaitable[None]], asyncio.Task]
SPEAKERS = ("renter", "landlord", "agent")


class CallLog:
    def __init__(self, db: Database | None, spawn: Spawn):
        self.db, self._spawn = db, spawn
        self._last: dict[str, asyncio.Task] = {}     # each call's latest write, which the next one waits for
        self._next_idx: dict[str, int] = {}
        self._next_seq: dict[tuple[str, str], int] = {}   # (call_id, track) -> the next chunk's seq
        self._started_at: dict[str, float] = {}

    def started(self, call_id: str, session_id: str, transport: str,
                deadline_at: datetime | None = None) -> None:
        self._next_idx[call_id], self._started_at[call_id] = 0, time.monotonic()
        self._then(call_id, lambda db: db.calls.create(call_id, session_id=session_id, role="renter",
                                                       transport=transport, state="live",
                                                       deadline_at=deadline_at))

    def said(self, call_id: str, speaker: str, text: str, *, start_ms: int | None = None,
             end_ms: int | None = None) -> int | None:
        """Stores a line, with where it is in the call's audio when known. Returns its idx, or None when
        nothing was stored (nothing said, or a call this log doesn't know)."""
        text = text.strip()
        if call_id not in self._next_idx or speaker not in SPEAKERS or not text:
            return None
        idx = self._next_idx[call_id]
        self._next_idx[call_id] = idx + 1
        t_ms = int((time.monotonic() - self._started_at[call_id]) * 1000)
        self._then(call_id, lambda db: db.turns.append(call_id, idx, speaker, text, t_ms=t_ms,
                                                       audio_start_ms=start_ms, audio_end_ms=end_ms))
        return idx

    def placed(self, call_id: str, idx: int, start_ms: int | None, end_ms: int | None) -> None:
        """A stored line's place in the audio changed: an agent line a barge-in cut short."""
        if call_id in self._next_idx:
            self._then(call_id, lambda db: db.turns.place(call_id, idx, start_ms, end_ms))

    def audio(self, call_id: str, track: str, start_ms: int, audio: bytes, *, fmt: str, rate: int) -> None:
        """Stores a chunk of one side of the call, `start_ms` into it. Chunks of a track keep their order."""
        if call_id not in self._next_idx or track not in SPEAKERS or not audio:
            return
        seq = self._next_seq.get((call_id, track), 0)
        self._next_seq[call_id, track] = seq + 1
        self._then(call_id, lambda db: db.audio.append(call_id, track, seq, start_ms, fmt, rate, audio))

    def ended(self, call_id: str, reason: str) -> None:
        if call_id not in self._next_idx:
            return
        self._then(call_id, lambda db: db.calls.finalize(call_id, reason), last=True)

    async def flushed(self, call_id: str) -> None:
        """Wait until this call's queued writes, including the one `ended` just added, have landed."""
        task = self._last.get(call_id)
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)

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
        for key in [k for k in self._next_seq if k[0] == call_id]:
            del self._next_seq[key]
