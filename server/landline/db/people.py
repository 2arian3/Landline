"""Repositories for renters: users by phone number, their profiles and what they did with listings.

`Users.forget()` is `forget_me`: it deletes the user, and with them their profile
and interactions (on delete cascade), and unlinks their sessions, which stay for
the event log. Scrubbing a phone number out of stored event payloads is O-05's call.
"""
from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from typing import Any

from psycopg.types.json import Jsonb

from landline.db.base import Conn, Repo
from landline.db.rows import InteractionRow, ProfileRow, UserRow

ACTIONS = ("viewed", "liked", "rejected", "called", "booked")


class Users(Repo):
    async def get(self, user_id: int, conn: Conn = None, *, lock: bool = False) -> UserRow | None:
        """`lock`: hold the row until the transaction ends (a read-modify-write, U-12a)."""
        return await self._one(UserRow, "select * from users where user_id = %s"
                               + (" for update" if lock else ""), (user_id,), conn)

    async def by_phone(self, phone_e164: str, conn: Conn = None) -> UserRow | None:
        return await self._one(UserRow, "select * from users where phone_e164 = %s", (phone_e164,), conn)

    async def upsert(self, phone_e164: str, *, name: str | None = None, language: str | None = None,
                     conn: Conn = None) -> UserRow:
        """The user for this number, created on the first call. None keeps the stored name or language."""
        row = await self._one(
            UserRow,
            "insert into users (phone_e164, name, language) values (%s, %s, %s) "
            "on conflict (phone_e164) do update set name = coalesce(excluded.name, users.name), "
            "language = coalesce(excluded.language, users.language) returning *",
            (phone_e164, name, language), conn,
        )
        assert row is not None
        return row

    async def set_consent(self, user_id: int, consent: bool, conn: Conn = None) -> bool:
        return await self._count("update users set consent_memory = %s where user_id = %s",
                                 (consent, user_id), conn) == 1

    async def set_settings(self, user_id: int, *, email: str | None, use_saved_search: bool,
                           conn: Conn = None) -> bool:
        """The renter's own settings from /me (U-12a): their email, and whether the next call starts from the
        saved search."""
        return await self._count("update users set email = %s, use_saved_search = %s where user_id = %s",
                                 (email, use_saved_search, user_id), conn) == 1

    async def touch(self, user_id: int, conn: Conn = None) -> bool:
        n = await self._count("update users set last_seen_at = now() where user_id = %s", (user_id,), conn)
        return n == 1

    async def forget(self, user_id: int, conn: Conn = None) -> bool:
        """Delete the user, their profile and interactions; their sessions stay, unlinked."""
        async with self._use(conn) as c, c.transaction():
            await c.execute("update sessions set user_id = null where user_id = %s", (user_id,))
            cur = await c.execute("delete from users where user_id = %s", (user_id,))
            return cur.rowcount == 1


class Profiles(Repo):
    async def get(self, user_id: int, conn: Conn = None, *, lock: bool = False) -> ProfileRow | None:
        """`lock`: hold the row until the transaction ends, so /me's edits and a call's end never overwrite
        each other."""
        return await self._one(ProfileRow, "select * from user_profiles where user_id = %s"
                               + (" for update" if lock else ""), (user_id,), conn)

    async def upsert(self, user_id: int, *, hard: dict[str, Any] | None = None,
                     priorities: list[Any] | None = None, anchors: list[Any] | None = None,
                     move_in: date | None = None, occupants: int | None = None,
                     shareable: Sequence[str] = ("move_in", "occupants", "pets"),
                     memory_summary: str | None = None, conn: Conn = None) -> ProfileRow:
        """Write the whole profile (O-05 rebuilds it at the end of each session)."""
        row = await self._one(
            ProfileRow,
            "insert into user_profiles (user_id, hard, priorities, anchors, move_in, occupants, shareable, "
            "memory_summary, updated_at) values (%s, %s, %s, %s, %s, %s, %s, %s, now()) "
            "on conflict (user_id) do update set hard = excluded.hard, priorities = excluded.priorities, "
            "anchors = excluded.anchors, move_in = excluded.move_in, occupants = excluded.occupants, "
            "shareable = excluded.shareable, memory_summary = excluded.memory_summary, updated_at = now() "
            "returning *",
            (user_id, Jsonb(hard or {}), Jsonb(priorities or []), Jsonb(anchors or []), move_in, occupants,
             list(shareable), memory_summary), conn,
        )
        assert row is not None
        return row


class Interactions(Repo):
    async def add(self, user_id: int, listing_id: str, action: str, *, session_id: str | None = None,
                  reason: str | None = None, attribute: str | None = None,
                  conn: Conn = None) -> InteractionRow:
        if action not in ACTIONS:
            raise ValueError(f"action must be one of {ACTIONS}, not {action!r}")
        row = await self._one(
            InteractionRow,
            "insert into listing_interactions (user_id, listing_id, session_id, action, reason, attribute) "
            "values (%s, %s, %s, %s, %s, %s) returning *",
            (user_id, listing_id, session_id, action, reason, attribute), conn,
        )
        assert row is not None
        return row

    async def for_user(self, user_id: int, since: datetime | None = None,
                       conn: Conn = None) -> list[InteractionRow]:
        """Most recent first; only those after `since` when given."""
        return await self._all(InteractionRow, "select * from listing_interactions where user_id = %s "
                               "and (%s::timestamptz is null or at > %s) order by at desc",
                               (user_id, since, since), conn)

    async def rejected(self, user_id: int, conn: Conn = None) -> set[str]:
        """Listings this renter turned down: the Matcher leaves them out."""
        async with self._use(conn) as c:
            cur = await c.execute("select distinct listing_id from listing_interactions "
                                  "where user_id = %s and action = 'rejected'", (user_id,))
            return {r[0] for r in await cur.fetchall()}
