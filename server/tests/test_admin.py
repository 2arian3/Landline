"""The admin panel's API: off without a token, refused with a wrong one, and with the right one, every
renter with their sessions, calls, transcripts and chosen listings. Also the stored record of a renter
call (orchestrator/calllog.py). Runs against Postgres in a throwaway schema; skips without one."""
import os
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from landline.app import create_app
from landline.config import Settings
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
from landline.db.catalogue import Catalogue
from landline.db.pool import Database
from landline.orchestrator import SessionRuntime

T0 = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
TOKEN = "test-admin-token"
PHONE = "+14165550123"
SID = "s_admn000001"
RENTER_CALL = "c_admn000000"
LISTING_CALLS = ["c_admn000001", "c_admn000002"]


def _listing(n: int, rent: int) -> Listing:
    return Listing(listing_id=f"t:{n}", source="t", source_id=str(n), url=f"https://example.com/{n}",
                   first_seen=T0, last_seen=T0, address=f"{n} Admin St", lat=43.65, lng=-79.38, rent=rent,
                   monthly_cost=rent, beds=1, sqft=700)


LISTINGS = [_listing(n, 2000 + 100 * n) for n in range(1, 5)]


def _meta(key: str, s: int = 0) -> dict:
    return {"session_id": SID, "at": T0 + timedelta(seconds=s), "idem_key": f"{SID}:{key}"}


@pytest.fixture
async def url(schema):
    """A database URL whose connections use the throwaway schema, holding one renter's whole story."""
    url = f"{os.environ['TEST_DATABASE_URL']}?options=-csearch_path%3D{schema[0]},public"
    db = await Database.open(url, max_size=4)
    async with db.transaction() as conn:
        await conn.execute("truncate users, sessions, events, calls, listings cascade")
        for x in LISTINGS:
            await conn.execute(
                "insert into listings (listing_id, source, source_id, url, first_seen, last_seen, status, "
                "address, rent, monthly_cost, beds) "
                "values (%s, 't', %s, %s, %s, %s, 'active', %s, %s, %s, 1)",
                (x.listing_id, x.source_id, x.url, x.first_seen, x.last_seen, x.address, x.rent, x.rent))
    rt = SessionRuntime(Catalogue(LISTINGS, "test"), db)
    # The renter's call: a session, a stored call with its transcript, a reaction and a plan.
    await rt.start(SessionStarted(**_meta("start"), channel="phone", caller_phone=PHONE))
    rt.calls.started(RENTER_CALL, SID, "voice")
    rt.calls.said(RENTER_CALL, "agent", "Hi, this is Landline. What are you looking for?")
    rt.calls.said(RENTER_CALL, "renter", "A one-bedroom under 2,600.")
    rt.calls.said(RENTER_CALL, "renter", "   ")   # nothing said: not stored
    await rt.emit(PreferencesUpdated(**_meta("p1", 1), delta=PreferenceDelta(max_monthly=2600, min_beds=1)))
    await rt.emit(FeedbackGiven(**_meta("f1", 2), listing_id="t:3", reaction="reject", reason="too dark"))
    await rt.emit(CallsRequested(**_meta("plan", 3), listing_ids=["t:1", "t:2"], may_book=True))
    rt.calls.ended(RENTER_CALL, "hangup")
    await rt.emit(SessionEnded(**_meta("end", 4), reason="hangup"))
    # The calls to the two listings: one books a viewing, one never answers.
    async with rt.session_tx(SID) as tx:
        plan = CallPlan(session_id=SID, listing_ids=["t:1", "t:2"], may_book=True, created_at=T0)
        await tx.apply(CallsScheduled(**_meta("scheduled", 5), plan=plan, call_ids=LISTING_CALLS))
        for cid, lid in zip(LISTING_CALLS, ["t:1", "t:2"], strict=True):
            await db.calls.create(cid, session_id=SID, role="listing", transport="sim", state="queued",
                                  listing_id=lid, conn=tx.conn)
    viewing = Viewing(starts_at=datetime(2026, 10, 24, 18, 0, tzinfo=UTC), slot_text="Saturday at two")
    await rt.emit(OutcomeReady(**_meta("o1", 6), outcome=CallOutcome(
        call_id=LISTING_CALLS[0], listing_id="t:1", end_reason="done", viewing=viewing)))
    await rt.emit(OutcomeReady(**_meta("o2", 7), outcome=CallOutcome(
        call_id=LISTING_CALLS[1], listing_id="t:2", end_reason="no_answer")))
    await rt.emit(CallsCompleted(**_meta("done", 8), call_ids=LISTING_CALLS))
    await rt.idle()
    await rt.close()
    await db.close()
    yield url


