"""Repositories for the session runtime: sessions, events, calls, turns, call audio, facts and boards.

Ids come from the caller (code mints them, never a model), and times default to
now() in SQL. Two rules live here because they must hold whoever calls:
a duplicate `idem_key` appends nothing, and a call ends exactly once.

`board_messages` from 0001_init.sql has no repository: nothing writes it since the
two-flow contracts (docs/CONTRACTS.md, "Where it is stored").
"""
from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Any

from psycopg.types.json import Jsonb

from landline.contracts import EventBase
from landline.db.base import Conn, Repo
from landline.db.rows import (
    AudioRow,
    AvatarClip,
    AvatarFile,
    AvatarRow,
    BoardEntry,
    BoardRow,
    CallRow,
    EventRow,
    FactRow,
    SessionRow,
    TurnRow,
    ViewingConfirmationRow,
)


class StaleVersion(Exception):
    """Someone saved the session since it was read: reload and apply again."""


class Sessions(Repo):
    async def create(self, session_id: str, channel: str, user_id: int | None = None,
                     conn: Conn = None) -> SessionRow:
        row = await self._one(SessionRow, "insert into sessions (session_id, channel, user_id) "
                              "values (%s, %s, %s) returning *", (session_id, channel, user_id), conn)
        assert row is not None
        return row

    async def get(self, session_id: str, conn: Conn = None) -> SessionRow | None:
        return await self._one(SessionRow, "select * from sessions where session_id = %s",
                               (session_id,), conn)

    async def link_user(self, session_id: str, user_id: int, conn: Conn = None) -> bool:
        """The renter a phone session belongs to, found by number once its brief ends."""
        n = await self._count("update sessions set user_id = %s where session_id = %s", (user_id, session_id),
                              conn)
        return n == 1

    async def latest_for_user(self, user_id: int, conn: Conn = None) -> SessionRow | None:
        """The user's most recent session: what their next call opens with."""
        return await self._one(SessionRow, "select * from sessions where user_id = %s "
                               "order by started_at desc, session_id desc limit 1", (user_id,), conn)

    async def list_active(self, conn: Conn = None) -> list[SessionRow]:
        """Open and calling sessions, oldest first: what a restart reloads."""
        return await self._all(SessionRow, "select * from sessions where status in ('open', 'calling') "
                               "order by started_at, session_id", None, conn)

    async def save(self, session_id: str, *, expected_version: int, status: str, snapshot: dict[str, Any],
                   conn: Conn = None) -> SessionRow:
        """Write the phase and snapshot and bump the version, if nobody saved since `expected_version`."""
        row = await self._one(
            SessionRow,
            "update sessions set status = %s, snapshot = %s, version = version + 1 "
            "where session_id = %s and version = %s returning *",
            (status, Jsonb(snapshot), session_id, expected_version), conn,
        )
        if row is None:
            raise StaleVersion(f"{session_id} is no longer at version {expected_version}")
        return row

    async def end(self, session_id: str, status: str, conn: Conn = None) -> bool:
        """Set the final phase (done or closed) once. False if it had already ended."""
        n = await self._count("update sessions set status = %s, ended_at = now() "
                              "where session_id = %s and ended_at is null", (status, session_id), conn)
        return n == 1


class Events(Repo):
    async def append(self, event: EventBase, conn: Conn = None) -> EventRow | None:
        """Store a contract event. None when its idem_key is already stored: the retry is a no-op."""
        payload = event.model_dump(mode="json", by_alias=True)
        outcome = getattr(event, "outcome", None)   # OutcomeReady carries its call inside the outcome
        call_id = getattr(event, "call_id", None) or (outcome.call_id if outcome else None)
        return await self._one(
            EventRow,
            "insert into events (session_id, call_id, type, idem_key, payload, at) "
            "values (%s, %s, %s, %s, %s, %s) on conflict (idem_key) do nothing returning *",
            (event.session_id, call_id, payload["type"], event.idem_key, Jsonb(payload), event.at), conn,
        )

    async def seen(self, idem_key: str, conn: Conn = None) -> bool:
        async with self._use(conn) as c:
            cur = await c.execute("select 1 from events where idem_key = %s", (idem_key,))
            return await cur.fetchone() is not None

    async def list(self, session_id: str, after_seq: int = 0, conn: Conn = None) -> list[EventRow]:
        return await self._all(EventRow, "select * from events where session_id = %s and seq > %s "
                               "order by seq", (session_id, after_seq), conn)


