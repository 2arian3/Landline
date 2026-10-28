"""The async connection pool and one repository per table.

    db = await Database.open(settings.database_url)
    session = await db.sessions.get("s_ab12cd34ef")
    async with db.transaction() as conn:          # O-02's session_tx: several writes, one commit
        await db.sessions.save(sid, expected_version=v, status="open", snapshot=snap, conn=conn)
        await db.events.append(event, conn=conn)
    await db.close()

With `schema`, every connection's search path is `<schema>, public`: the tests run
against a throwaway schema while PostGIS and pgvector stay in public.
"""
from __future__ import annotations

import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

from landline.db.admin import Admin
from landline.db.listings import IngestRuns, Listings
from landline.db.people import Interactions, Profiles, Users
from landline.db.reference import Buildings, Neighbourhoods, Stations
from landline.db.runtime import (
    Audio,
    Avatars,
    Boards,
    Calls,
    Events,
    Facts,
    Sessions,
    Turns,
    ViewingConfirmations,
)

_SCHEMA = re.compile(r"^[a-z_][a-z0-9_]*$")


class Database:
    def __init__(self, pool: AsyncConnectionPool):
        self.pool = pool
        # runtime
        self.sessions = Sessions(pool)
        self.events = Events(pool)
        self.calls = Calls(pool)
        self.turns = Turns(pool)
        self.audio = Audio(pool)              # call_audio
        self.avatars = Avatars(pool)          # call_avatar
        self.facts = Facts(pool)
        self.boards = Boards(pool)            # session_listings
        self.confirmations = ViewingConfirmations(pool)   # viewing_confirmations (O-08)
        # people
        self.users = Users(pool)
        self.profiles = Profiles(pool)        # user_profiles
        self.interactions = Interactions(pool)  # listing_interactions
        # catalogue
        self.listings = Listings(pool)
        self.ingest_runs = IngestRuns(pool)
        # reference
        self.neighbourhoods = Neighbourhoods(pool)
        self.stations = Stations(pool)
        self.buildings = Buildings(pool)
        # read-only, for the admin panel
        self.admin = Admin(pool)

    @classmethod
    async def open(cls, url: str, *, schema: str | None = None, min_size: int = 1, max_size: int = 10,
                   timeout: float = 10.0) -> Database:
        kwargs = {}
        if schema:
            if not _SCHEMA.match(schema):
                raise ValueError(f"not a schema name: {schema!r}")
            kwargs["options"] = f"-c search_path={schema},public"
        pool = AsyncConnectionPool(url, min_size=min_size, max_size=max_size, kwargs=kwargs, open=False,
                                   timeout=timeout)
        await pool.open(wait=True, timeout=timeout)
        return cls(pool)

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[AsyncConnection]:
        """One connection and one transaction: commits on exit, rolls back if the block raises."""
        async with self.pool.connection() as conn, conn.transaction():
            yield conn

    async def close(self) -> None:
        await self.pool.close()
