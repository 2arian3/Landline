"""Repository tests against a throwaway schema.

Run with TEST_DATABASE_URL pointing at a Postgres with PostGIS and pgvector available
(`make test` uses the local landline-db-1). The tests create a schema `t_<random>`,
apply the migrations into it, and drop it afterwards; the public schema is never
touched. Without a reachable TEST_DATABASE_URL they skip.
"""
import os
import secrets
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from psycopg import sql

from landline.contracts import CallOutcome, OutcomeReady, SessionStarted
from landline.db.migrations import apply
from landline.db.pool import Database
from landline.db.rows import BoardEntry
from landline.db.runtime import StaleVersion

URL = os.environ.get("TEST_DATABASE_URL")
T0 = datetime(2026, 10, 24, 1, 0, tzinfo=UTC)
SQUARE = {"type": "Polygon", "coordinates": [[[-79.40, 43.64], [-79.37, 43.64], [-79.37, 43.66],
                                              [-79.40, 43.66], [-79.40, 43.64]]]}


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


async def _listing(db: Database, lid: str, *, lat=43.65, lng=-79.38, first_seen=T0, status="active") -> str:
    async with db.transaction() as conn:
        await conn.execute(
            "insert into listings (listing_id, source, source_id, url, first_seen, last_seen, status, "
            "address, rent, monthly_cost, beds, geom, amenities) values (%s, 't', %s, 'https://example.com', "
            "%s, %s, %s, '1 Test St', 2000, 2000, 1, ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography, "
            "'{Elevator}')",
            (lid, lid, first_seen, first_seen, status, lng, lat),
        )
    return lid


def _started(sid: str, key: str = "start") -> SessionStarted:
    return SessionStarted(session_id=sid, at=T0, idem_key=f"{sid}:{key}", channel="phone")


# Migrations


def test_migrations_apply_once_then_do_nothing(schema):
    name, applied = schema
    assert applied == ["0001_init.sql"]
    with psycopg.connect(URL, autocommit=True) as conn:
        assert apply(conn, schema=name) == []


# Runtime


async def test_sessions_save_bumps_the_version_and_refuses_a_stale_one(db):
    s = await db.sessions.create("s_sess000001", "browser")
    assert (s.status, s.version, s.snapshot) == ("open", 0, {})
    saved = await db.sessions.save(s.session_id, expected_version=0, status="calling",
                                   snapshot={"plan": ["a"]})
    assert (saved.version, saved.status, saved.snapshot) == (1, "calling", {"plan": ["a"]})
    with pytest.raises(StaleVersion):
        await db.sessions.save(s.session_id, expected_version=0, status="open", snapshot={})
    assert s.session_id in {x.session_id for x in await db.sessions.list_active()}
    assert await db.sessions.end(s.session_id, "done") is True
    assert await db.sessions.end(s.session_id, "closed") is False     # ends once
    ended = await db.sessions.get(s.session_id)
    assert ended.status == "done" and ended.ended_at is not None
    assert s.session_id not in {x.session_id for x in await db.sessions.list_active()}


async def test_a_duplicate_event_is_a_no_op(db):
    sid = (await db.sessions.create("s_evnt000001", "phone")).session_id
    first = await db.events.append(_started(sid))
    assert first is not None and first.type == "SessionStarted"
    assert await db.events.append(_started(sid)) is None
    assert await db.events.seen(f"{sid}:start") and not await db.events.seen("nope")
    outcome = OutcomeReady(session_id=sid, at=T0, idem_key=f"{sid}:outcome",
                           outcome=CallOutcome(call_id="c_evnt000001", listing_id="t:1", end_reason="done"))
    stored = await db.events.append(outcome)
    assert stored.call_id == "c_evnt000001"                 # taken from inside the outcome
    rows = await db.events.list(sid)
    assert [r.type for r in rows] == ["SessionStarted", "OutcomeReady"]
    assert rows[0].event() == _started(sid)                  # round-trips to the contract event
    assert [r.type for r in await db.events.list(sid, after_seq=rows[0].seq)] == ["OutcomeReady"]