class Calls(Repo):
    async def create(self, call_id: str, *, session_id: str | None, role: str, transport: str,
                     state: str, listing_id: str | None = None, deadline_at: datetime | None = None,
                     attempt: int = 1, conn: Conn = None) -> CallRow:
        row = await self._one(
            CallRow,
            "insert into calls (call_id, session_id, listing_id, role, transport, state, deadline_at, "
            "attempt) values (%s, %s, %s, %s, %s, %s, %s, %s) returning *",
            (call_id, session_id, listing_id, role, transport, state, deadline_at, attempt), conn,
        )
        assert row is not None
        return row

    async def get(self, call_id: str, conn: Conn = None) -> CallRow | None:
        return await self._one(CallRow, "select * from calls where call_id = %s", (call_id,), conn)

    async def set_state(self, call_id: str, state: str, *, twilio_sid: str | None = None,
                        to_e164: str | None = None, answered: bool = False,
                        conn: Conn = None) -> CallRow | None:
        """Move a call that hasn't ended. None if it has: an ended call never changes again. `to_e164` is the
        number a listing call dialled (A-13a), stored as it starts dialling."""
        return await self._one(
            CallRow,
            "update calls set state = %s, twilio_sid = coalesce(%s, twilio_sid), "
            "to_e164 = coalesce(%s, to_e164), "
            "answered_at = case when %s then coalesce(answered_at, now()) else answered_at end "
            "where call_id = %s and ended_at is null returning *",
            (state, twilio_sid, to_e164, answered, call_id), conn,
        )

    async def finalize(self, call_id: str, reason: str, conn: Conn = None) -> bool:
        """End the call with `reason`, the first time only. True if this was the call that ended it."""
        n = await self._count("update calls set state = 'ended', end_reason = %s, ended_at = now() "
                              "where call_id = %s and ended_at is null", (reason, call_id), conn)
        return n == 1

    async def for_session(self, session_id: str, conn: Conn = None) -> list[CallRow]:
        return await self._all(CallRow, "select * from calls where session_id = %s "
                               "order by created_at, call_id", (session_id,), conn)

    async def in_state(self, states: Iterable[str], conn: Conn = None) -> list[CallRow]:
        """Calls in any of `states` that haven't ended, oldest first (e.g. queued ones to re-queue)."""
        return await self._all(CallRow, "select * from calls where state = any(%s) and ended_at is null "
                               "order by created_at, call_id", (list(states),), conn)

    async def past_deadline(self, now: datetime | None = None, conn: Conn = None) -> list[CallRow]:
        """Calls still open after their deadline: a restart finalizes them as timeout."""
        return await self._all(CallRow, "select * from calls where ended_at is null and deadline_at < "
                               "coalesce(%s, now()) order by deadline_at", (now,), conn)


class Turns(Repo):
    async def append(self, call_id: str, idx: int, speaker: str, text: str, lang: str | None = None,
                     t_ms: int | None = None, audio_start_ms: int | None = None,
                     audio_end_ms: int | None = None, conn: Conn = None) -> TurnRow | None:
        """Store one turn. None if that idx is already stored (a retried write)."""
        return await self._one(
            TurnRow,
            "insert into call_turns (call_id, idx, speaker, text, lang, t_ms, audio_start_ms, audio_end_ms) "
            "values (%s, %s, %s, %s, %s, %s, %s, %s) on conflict (call_id, idx) do nothing returning *",
            (call_id, idx, speaker, text, lang, t_ms, audio_start_ms, audio_end_ms), conn,
        )

    async def place(self, call_id: str, idx: int, audio_start_ms: int | None, audio_end_ms: int | None,
                    conn: Conn = None) -> bool:
        """Move a stored turn to where it really is in the call's audio, e.g. cut short by a barge-in."""
        n = await self._count("update call_turns set audio_start_ms = %s, audio_end_ms = %s "
                              "where call_id = %s and idx = %s",
                              (audio_start_ms, audio_end_ms, call_id, idx), conn)
        return n == 1

    async def list(self, call_id: str, after_idx: int = -1, conn: Conn = None) -> list[TurnRow]:
        """The call's turns after `after_idx`, in order: live captions poll with the last idx they have."""
        return await self._all(TurnRow, "select * from call_turns where call_id = %s and idx > %s "
                               "order by idx", (call_id, after_idx), conn)


