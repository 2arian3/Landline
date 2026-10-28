"""Fixtures shared by the tests that need Postgres (test_db.py, test_orchestrator.py), and Clerk session
tokens signed with a throwaway RSA key (`clerk`, test_me_signin.py and test_admin.py).

`schema` creates a throwaway schema `t_<random>` in TEST_DATABASE_URL, applies the
migrations into it, and drops it afterwards. Without a reachable TEST_DATABASE_URL
every test that asks for it skips.

On Windows the tests run on the selector event loop: psycopg's async pool can't use the default
Proactor loop, and every Postgres test there timed out opening it. CI (Linux) is untouched.

Tests never send traces to Logfire, even with LOGFIRE_TOKEN in .env: the empty value set here wins over
.env's, so app.py never configures it.
"""
import asyncio
import os
import secrets
import sys
import time

import jwt
import psycopg
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from psycopg import sql

from landline.db.migrations import apply
from landline.db.pool import Database

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

os.environ["LOGFIRE_TOKEN"] = ""

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


class ClerkKeys:
    """A throwaway Clerk instance: `pem` is the public key the API is given as CLERK_JWT_KEY, and `token()`
    signs a session token with the matching private key. Never a real key."""

    WEB = "http://localhost:3000"

    def __init__(self) -> None:
        self._key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.pem = self._key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()

    def token(self, *, phone: str | None = "+14165550123", azp: str | None = WEB, expires_in: int = 60,
              key: rsa.RSAPrivateKey | None = None) -> str:
        now = int(time.time())
        claims = {"sub": "user_test", "sid": "sess_test", "iat": now, "nbf": now, "exp": now + expires_in,
                  "azp": azp, "phone": phone}
        claims = {k: v for k, v in claims.items() if v is not None}
        return jwt.encode(claims, key or self._key, algorithm="RS256")


@pytest.fixture(scope="session")
def clerk() -> ClerkKeys:
    return ClerkKeys()