async def test_a_call_ends_exactly_once(db):
    sid = (await db.sessions.create("s_call000001", "phone")).session_id
    lid = await _listing(db, "t:call1")
    c = await db.calls.create("c_call000001", session_id=sid, role="listing", transport="sim",
                              state="queued", listing_id=lid, deadline_at=T0 - timedelta(minutes=1))
    assert c.state == "queued" and c.ended_at is None
    assert [x.call_id for x in await db.calls.in_state(["queued"])] == ["c_call000001"]
    live = await db.calls.set_state(c.call_id, "live", twilio_sid="CA123", answered=True)
    assert (live.state, live.twilio_sid) == ("live", "CA123") and live.answered_at is not None
    assert [x.call_id for x in await db.calls.past_deadline()] == ["c_call000001"]
    assert await db.calls.finalize(c.call_id, "done") is True
    assert await db.calls.finalize(c.call_id, "timeout") is False
    assert await db.calls.set_state(c.call_id, "live") is None     # an ended call never changes again
    ended = await db.calls.get(c.call_id)
    assert (ended.state, ended.end_reason) == ("ended", "done")
    assert await db.calls.past_deadline() == [] and await db.calls.in_state(["queued", "live"]) == []
    assert [x.call_id for x in await db.calls.for_session(sid)] == ["c_call000001"]


async def test_turns_and_facts(db):
    sid = (await db.sessions.create("s_turn000001", "phone")).session_id
    lid = await _listing(db, "t:turn1")
    cid = (await db.calls.create("c_turn000001", session_id=sid, role="listing", transport="stub",
                                 state="live", listing_id=lid)).call_id
    await db.turns.append(cid, 0, "agent", "Hi, I'm an AI assistant calling for a renter.")
    await db.turns.append(cid, 1, "landlord", "Parking is one eighty a month.", lang="en", t_ms=4200)
    assert await db.turns.append(cid, 1, "landlord", "retried") is None
    assert [t.text for t in await db.turns.list(cid)][1] == "Parking is one eighty a month."
    assert [t.idx for t in await db.turns.list(cid, after_idx=0)] == [1]
    fact = await db.facts.add(listing_id=lid, call_id=cid, field="parking_cost", value="$180 a month",
                              quote="Parking is one eighty a month.", source="tool", turn_idx=1,
                              confirmed=True)
    assert fact.value == "$180 a month" and fact.confirmed
    assert [f.fact_id for f in await db.facts.for_call(cid)] == [fact.fact_id]
    assert [f.field for f in await db.facts.for_listing(lid)] == ["parking_cost"]


async def test_a_board_is_replaced_whole(db):
    sid = (await db.sessions.create("s_brd0000001", "browser")).session_id
    a, b = await _listing(db, "t:brd1"), await _listing(db, "t:brd2")
    await db.boards.replace(sid, [BoardEntry(a, rank=2, score=60.5),
                                  BoardEntry(b, rank=1, score=70.0, contributions={"cost": 3.0})])
    assert [(r.listing_id, r.rank) for r in await db.boards.list(sid)] == [(b, 1), (a, 2)]
    await db.boards.replace(sid, [BoardEntry(a, rank=1, status="calling", reaction="too dark")])
    [row] = await db.boards.list(sid)
    assert (row.listing_id, row.status, row.reaction) == (a, "calling", "too dark")


async def test_writes_in_one_transaction_roll_back_together(db):
    with pytest.raises(RuntimeError):
        async with db.transaction() as conn:
            await db.sessions.create("s_txn0000001", "phone", conn=conn)
            await db.events.append(_started("s_txn0000001"), conn=conn)
            raise RuntimeError("handler failed")
    assert await db.sessions.get("s_txn0000001") is None
    assert not await db.events.seen("s_txn0000001:start")


# People


async def test_users_by_phone_keep_their_name(db):
    u = await db.users.upsert("+14165550101", name="Sara", language="fa")
    again = await db.users.upsert("+14165550101")
    assert again.user_id == u.user_id and (again.name, again.language) == ("Sara", "fa")
    assert (await db.users.by_phone("+14165550101")).user_id == u.user_id
    assert await db.users.set_consent(u.user_id, True) and (await db.users.get(u.user_id)).consent_memory
    assert await db.users.touch(u.user_id) and (await db.users.get(u.user_id)).last_seen_at is not None


async def test_forget_removes_the_profile_and_interactions_and_keeps_sessions(db):
    u = await db.users.upsert("+14165550102", name="Sam")
    lid = await _listing(db, "t:forget1")
    sid = (await db.sessions.create("s_fgt0000001", "phone", user_id=u.user_id)).session_id
    await db.profiles.upsert(u.user_id, hard={"max_monthly": 2800}, anchors=[{"label": "King and Bay"}])
    await db.interactions.add(u.user_id, lid, "rejected", session_id=sid, reason="too dark",
                              attribute="light")
    assert await db.users.forget(u.user_id) is True
    assert await db.users.get(u.user_id) is None
    assert await db.profiles.get(u.user_id) is None
    assert await db.interactions.for_user(u.user_id) == []
    kept = await db.sessions.get(sid)
    assert kept is not None and kept.user_id is None
    assert await db.users.forget(u.user_id) is False