class Audio(Repo):
    """call_audio: each side of a call's audio, in chunks on the call's clock."""

    async def append(self, call_id: str, track: str, seq: int, start_ms: int, fmt: str, rate: int,
                     audio: bytes, conn: Conn = None) -> bool:
        """Store one chunk. False if that (track, seq) is already stored (a retried write)."""
        n = await self._count(
            "insert into call_audio (call_id, track, seq, start_ms, format, rate, audio) "
            "values (%s, %s, %s, %s, %s, %s, %s) on conflict (call_id, track, seq) do nothing",
            (call_id, track, seq, start_ms, fmt, rate, audio), conn,
        )
        return n == 1

    async def list(self, call_id: str, from_ms: int | None = None, to_ms: int | None = None,
                   conn: Conn = None) -> list[AudioRow]:
        """The call's chunks that overlap [from_ms, to_ms) (all of them by default), by track and order."""
        # A chunk lasts octet_length / (rate × bytes a sample) seconds: 1 byte for μ-law, 2 for PCM16.
        return await self._all(
            AudioRow,
            "select * from call_audio where call_id = %(call_id)s "
            "and (%(to)s::int is null or start_ms < %(to)s) "
            "and (%(from)s::int is null or start_ms + octet_length(audio) * 1000 "
            "     / (rate * case format when 'pcmu' then 1 else 2 end) > %(from)s) "
            "order by track, seq",
            {"call_id": call_id, "from": from_ms, "to": to_ms}, conn,
        )

    async def exists(self, call_id: str, conn: Conn = None) -> bool:
        async with self._use(conn) as c, c.cursor() as cur:
            await cur.execute("select exists (select 1 from call_audio where call_id = %s)", (call_id,))
            row = await cur.fetchone()
            return bool(row and row[0])


class ViewingConfirmations(Repo):
    """O-08: one row per booked listing call, found by the token in the link the listing agent is texted."""

    async def create(self, token: str, *, call_id: str, session_id: str, listing_id: str, starts_at: datetime,
                     conn: Conn = None) -> ViewingConfirmationRow | None:
        """The call's confirmation, pending. None when the call already has one: one row per call."""
        return await self._one(
            ViewingConfirmationRow,
            "insert into viewing_confirmations (token, call_id, session_id, listing_id, starts_at) "
            "values (%s, %s, %s, %s, %s) on conflict (call_id) do nothing returning *",
            (token, call_id, session_id, listing_id, starts_at), conn)

    async def for_call(self, call_id: str, conn: Conn = None) -> ViewingConfirmationRow | None:
        return await self._one(ViewingConfirmationRow,
                               "select * from viewing_confirmations where call_id = %s", (call_id,), conn)

    async def get(self, token: str, conn: Conn = None) -> ViewingConfirmationRow | None:
        return await self._one(ViewingConfirmationRow,
                               "select * from viewing_confirmations where token = %s", (token,), conn)

    async def decide(self, token: str, *, status: str, agent_email: str | None = None,
                     conn: Conn = None) -> ViewingConfirmationRow | None:
        """O-08c: the agent's answer, once. One update that only a pending row matches, so two answers at
        the same moment can't both win. None when the token is unknown or already decided."""
        return await self._one(
            ViewingConfirmationRow,
            "update viewing_confirmations set status = %s, agent_email = coalesce(%s, agent_email), "
            "decided_at = now() where token = %s and status = 'pending' returning *",
            (status, agent_email, token), conn)

    async def set_event(self, token: str, event_id: str, conn: Conn = None) -> None:
        async with self._use(conn) as c:
            await c.execute("update viewing_confirmations set calendar_event_id = %s where token = %s",
                            (event_id, token))