def _client(url: str | None, token: str | None = TOKEN) -> TestClient:
    settings = Settings(database_url=url, web_url="http://localhost:3000", transport="stub",
                        admin_token=token)
    return TestClient(create_app(settings, Catalogue(LISTINGS, "test")))


AUTH = {"Authorization": f"Bearer {TOKEN}"}


def test_the_admin_api_is_off_without_a_token_and_refuses_a_wrong_one(url):
    with _client(url, token=None) as c:   # no ADMIN_TOKEN: as if the routes weren't there
        assert c.get("/api/admin/users", headers=AUTH).status_code == 404
    with _client(None) as c:              # no database: same
        assert c.get("/api/admin/users", headers=AUTH).status_code == 404
    with _client(url) as c:
        assert c.get("/api/admin/users").status_code == 401
        assert c.get("/api/admin/users", headers={"Authorization": "Bearer wrong"}).status_code == 401
        assert c.get(f"/api/admin/users?token={TOKEN}").status_code == 401   # never from the URL
        preflight = {"Origin": "http://localhost:3000", "Access-Control-Request-Method": "GET",
                     "Access-Control-Request-Headers": "authorization"}
        assert c.options("/api/admin/users", headers=preflight).status_code == 200


def test_the_users_list_counts_what_each_renter_did(url):
    with _client(url) as c:
        [row] = c.get("/api/admin/users", headers=AUTH).json()
        assert (row["phone"], row["sessions"], row["calls"], row["booked"], row["last_phase"]) == (
            PHONE, 1, 3, 1, "done")
        assert row["summary"] == ("Wants up to $2,600 a month and 1 bedroom or more. "
                                  "Turned down 3 Admin St (too dark).")


def test_a_renter_shows_their_history_calls_and_chosen_places(url):
    with _client(url) as c:
        uid = c.get("/api/admin/users", headers=AUTH).json()[0]["user_id"]
        u = c.get(f"/api/admin/users/{uid}", headers=AUTH).json()
        assert (u["preferences"]["max_monthly"], u["preferences"]["min_beds"]) == (2600, 1)
        places = {p["listing_id"]: (p["address"], p["actions"], p["reason"]) for p in u["places"]}
        assert places == {"t:1": ("1 Admin St", ["booked"], None), "t:2": ("2 Admin St", ["called"], None),
                          "t:3": ("3 Admin St", ["rejected"], "too dark")}
        [session] = u["history"]
        planned = [p["address"] for p in session["plan"]]
        assert (session["phase"], planned) == ("done", ["1 Admin St", "2 Admin St"])
        assert session["results"].startswith("I booked 1 Admin St for Saturday at 2 pm.")
        calls = {k["call_id"]: (k["role"], k["address"], k["turns"]) for k in session["calls"]}
        assert calls == {RENTER_CALL: ("renter", None, 2), LISTING_CALLS[0]: ("listing", "1 Admin St", 0),
                         LISTING_CALLS[1]: ("listing", "2 Admin St", 0)}
        assert c.get("/api/admin/users/999999", headers=AUTH).status_code == 404


def test_a_call_shows_its_transcript_in_order(url):
    with _client(url) as c:
        call = c.get(f"/api/admin/calls/{RENTER_CALL}", headers=AUTH).json()
        assert (call["state"], call["end_reason"], call["transport"], call["session_id"]) == (
            "ended", "hangup", "voice", SID)
        assert [(t["idx"], t["speaker"], t["text"]) for t in call["transcript"]] == [
            (0, "agent", "Hi, this is Landline. What are you looking for?"),
            (1, "renter", "A one-bedroom under 2,600."),
        ]
        assert c.get("/api/admin/calls/c_nope", headers=AUTH).status_code == 404
        # The renter's own call also answers the public live-captions route the page polls.
        assert len(c.get(f"/api/calls/{RENTER_CALL}/turns").json()) == 2
