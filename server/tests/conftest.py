"""Fixtures shared by the tests that need Postgres (test_db.py, test_orchestrator.py).

`schema` creates a throwaway schema `t_<random>` in TEST_DATABASE_URL, applies the
migrations into it, and drops it afterwards. Without a reachable TEST_DATABASE_URL
every test that asks for it skips.

On Windows the tests run on the selector event loop: psycopg's async pool can't use the default
Proactor loop, and every Postgres test there timed out opening it. CI (Linux) is untouched.
"""
import asyncio
import os
import secrets
import sys

import psycopg
import pytest
from psycopg import sql

from landline.db.migrations import apply
from landline.db.pool import Database

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

URL = os.environ.get("TEST_DATABASE_URL")


@pytest.fixture(scope="module")
def schema():
    if not URL:
        pytest.skip("TEST_DATABASE_URL is not set")
    try:
        conn = psycopg.connect(URL, autocommit=True, connect_timeout=3)
    except psycopg.OperationalError as e:
        pytest.skip(f"no database at TEST_DATABASE_URL: {e}")
    name = f"t_{secrets.token_hex(4)}"
    with conn:
        conn.execute("create extension if not exists postgis with schema public")
        conn.execute("create extension if not exists vector with schema public")
        conn.execute(sql.SQL("create schema {}").format(sql.Identifier(name)))
        try:
            yield name, apply(conn, schema=name)
        finally:
            conn.execute(sql.SQL("drop schema {} cascade").format(sql.Identifier(name)))


@pytest.fixture
async def db(schema):
    database = await Database.open(URL, schema=schema[0], max_size=4)
    yield database
    await database.close()
