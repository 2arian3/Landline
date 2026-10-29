"""Profiles (O-05): a renter is stored by phone number, for /me. Nothing of it is read back into a call: every
call starts from the beginning (#144), even with "Start my next call from this search" on (U-12d is off).

These run against Postgres in a throwaway schema (conftest.py) and skip without TEST_DATABASE_URL.
"""
import asyncio
import os
import time
from datetime import UTC, date, datetime, timedelta

import pytest

from landline.config import Settings
from landline.contracts import (
    AnchorResolved,
    CallOutcome,
    CallPlan,
    CallsCompleted,
    CallsRequested,
    CallsScheduled,
    FeedbackGiven,
    Listing,
    OutcomeReady,
    PreferenceDelta,
    Preferences,
    PreferencesUpdated,
    SessionEnded,
    SessionStarted,
    Viewing,
)
from landline.contracts.tools import AnchorRef
from landline.db.catalogue import Catalogue
from landline.db.pool import Database
from landline.orchestrator import SessionRuntime
from landline.voice import bridge
from landline.voice.dispatch import Dispatcher

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


async def test_a_new_number_gets_a_user_and_its_session_is_linked_when_the_brief_ends(db):
    rt = _runtime(db)
    view = await _call(rt, "s_prof000001")
    assert await db.users.by_phone(PHONE) is None   # nothing is looked up or written before the greeting
    assert (view.returning, rt.opening("s_prof000001")) == (False, None)
    assert view.agent_says == "Tell me what you're looking for, and the map will follow."
    await _hang_up(rt, "s_prof000001")
    user = await db.users.by_phone(PHONE)
    assert user is not None and (await db.sessions.get("s_prof000001")).user_id == user.user_id


async def test_the_brief_is_saved_and_the_next_call_does_not_start_from_it(db):
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

    # The same number calls again. The profile is still there, and none of it is put on this call (#144).
    again = await _call(rt, "s_prof000003", phone="(416) 555-0100")
    assert not again.returning
    assert again.agent_says == "Tell me what you're looking for, and the map will follow."
    assert again.preferences.max_monthly is None and again.preferences.pets == []
    assert rt.state("s_prof000003").reactions == {}
    assert rt.opening("s_prof000003") is None
    assert "t:2" in {c.listing_id for c in again.board}
    # Ending the second call without new reactions stores nothing twice.
    await _hang_up(rt, "s_prof000003")
    assert len(await db.interactions.for_user(user.user_id)) == 2


async def test_a_call_adds_to_the_profile_and_keeps_the_renters_own_edits(db):
    """U-12a review: a call's end used to rewrite the whole profile from that call alone, so it reset what
    the renter allowed Landline to share, their move-in date, and a budget they saved on /me."""
    rt = _runtime(db)
    user = await db.users.upsert(PHONE)
    saved = {"max_monthly": 2500, "areas": ["Annex"], "move_in": "2026-12-01"}
    await db.profiles.upsert(user.user_id, hard=saved, move_in=date(2026, 12, 1), shareable=["name"])
    await _call(rt, "s_prof000009")
    said = PreferenceDelta(pets=["cat"], max_monthly=2700)
    await rt.emit(PreferencesUpdated(**_meta("s_prof000009", "p1", 1), delta=said))
    await _hang_up(rt, "s_prof000009")
    profile = await db.profiles.get(user.user_id)
    assert profile.hard == {"max_monthly": 2700, "areas": ["Annex"], "move_in": "2026-12-01", "pets": ["cat"]}
    assert (profile.shareable, profile.move_in) == (["name"], date(2026, 12, 1))
    await _call(rt, "s_prof000010")   # a call where nothing is said keeps it all
    await _hang_up(rt, "s_prof000010")
    assert (await db.profiles.get(user.user_id)).hard == profile.hard


async def test_the_next_call_does_not_open_with_the_results_of_the_last_one(db):
    rt = _runtime(db)
    sid = "s_prof000004"
    await _call(rt, sid)
    await rt.emit(CallsRequested(**_meta(sid, "plan", 1), listing_ids=["t:1", "t:2"], may_book=True))
    await _hang_up(rt, sid)

    # While the calls are still running, the next call is not told, and does not open on them (#144).
    running = await _call(rt, "s_prof000005")
    assert rt.opening("s_prof000005") is None and not running.returning
    assert running.agent_says == "Tell me what you're looking for, and the map will follow."

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
    assert rt.opening("s_prof000006") is None and not later.returning
    assert "booked" not in later.agent_says.lower()
    assert later.agent_says == "Tell me what you're looking for, and the map will follow."


