"""Profiles (O-05): a renter is remembered by phone number from one call to the next.

These run against Postgres in a throwaway schema (conftest.py) and skip without TEST_DATABASE_URL.
"""
import asyncio
import os
from datetime import UTC, datetime, timedelta

import pytest

from landline.contracts import (
    CallOutcome,
    CallPlan,
    CallsCompleted,
    CallsRequested,
    CallsScheduled,
    FeedbackGiven,
    Listing,
    OutcomeReady,
    PreferenceDelta,
    PreferencesUpdated,
    SessionEnded,
    SessionStarted,
    Viewing,
)
from landline.contracts.tools import AnchorRef
from landline.db.catalogue import Catalogue
from landline.db.pool import Database
from landline.orchestrator import SessionRuntime
from landline.orchestrator.profiles import MAX_OPENING_WORDS, Remembered, opening

T0 = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
PHONE = "+14165550100"
SATURDAY_2PM = datetime(2026, 10, 24, 18, 0, tzinfo=UTC)


def _listing(n: int, rent: int, first_seen: datetime = T0) -> Listing:
    return Listing(listing_id=f"t:{n}", source="t", source_id=str(n), url=f"https://example.com/{n}",
                   first_seen=first_seen, last_seen=first_seen, address=f"{n} Main St", lat=43.65,
                   lng=-79.38, rent=rent, monthly_cost=rent, beds=1, sqft=700)


LISTINGS = [_listing(n, 2000 + 100 * n) for n in range(1, 6)]


@pytest.fixture
async def db(schema):
    database = await Database.open(os.environ["TEST_DATABASE_URL"], schema=schema[0], max_size=4)
    async with database.transaction() as conn:
        await conn.execute("truncate users, sessions, events, listings cascade")
        for x in LISTINGS:   # listing_interactions points at listings
            await conn.execute(
                "insert into listings (listing_id, source, source_id, url, first_seen, last_seen, status, "
                "address, rent, monthly_cost, beds, geom) values (%s, 't', %s, %s, %s, %s, 'active', %s, %s, "
                "%s, 1, ST_SetSRID(ST_MakePoint(-79.38, 43.65), 4326)::geography)",
                (x.listing_id, x.source_id, x.url, x.first_seen, x.last_seen, x.address, x.rent, x.rent))
    yield database
    await database.close()


def _runtime(db: Database, listings=LISTINGS) -> SessionRuntime:
    return SessionRuntime(Catalogue(listings, "test"), db)


def _meta(sid: str, key: str, s: int = 0) -> dict:
    return {"session_id": sid, "at": T0 + timedelta(seconds=s), "idem_key": f"{sid}:{key}"}


async def _call(rt: SessionRuntime, sid: str, phone: str | None = PHONE, channel: str = "phone"):
    return await rt.start(SessionStarted(**_meta(sid, "start"), channel=channel, caller_phone=phone))


async def _hang_up(rt: SessionRuntime, sid: str) -> None:
    await rt.emit(SessionEnded(**_meta(sid, "end", 50), reason="hangup"))
    await rt.idle()   # the profile is written after the commit


async def test_a_new_number_gets_a_user_and_its_session_is_linked(db):
    rt = _runtime(db)
    view = await _call(rt, "s_prof000001")
    user = await db.users.by_phone(PHONE)
    assert user is not None and (await db.sessions.get("s_prof000001")).user_id == user.user_id
    assert (view.returning, rt.opening("s_prof000001")) == (False, None)
    assert view.agent_says == "Tell me what you're looking for, and the map will follow."


async def test_the_brief_is_saved_and_the_next_call_starts_from_it(db):
    rt = _runtime(db)
    sid = "s_prof000002"
    await _call(rt, sid)
    wants = PreferenceDelta(max_monthly=2600, min_beds=1, priorities=["light"], pets=["dog"],
                            anchors=[AnchorRef(label="King and Bay")])
    await rt.emit(PreferencesUpdated(**_meta(sid, "p1", 1), delta=wants))
    await rt.emit(FeedbackGiven(**_meta(sid, "f1", 2), listing_id="t:1", reaction="like"))
    await rt.emit(FeedbackGiven(**_meta(sid, "f2", 3), listing_id="t:2", reaction="reject", reason="too dark",
                                attribute="light"))
    await _hang_up(rt, sid)

    user = await db.users.by_phone(PHONE)
    profile = await db.profiles.get(user.user_id)
    assert profile.hard == {"max_monthly": 2600, "min_beds": 1, "pets": ["dog"]}
    assert (profile.priorities, profile.anchors) == ([{"feature": "light"}],
                                                     [{"label": "King and Bay", "lat": None, "lng": None}])
    # The summary only reads back stored fields.
    assert profile.memory_summary == ("Wants up to $2,600 a month, 1 bedroom or more, near King and Bay, "
                                      "light matters most and has a dog. Liked 1 Main St. "
                                      "Turned down 2 Main St (too dark).")
    stored = await db.interactions.for_user(user.user_id)
    assert sorted((i.listing_id, i.action, i.reason) for i in stored) == [("t:1", "liked", None),
                                                                         ("t:2", "rejected", "too dark")]
    assert user.last_seen_at is not None

    # The same number calls again, written another way: the search and the reactions are back.
    again = await _call(rt, "s_prof000003", phone="(416) 555-0100")
    assert again.returning and again.agent_says == "Welcome back. Here is your search from last time."
    p = again.preferences
    assert (p.max_monthly, p.min_beds, p.pets, p.priorities, [a.label for a in p.anchors]) == (
        2600, 1, ["dog"], ["light"], ["King and Bay"])
    # The place they turned down stays off the board (O-04), and the reaction is still remembered.
    assert "t:2" not in {c.listing_id for c in again.board}
    assert rt.state("s_prof000003").reactions["t:2"].reason == "too dark"
    said = rt.opening("s_prof000003")
    assert said.startswith("This renter last called earlier today. What you know: Wants up to $2,600")
    # Ending the second call without new reactions stores nothing twice.
    await _hang_up(rt, "s_prof000003")
    assert len(await db.interactions.for_user(user.user_id)) == 2


