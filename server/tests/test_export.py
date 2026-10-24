"""The database copy behind `make db-pull` (X-00): exported from one throwaway schema and loaded into
another, every row, id, geometry and byte arrives, parents before children, and loading again replaces it.
The API hands it out only with the admin token. The Postgres tests skip without TEST_DATABASE_URL."""
import os
import secrets
import subprocess
import sys
import zlib
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg import sql

from landline.app import create_app
from landline.config import Settings
from landline.db.catalogue import Catalogue
from landline.db.export import AUDIO, export, lines, load, read_head
from landline.db.migrations import apply
from landline.db.pool import Database

ROOT = Path(__file__).resolve().parents[2]
URL = os.environ.get("TEST_DATABASE_URL")
TOKEN = "test-admin-token"
SOUND = bytes(range(256)) * 4            # every byte value, newline and backslash included
TABLES = ("neighbourhoods", "listings", "users", "sessions", "calls", "call_turns")


def _in(schema: str) -> str:
    return f"{URL}?options=-csearch_path%3D{schema},public"


@pytest.fixture(scope="module")
def source(schema):
    """The conftest schema, holding one of everything, with the awkward characters text format escapes."""
    with psycopg.connect(_in(schema[0]), autocommit=True) as c:
        c.execute("insert into neighbourhoods (id, name, geom) values (1, 'Annex', ST_Multi(ST_GeomFromText("
                  "'POLYGON((-79.41 43.66, -79.40 43.66, -79.40 43.67, -79.41 43.66))', 4326))::geography)")
        c.execute("insert into listings (listing_id, source, source_id, url, first_seen, last_seen, status, "
                  "address, rent, monthly_cost, beds, geom) values ('t:1', 't', '1', 'u', now(), now(), "
                  "'active', '1 Copy St', 2000, 2000, 1, "
                  "ST_SetSRID(ST_MakePoint(-79.405, 43.665), 4326)::geography)")
        for n in range(3):   # ids 1-3, so the sequence has somewhere to be
            c.execute("insert into users (phone_e164, name) values (%s, %s)",
                      (f"+1416555010{n}", "Tab\there, a \\ backslash"))
        c.execute("insert into sessions (session_id, user_id, channel, snapshot) "
                  "values ('s_copy000001', 3, 'phone', %s)", ('{"said": "two\\nlines"}',))
        c.execute("insert into calls (call_id, session_id, listing_id, role, transport, state) "
                  "values ('c_copy000001', 's_copy000001', 't:1', 'listing', 'sim', 'ended')")
        c.execute("insert into call_turns (call_id, idx, speaker, text) "
                  "values ('c_copy000001', 0, 'landlord', 'Line one\nline two')")
        c.execute("insert into call_audio (call_id, track, seq, start_ms, format, rate, audio) "
                  "values ('c_copy000001', 'landlord', 0, 0, 'pcmu', 8000, %s)", (SOUND,))
    return schema[0]


@pytest.fixture
def dest():
    """A second throwaway schema, migrated and empty but for one stale renter the copy must replace."""
    if not URL:
        pytest.skip("TEST_DATABASE_URL is not set")
    name = f"t_{secrets.token_hex(4)}"
    with psycopg.connect(URL, autocommit=True) as admin:
        admin.execute(sql.SQL("create schema {}").format(sql.Identifier(name)))
        try:
            apply(admin, schema=name)
            with psycopg.connect(_in(name), autocommit=True) as c:
                c.execute("insert into users (phone_e164) values ('+19995550000')")
                yield c
        finally:
            admin.execute(sql.SQL("drop schema {} cascade").format(sql.Identifier(name)))


async def _export(schema: str, audio: bool = False) -> list[bytes]:
    db = await Database.open(URL, schema=schema, max_size=1)
    try:
        async with db.pool.connection() as conn:
            return [chunk async for chunk in export(conn, audio=audio)]
    finally:
        await db.close()


def _load(conn, chunks: list[bytes]) -> tuple[dict, dict[str, int]]:
    stream = lines(chunks)
    head = read_head(stream)
    return head, load(conn, head, stream)