async def test_the_call_plan_shares_only_what_the_renter_ticked_on_me(db):
    # U-12: /me's privacy choice (user_profiles.shareable) is what the Caller may tell listing agents.
    user = await db.users.upsert(PHONE, name="Sara")
    await db.profiles.upsert(user.user_id, shareable=["name", "move_in"])   # occupants and pets unticked
    rt = _runtime(db)
    d = Dispatcher(rt, Settings(database_url=None, web_url="https://landline.example", transport="stub"))
    sid = "s_prof000023"
    await _call(rt, sid)
    await rt.emit(PreferencesUpdated(**_meta(sid, "prefs", 1), delta=PreferenceDelta(
        move_in="December 1", occupants=2, pets=["dog"])))
    await rt.emit(CallsRequested(**_meta(sid, "plan", 2), listing_ids=["t:1"]))
    await _hang_up(rt, sid)
    await d.idle()
    assert rt.state(sid).plan.shareable.model_dump() == {"name": "Sara", "move_in": "December 1",
                                                          "occupants": None, "pets": []}
    assert (await db.profiles.get(user.user_id)).shareable == ["name", "move_in"]   # the call kept the choice


async def test_calls_nobody_can_place_end_the_session_and_the_next_call_is_not_told(db):
    # Production with TRANSPORT=stub: a phone session's calls have no runner (issue #95).
    rt = _runtime(db)
    d = Dispatcher(rt, Settings(database_url=None, web_url="https://landline.example", transport="stub"))
    sid = "s_prof000014"
    await _call(rt, sid)
    await rt.emit(CallsRequested(**_meta(sid, "plan", 1), listing_ids=["t:1"], may_book=True))
    await _hang_up(rt, sid)
    await d.idle()
    assert (await db.sessions.get(sid)).status == "done"   # not left in `calling`
    user = await db.users.by_phone(PHONE)
    assert "called" not in [i.action for i in await db.interactions.for_user(user.user_id)]

    await _call(rt, "s_prof000015")
    assert rt.opening("s_prof000015") is None   # the missed call is not appended onto the next one (#144)


async def test_a_session_stuck_in_calling_stops_counting_as_running(db):
    rt = _runtime(db)   # no Dispatcher, as when the server went down between the hang-up and the calls
    sid = "s_prof000016"
    await _call(rt, sid)
    await rt.emit(CallsRequested(**_meta(sid, "plan", 1), listing_ids=["t:1"]))
    await _hang_up(rt, sid)
    assert (await db.sessions.get(sid)).status == "calling"
    async with db.transaction() as conn:
        await conn.execute("update sessions set started_at = now() - interval '2 hours' "
                           "where session_id = %s", (sid,))
    await _call(rt, "s_prof000017")
    assert rt.opening("s_prof000017") is None   # a stuck session is not appended onto the next call (#144)


async def test_places_new_since_the_last_call_are_not_appended(db):
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
    assert rt.opening("s_prof000008") is None   # new places since last time are not appended (#144)


async def test_a_judge_session_gets_no_user_and_a_phone_start_never_looks_the_caller_up(db, monkeypatch):
    rt = _runtime(db)
    judge = await _call(rt, "s_prof000009", phone=None, channel="judge")
    await _hang_up(rt, "s_prof000009")
    assert not judge.returning and await db.users.by_phone(PHONE) is None

    async def down(phone):
        raise ConnectionError("database is down")

    monkeypatch.setattr(db.users, "by_phone", down)   # never called: the start reads nothing about them
    view = await _call(rt, "s_prof000010")
    assert view.version == 1 and not view.returning and rt.state("s_prof000010").user_id is None


async def test_the_start_never_waits_on_the_caller_s_profile(db, monkeypatch):
    rt = _runtime(db)

    async def slow(phone):
        await asyncio.sleep(1)

    monkeypatch.setattr(db.users, "by_phone", slow)
    started = time.monotonic()
    assert (await _call(rt, "s_prof000011")).returning is False
    assert time.monotonic() - started < 0.5


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


# Every call starts from the beginning: a saved search, a name, preferences, reactions and places from earlier
# calls never reach a new one, even with "Start my next call from this search" on (U-12d is off)