class Avatars(Repo):
    """call_avatar: one Higgs Avatar MP4 of Landline's voice per call, rendered after the call."""

    async def claim(self, call_id: str, conn: Conn = None) -> bool:
        """Start this call's clip. False when a row is already there: a second ask does nothing."""
        n = await self._count(
            "insert into call_avatar (call_id, status) values (%s, 'queued') "
            "on conflict (call_id) do nothing",
            (call_id,), conn,
        )
        return n == 1

    async def save(self, call_id: str, *, status: str, boson_video_id: str | None = None,
                   error: str | None = None, video: bytes | None = None, conn: Conn = None) -> None:
        """Move the clip on. A missing id or MP4 leaves the one already stored."""
        await self._count(
            "update call_avatar set status = %s, boson_video_id = coalesce(%s, boson_video_id), "
            "error = %s, video = coalesce(%s, video), updated_at = now() where call_id = %s",
            (status, boson_video_id, error, video, call_id), conn,
        )

    async def get(self, call_id: str, conn: Conn = None) -> AvatarRow | None:
        return await self._one(
            AvatarRow,
            "select call_id, status, boson_video_id, error, updated_at from call_avatar where call_id = %s",
            (call_id,), conn,
        )

    async def file(self, call_id: str, session_id: str | None = None, conn: Conn = None) -> AvatarFile | None:
        """The MP4 when this call has one. With `session_id`, only if the call belongs to that session."""
        if session_id is None:
            return await self._one(AvatarFile, "select status, video from call_avatar where call_id = %s",
                                   (call_id,), conn)
        return await self._one(
            AvatarFile,
            "select a.status, a.video from call_avatar a join calls c using (call_id) "
            "where a.call_id = %s and c.session_id = %s",
            (call_id, session_id), conn,
        )

    async def for_session(self, session_id: str, conn: Conn = None) -> list[AvatarClip]:
        """The session's clips, the renter's call first, then listing calls in the order they were made."""
        return await self._all(
            AvatarClip,
            "select c.call_id, c.role, c.listing_id, a.status from calls c "
            "join call_avatar a using (call_id) where c.session_id = %s "
            "order by case when c.role = 'renter' then 0 else 1 end, c.created_at, c.call_id",
            (session_id,), conn,
        )


class Facts(Repo):
    async def add(self, *, listing_id: str, field: str, value: Any, quote: str, source: str,
                  call_id: str | None = None, turn_idx: int | None = None, confirmed: bool = False,
                  conn: Conn = None) -> FactRow:
        row = await self._one(
            FactRow,
            "insert into facts (call_id, listing_id, field, value, quote, turn_idx, confirmed, source) "
            "values (%s, %s, %s, %s, %s, %s, %s, %s) returning *",
            (call_id, listing_id, field, Jsonb(value), quote, turn_idx, confirmed, source), conn,
        )
        assert row is not None
        return row

    async def for_call(self, call_id: str, conn: Conn = None) -> list[FactRow]:
        return await self._all(FactRow, "select * from facts where call_id = %s order by fact_id",
                               (call_id,), conn)

    async def for_listing(self, listing_id: str, conn: Conn = None) -> list[FactRow]:
        return await self._all(FactRow, "select * from facts where listing_id = %s order by fact_id",
                               (listing_id,), conn)


class Boards(Repo):
    """session_listings: each session's ranked board, replaced whole on every re-rank."""

    async def replace(self, session_id: str, entries: list[BoardEntry], conn: Conn = None) -> None:
        async with self._use(conn) as c, c.transaction(), c.cursor() as cur:
            await cur.execute("delete from session_listings where session_id = %s", (session_id,))
            await cur.executemany(
                "insert into session_listings (session_id, listing_id, rank, score, contributions, status, "
                "reaction, reaction_reason) values (%s, %s, %s, %s, %s, %s, %s, %s)",
                [(session_id, e.listing_id, e.rank, e.score,
                  Jsonb(e.contributions) if e.contributions is not None else None, e.status, e.reaction,
                  e.reaction_reason) for e in entries],
            )

    async def list(self, session_id: str, conn: Conn = None) -> list[BoardRow]:
        return await self._all(BoardRow, "select * from session_listings where session_id = %s "
                               "order by rank nulls last, listing_id", (session_id,), conn)