async def test_everything_arrives_as_it_was(source, dest):
    head, counts = _load(dest, await _export(source))
    order = [t["name"] for t in head["tables"]]
    assert order.index("users") < order.index("sessions") < order.index("calls") < order.index("call_turns")
    assert order.index("listings") < order.index("calls") and AUDIO not in order
    assert head["migrations"] == sorted(p.name for p in (ROOT / "db" / "migrations").glob("*.sql"))
    assert head["audio"] is False
    assert {t: counts[t] for t in TABLES} == {"neighbourhoods": 1, "listings": 1, "users": 3, "sessions": 1,
                                              "calls": 1, "call_turns": 1}
    assert dest.execute("select user_id, name from users order by 1").fetchall() == [
        (1, "Tab\there, a \\ backslash"), (2, "Tab\there, a \\ backslash"), (3, "Tab\there, a \\ backslash")]
    assert dest.execute("select text from call_turns").fetchone()[0] == "Line one\nline two"
    assert dest.execute("select snapshot from sessions").fetchone()[0] == {"said": "two\nlines"}
    assert dest.execute("select ST_AsText(geom) from listings").fetchone()[0] == "POINT(-79.405 43.665)"
    assert dest.execute("select round(ST_Area(geom)) > 0 from neighbourhoods").fetchone()[0]
    # The copied ids moved the sequence on: a new renter doesn't collide with renter 3.
    assert dest.execute("insert into users (phone_e164) values ('+14165550199') returning user_id"
                        ).fetchone()[0] == 4


async def test_recordings_come_only_when_asked_and_a_second_load_replaces_the_first(source, dest):
    chunks = await _export(source, audio=True)
    _load(dest, chunks)
    head, counts = _load(dest, chunks)
    assert head["audio"] is True and counts[AUDIO] == 1 and counts["users"] == 3
    assert dest.execute("select audio from call_audio").fetchone()[0] == SOUND
    assert dest.execute("select count(*) from users").fetchone()[0] == 3


def test_the_api_hands_it_out_only_with_the_admin_token(source):
    settings = Settings(database_url=_in(source), web_url="http://localhost:3000", transport="stub",
                        admin_token=TOKEN)
    with TestClient(create_app(settings, Catalogue([], "test"))) as c:
        assert c.get("/api/admin/export").status_code == 401
        resp = c.get("/api/admin/export", headers={"Authorization": f"Bearer {TOKEN}"})
        assert resp.status_code == 200 and resp.headers["content-type"] == "application/gzip"
        head = read_head(lines([resp.content]))
        assert {t["name"] for t in head["tables"]} >= set(TABLES)
    settings = Settings(database_url=_in(source), web_url="http://localhost:3000", transport="stub")
    with TestClient(create_app(settings, Catalogue([], "test"))) as c:   # no token: not there at all
        assert c.get("/api/admin/export", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 404


def test_lines_survive_any_chunking_and_a_stranger_is_refused():
    gz = zlib.compressobj(6, zlib.DEFLATED, 31)
    blob = gz.compress(b'{"format": "landline-export-v1"}\nCOPY "x" ("a") FROM stdin;\n1\n\\.\n') + gz.flush()
    for size in (1, 3, len(blob)):
        assert list(lines(blob[i:i + size] for i in range(0, len(blob), size)))[1:] == [
            b'COPY "x" ("a") FROM stdin;\n', b"1\n", b"\\.\n"]
    gz = zlib.compressobj(6, zlib.DEFLATED, 31)
    with pytest.raises(ValueError):
        read_head(lines([gz.compress(b'{"format": "pg_dump"}\n') + gz.flush()]))


def test_db_pull_loads_only_into_a_database_on_this_machine():
    env = {**os.environ, "ADMIN_TOKEN": "x", "PYTHONPATH": str(ROOT / "server")}
    out = subprocess.run([sys.executable, str(ROOT / "scripts" / "db_pull.py"), "--api", "http://127.0.0.1:9",
                          "--database-url", "postgresql://landline@db.example.com/landline"],
                         capture_output=True, text=True, env=env, timeout=60)
    assert out.returncode == 1 and "refusing to load into db.example.com" in out.stderr
