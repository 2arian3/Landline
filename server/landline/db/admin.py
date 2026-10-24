"""Read-only queries for the admin panel: every renter, their sessions, calls and transcripts.

Nothing here writes. Rows come back as dicts and the API shapes them (server/landline/api/admin.py).
"""
from __future__ import annotations

from typing import Any

from psycopg.rows import dict_row

from landline.db.base import Conn, Repo

MAX_SESSIONS = 50   # per renter, newest first


class Admin(Repo):
    async def users(self, user_id: int | None = None, conn: Conn = None) -> list[dict[str, Any]]:
        """Every renter (or just `user_id`), most recently seen first, with counts of what they did."""
        return await self._dicts("""
            select u.user_id, u.phone_e164, u.name, u.language, u.consent_memory, u.created_at,
              u.last_seen_at, p.memory_summary,
              (select count(*) from sessions s where s.user_id = u.user_id) as sessions,
              (select count(*) from calls c join sessions s using (session_id)
                 where s.user_id = u.user_id) as calls,
              (select count(*) from listing_interactions i
                 where i.user_id = u.user_id and i.action = 'booked') as booked,
              (select s.status from sessions s where s.user_id = u.user_id
                 order by s.started_at desc limit 1) as last_phase
            from users u left join user_profiles p using (user_id)
            where %(user_id)s::bigint is null or u.user_id = %(user_id)s
            order by coalesce(u.last_seen_at, u.created_at) desc, u.user_id desc
        """, {"user_id": user_id}, conn)

    async def user(self, user_id: int, conn: Conn = None) -> dict[str, Any] | None:
        rows = await self._dicts("""
            select u.*, p.hard, p.priorities, p.anchors, p.occupants, p.memory_summary,
              p.updated_at as profile_updated_at
            from users u left join user_profiles p using (user_id) where u.user_id = %s
        """, (user_id,), conn)
        return rows[0] if rows else None

    async def sessions(self, user_id: int, conn: Conn = None) -> list[dict[str, Any]]:
        return await self._dicts("""
            select session_id, channel, status, version, snapshot, started_at, ended_at from sessions
            where user_id = %s order by started_at desc limit %s
        """, (user_id, MAX_SESSIONS), conn)

    async def calls(self, session_ids: list[str], conn: Conn = None) -> list[dict[str, Any]]:
        """The calls of these sessions, oldest first, with how many transcript lines each has."""
        return await self._dicts("""
            select c.*, (select count(*) from call_turns t where t.call_id = c.call_id) as turns
            from calls c where c.session_id = any(%s) order by c.created_at, c.call_id
        """, (session_ids,), conn)

    async def interactions(self, user_id: int, conn: Conn = None) -> list[dict[str, Any]]:
        return await self._dicts("""
            select listing_id, session_id, action, reason, attribute, at from listing_interactions
            where user_id = %s order by at desc
        """, (user_id,), conn)

    async def call(self, call_id: str, conn: Conn = None) -> dict[str, Any] | None:
        rows = await self._dicts("""
            select c.*, s.user_id from calls c left join sessions s using (session_id) where c.call_id = %s
        """, (call_id,), conn)
        return rows[0] if rows else None

    async def _dicts(self, query: str, params: Any, conn: Conn) -> list[dict[str, Any]]:
        async with self._use(conn) as c, c.cursor(row_factory=dict_row) as cur:
            await cur.execute(query, params)
            return await cur.fetchall()