async def test_profiles_are_written_whole(db):
    u = await db.users.upsert("+14165550103")
    p = await db.profiles.upsert(u.user_id, hard={"max_monthly": 2800}, priorities=[{"feature": "light"}],
                                 occupants=2, memory_summary="Wants light, near King and Bay.")
    assert (p.hard, p.occupants, p.shareable) == ({"max_monthly": 2800}, 2, ["move_in", "occupants", "pets"])
    p2 = await db.profiles.upsert(u.user_id, hard={"max_monthly": 3000})
    assert (p2.hard, p2.occupants, p2.memory_summary) == ({"max_monthly": 3000}, None, None)


async def test_interactions(db):
    u = await db.users.upsert("+14165550104")
    a, b = await _listing(db, "t:int1"), await _listing(db, "t:int2")
    with pytest.raises(ValueError):
        await db.interactions.add(u.user_id, a, "loved")
    first = await db.interactions.add(u.user_id, a, "rejected", reason="too small", attribute="size")
    await db.interactions.add(u.user_id, b, "liked")
    assert await db.interactions.rejected(u.user_id) == {a}
    assert [i.listing_id for i in await db.interactions.for_user(u.user_id)] == [b, a]
    assert await db.interactions.for_user(u.user_id, since=first.at + timedelta(days=1)) == []


# Catalogue


async def test_listings_read_as_contract_listings(db):
    await _listing(db, "t:old1", first_seen=T0 - timedelta(days=3))
    new = await _listing(db, "t:new1", first_seen=T0 + timedelta(hours=1))
    await _listing(db, "t:gone1", status="removed", first_seen=T0 + timedelta(hours=2))
    got = await db.listings.get(new)
    assert (got.listing_id, got.lat, got.lng, got.beds) == (new, 43.65, -79.38, 1.0)
    assert got.amenities == ["Elevator"]
    assert await db.listings.get("t:nope") is None
    active = {x.listing_id for x in await db.listings.list_active()}
    assert new in active and "t:gone1" not in active
    assert await db.listings.first_seen_after(T0) == [new]


async def test_latest_ingest_run(db):
    async with db.transaction() as conn:
        await conn.execute("insert into ingest_runs (source, started_at, status, fetched) values "
                           "('livrent', %s, 'ok', 900), ('livrent', %s, 'failed', 0)",
                           (T0, T0 + timedelta(hours=6)))
    latest = await db.ingest_runs.latest("livrent")
    assert (latest.status, latest.fetched) == ("failed", 0)
    assert await db.ingest_runs.latest("nowhere") is None


# Reference


async def test_neighbourhood_containing_a_point(db):
    n = await db.neighbourhoods.upsert(77, "Waterfront Communities", SQUARE, adjacent=[78],
                                       median_rent={"1": 2400})
    assert (n.name, n.adjacent, n.median_rent) == ("Waterfront Communities", [78], {"1": 2400})
    assert (await db.neighbourhoods.containing(43.65, -79.38)).id == 77
    assert await db.neighbourhoods.containing(43.70, -79.38) is None
    assert (await db.neighbourhoods.get(77)).name == "Waterfront Communities"


async def test_nearest_station_with_distance(db):
    await db.stations.upsert("s_king", "King", 43.6490, -79.3778, line="1")
    await db.stations.upsert("s_union", "Union", 43.6453, -79.3806, line="1")
    [near] = await db.stations.nearest(43.6487, -79.3817)
    assert near.id == "s_king" and 300 < near.distance_m < 350
    assert [s.id for s in await db.stations.nearest(43.6487, -79.3817, k=2)] == ["s_king", "s_union"]


async def test_buildings_by_registration_and_nearness(db):
    b = await db.buildings.upsert(4401234, 43.6487, -79.3817, address_norm="100 KING ST W", year_built=1972,
                                  elevators=2, eval_score=86, eval_detail={"hallways": 4})
    assert (b.year_built, b.elevators, b.eval_score, b.eval_detail) == (1972, 2, 86.0, {"hallways": 4})
    again = await db.buildings.upsert(4401234, 43.6487, -79.3817, eval_score=90)
    assert again.building_id == b.building_id and again.eval_score == 90.0
    assert (await db.buildings.get(b.building_id)).address_norm == "100 KING ST W"
    near = await db.buildings.nearest(43.6488, -79.3817)
    assert near.building_id == b.building_id and near.distance_m < 50
    assert await db.buildings.nearest(43.70, -79.38) is None
    with pytest.raises(ValueError):
        await db.buildings.upsert(1, None, None, colour="red")