async def test_the_next_call_opens_with_the_results_of_the_last_one(db):
    rt = _runtime(db)
    sid = "s_prof000004"
    await _call(rt, sid)
    await rt.emit(CallsRequested(**_meta(sid, "plan", 1), listing_ids=["t:1", "t:2"], may_book=True))
    await _hang_up(rt, sid)

    # While the calls are still running, the next call says so and has no results yet.
    running = await _call(rt, "s_prof000005")
    assert "The calls from their last call are still running." in rt.opening("s_prof000005")
    assert running.agent_says == "Welcome back. Here is your search from last time."

    plan = CallPlan(session_id=sid, listing_ids=["t:1", "t:2"], may_book=True, created_at=T0)
    calls = ["c_prof000001", "c_prof000002"]
    await rt.emit(CallsScheduled(**_meta(sid, "scheduled", 51), plan=plan, call_ids=calls))
    booked = CallOutcome(call_id="c_prof000001", listing_id="t:1", end_reason="done",
                         viewing=Viewing(starts_at=SATURDAY_2PM, slot_text="Saturday at two"))
    await rt.emit(OutcomeReady(**_meta(sid, "o1", 52), outcome=booked))
    await rt.emit(OutcomeReady(**_meta(sid, "o2", 53), outcome=CallOutcome(
        call_id="c_prof000002", listing_id="t:2", end_reason="no_answer")))
    await rt.emit(CallsCompleted(**_meta(sid, "done", 54), call_ids=calls))
    await rt.idle()
    user = await db.users.by_phone(PHONE)
    assert sorted((i.listing_id, i.action) for i in await db.interactions.for_user(user.user_id)) == [
        ("t:1", "booked"), ("t:2", "called")]

    # The session started while calls were running is the latest now; end it so the finished one is.
    await db.sessions.end("s_prof000005", "closed")
    async with db.transaction() as conn:
        await conn.execute("update sessions set started_at = now() - interval '1 hour' "
                           "where session_id = 's_prof000005'")
    later = await _call(rt, "s_prof000006")
    assert later.agent_says == ("Welcome back. I booked 1 Main St for Saturday at 2 pm. "
                                "Nobody picked up at 2 Main St.")
    assert "Results of the calls you made for them: I booked 1 Main St" in rt.opening("s_prof000006")


async def test_places_new_since_the_last_call_are_counted(db):
    fresh = _listing(6, 2100, first_seen=datetime.now(UTC) + timedelta(minutes=5))
    rt = _runtime(db, [*LISTINGS, fresh])
    async with db.transaction() as conn:
        await conn.execute("insert into listings (listing_id, source, source_id, url, first_seen, last_seen, "
                           "status, address, rent, monthly_cost, beds) values ('t:6', 't', '6', 'u', now(), "
                           "now(), 'active', '6 Main St', 2100, 2100, 1)")
    await _call(rt, "s_prof000007")
    await rt.emit(PreferencesUpdated(**_meta("s_prof000007", "p1", 1),
                                     delta=PreferenceDelta(max_monthly=2200)))
    await _hang_up(rt, "s_prof000007")
    await _call(rt, "s_prof000008")
    assert rt.opening("s_prof000008").endswith("1 new place matches since then.")


async def test_a_judge_session_and_a_failed_lookup_start_fresh(db, monkeypatch):
    rt = _runtime(db)
    judge = await _call(rt, "s_prof000009", phone=None, channel="judge")
    assert not judge.returning and await db.users.by_phone(PHONE) is None

    async def down(phone):
        raise ConnectionError("database is down")

    monkeypatch.setattr(rt.profiles, "recall", down)
    view = await _call(rt, "s_prof000010")
    assert view.version == 1 and not view.returning and rt.state("s_prof000010").user_id is None


async def test_a_slow_lookup_starts_the_call_fresh(db, monkeypatch):
    rt = _runtime(db)
    monkeypatch.setattr("landline.orchestrator.runtime.RECALL_TIMEOUT_S", 0.05)

    async def slow(phone):
        await asyncio.sleep(1)

    monkeypatch.setattr(rt.profiles, "recall", slow)
    assert (await _call(rt, "s_prof000011")).returning is False


async def test_forget_me_removes_the_renter(db):
    rt = _runtime(db)
    await _call(rt, "s_prof000012")
    await rt.emit(PreferencesUpdated(**_meta("s_prof000012", "p1", 1),
                                     delta=PreferenceDelta(max_monthly=2500)))
    await _hang_up(rt, "s_prof000012")
    user = await db.users.by_phone(PHONE)
    assert await rt.profiles.forget(user.user_id)
    assert await db.users.by_phone(PHONE) is None and await db.profiles.get(user.user_id) is None
    assert not (await _call(rt, "s_prof000013")).returning


def test_the_opening_keeps_the_results_and_stays_within_120_words():
    results = " ".join(f"I booked {n} Long Street Name Avenue for Saturday at {n} pm." for n in range(1, 15))
    r = Remembered(user_id=1, returning=True, last_seen_at=T0 - timedelta(days=2), last_results=results,
                   summary="Wants up to $2,600 a month and 1 bedroom or more.")
    said = opening(r, new_matches=3, now=T0)
    assert said.startswith("This renter last called on Wednesday. Results of the calls you made for them: "
                           "I booked 1 Long Street Name Avenue")
    assert len(said.split()) <= MAX_OPENING_WORDS