CHRISTIE_PITS = {"label": "Christie Pits", "lat": 43.6645, "lng": -79.4207}
FIRST_LINE = "Tell me what you're looking for, and the map will follow."


async def _saved(db: Database, on: bool) -> int:
    """A renter named Sara who saved up to $2,000, 1 bed or more, near Christie Pits (with its point), and the
    "Start my next call from this search" switch."""
    user = await db.users.upsert(PHONE, name="Sara")
    await db.profiles.upsert(user.user_id, hard={"max_monthly": 2000, "min_beds": 1, "language": "fa"},
                             priorities=[{"feature": "light"}], anchors=[CHRISTIE_PITS])
    async with db.transaction() as conn:
        await conn.execute("update users set use_saved_search = %s where user_id = %s", (on, user.user_id))
    return user.user_id


def _fresh(rt: SessionRuntime, sid: str) -> None:
    """The session is a first call's: nothing in it came from an earlier one."""
    state, view = rt.state(sid), rt.get(sid)
    assert view.version == 1 and view.preferences == Preferences() and state.reactions == {}
    assert (view.returning, view.user_name, view.agent_says) == (False, None, FIRST_LINE)
    assert (state.returning, state.user_name, state.saved_search, state.recalled) == (False, None, None, {})
    assert rt.opening(sid) is None


@pytest.mark.parametrize("on", [True, False])
async def test_a_saved_search_never_starts_a_call_even_with_the_switch_on(db, monkeypatch, on):
    await _saved(db, on=on)
    rt = _runtime(db)

    async def no_lookup(label, near=()):
        raise AssertionError(f"{label} was looked up: nothing saved should reach the call")
    monkeypatch.setattr(rt.places, "resolve", no_lookup)
    sid = "s_prof000020"
    await _call(rt, sid)
    await rt.idle()   # nothing runs after the start's commit either
    _fresh(rt, sid)
    assert [e.type for e in await db.events.list(sid)] == ["SessionStarted"]


async def test_a_second_call_after_a_search_and_reactions_starts_fresh_and_the_concierge_hears_none_of_it(
        db, monkeypatch):
    await _saved(db, on=True)
    rt = _runtime(db)

    async def no_lookup(label, near=()):
        return None
    monkeypatch.setattr(rt.places, "resolve", no_lookup)
    first = "s_prof000024"
    await _call(rt, first)
    wants = PreferenceDelta(max_monthly=2600, min_beds=1, priorities=["light"], pets=["dog"],
                            anchors=[AnchorRef(label="Christie Pits")])
    await rt.emit(PreferencesUpdated(**_meta(first, "p1", 1), delta=wants))
    await rt.emit(AnchorResolved(**_meta(first, "place:christie pits", 2), label="Christie Pits",
                                 lat=43.6645, lng=-79.4207))
    await rt.emit(FeedbackGiven(**_meta(first, "f1", 3), listing_id="t:1", reaction="like"))
    await rt.emit(FeedbackGiven(**_meta(first, "f2", 4), listing_id="t:2", reaction="reject",
                                reason="too dark", attribute="light"))
    await rt.emit(CallsRequested(**_meta(first, "plan", 5), listing_ids=["t:1"], may_book=True))
    await _hang_up(rt, first)
    user = await db.users.by_phone(PHONE)
    assert (await db.profiles.get(user.user_id)).hard["max_monthly"] == 2600   # still recorded, for /me
    assert len(await db.interactions.for_user(user.user_id)) == 2

    second, stranger = "s_prof000025", "s_prof000026"
    await _call(rt, second, phone="(416) 555-0100")   # the same number, written differently
    await _call(rt, stranger, phone="+14165550199")   # a number never heard before
    await rt.idle()
    _fresh(rt, second)
    # The board is the default one, the same a new caller sees, with the place they turned down back on it.
    board = [c.listing_id for c in rt.get(second).board]
    assert board == [c.listing_id for c in rt.get(stranger).board] and "t:2" in board
    assert rt.state(second).calls == {} and rt.state(second).request is None
    # What the bridge gives the Concierge (voice/bridge.py _talk) is a first call's, with nothing about them.
    instructions, start = bridge.opening(rt.opening(second))
    assert (instructions, start) == bridge.opening(None)
    said = instructions + str(start)
    for theirs in ("Sara", "Christie Pits", "2,600", "2,000", "1 Main St", "last called"):
        assert theirs.lower() not in said.lower(), theirs
