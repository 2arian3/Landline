"""What every repository shares: a pool, and an optional connection to run inside.

Pass `conn` to run a call inside a transaction the caller holds (O-02's
`session_tx`); without it a connection is borrowed from the pool and committed
when the call returns.
"""
from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import Any, TypeVar

from psycopg import AsyncConnection
from psycopg.rows import class_row
from psycopg_pool import AsyncConnectionPool

T = TypeVar("T")
Conn = AsyncConnection | None   # every repository method's optional `conn`


class Repo:
    def __init__(self, pool: AsyncConnectionPool):
        self._pool = pool

    @asynccontextmanager
    async def _use(self, conn: AsyncConnection | None) -> AsyncIterator[AsyncConnection]:
        if conn is not None:
            yield conn
        else:
            async with self._pool.connection() as c:
                yield c

    async def _one(self, row: type[T], query: str, params: Sequence[Any] | dict | None = None,
                   conn: AsyncConnection | None = None) -> T | None:
        async with self._use(conn) as c, c.cursor(row_factory=class_row(row)) as cur:
            await cur.execute(query, params)
            return await cur.fetchone()

    async def _all(self, row: type[T], query: str, params: Sequence[Any] | dict | None = None,
                   conn: AsyncConnection | None = None) -> list[T]:
        async with self._use(conn) as c, c.cursor(row_factory=class_row(row)) as cur:
            await cur.execute(query, params)
            return await cur.fetchall()

    async def _count(self, query: str, params: Sequence[Any] | dict | None = None,
                     conn: AsyncConnection | None = None) -> int:
        """Run a write and return how many rows it touched."""
        async with self._use(conn) as c, c.cursor() as cur:
            await cur.execute(query, params)
            return cur.rowcount
