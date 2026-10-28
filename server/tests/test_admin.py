"""The admin panel's API: off without a token, refused with a wrong one, and with the right one, every
renter with their sessions, calls, transcripts and chosen listings. Also the stored record of a renter
call (orchestrator/calllog.py). Runs against Postgres in a throwaway schema; skips without one."""
import io
import os
import struct
import wave
from datetime import UTC, datetime, timedelta

import psycopg
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
    Fact,
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
    rt.calls.said(RENTER_CALL, "agent", "Hi, this is Landline. What are you looking for?",
                  start_ms=0, end_ms=2)
    rt.calls.said(RENTER_CALL, "renter", "A one-bedroom under 2,600.", start_ms=2, end_ms=4)
    rt.calls.said(RENTER_CALL, "renter", "   ")   # nothing said: not stored
    # Its recording (A-10): 4 ms of the renter (2 silent, then 2 loud) and 2 ms of the agent, which the
    # renter cut short at 1 ms.
    rt.calls.audio(RENTER_CALL, "renter", 0, b"\xff" * 16 + b"\x00" * 16, fmt="pcmu", rate=8000)
    rt.calls.audio(RENTER_CALL, "agent", 0, b"\x80" * 16, fmt="pcmu", rate=8000)
    rt.calls.placed(RENTER_CALL, 0, 0, 1)
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
    heard = Fact(field="parking_cost", value="$180 a month per spot", quote="Parking is $180 a month.",
                 speaker="Agent heard", at=T0)
    await rt.emit(OutcomeReady(**_meta("o1", 6), outcome=CallOutcome(
        call_id=LISTING_CALLS[0], listing_id="t:1", end_reason="done", viewing=viewing, facts=[heard])))
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


def test_a_phone_number_opens_that_renters_history(url):
    with _client(url) as c:
        u = c.post("/api/me", json={"phone": "(416) 555-0123"}).json()
        assert u["phone"] == PHONE
        assert u["history"][0]["phase"] == "done"
        assert u["history"][0]["results"].startswith("I booked 1 Admin St")
        assert c.post("/api/me", json={"phone": "+14165550999"}).json()["detail"] == "unknown number"
        assert c.post("/api/me", json={"phone": "not a number"}).status_code == 422


def test_a_clerk_sign_in_opens_only_that_renters_history(url, clerk):
    """U-11a: the phone comes from the verified Clerk token, never from the request."""
    settings = Settings(database_url=url, web_url="http://localhost:3000", transport="stub",
                        admin_token=TOKEN, clerk_secret_key="sk_test_throwaway", clerk_jwt_key=clerk.pem)
    with TestClient(create_app(settings, Catalogue(LISTINGS, "test"))) as c:
        u = c.get("/api/me", headers={"Authorization": f"Bearer {clerk.token(phone=PHONE)}"}).json()
        assert u["phone"] == PHONE
        assert u["history"][0]["results"].startswith("I booked 1 Admin St")
        r = c.get("/api/me", headers={"Authorization": f"Bearer {clerk.token(phone='+14165550999')}"})
        assert (r.status_code, r.json()["detail"]) == (404, "unknown number")
        assert c.post("/api/me", json={"phone": "(416) 555-0123"}).status_code == 401   # no proof, no history


def test_a_renter_edits_only_their_own_profile(url, clerk):
    """U-12a: PATCH /api/me/profile saves what it sends and only that, for the token's renter alone."""
    neighbour = "+14165550177"
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("insert into users (phone_e164) values (%s)", (neighbour,))
    settings = Settings(database_url=url, web_url="http://localhost:3000", transport="stub",
                        clerk_secret_key="sk_test_throwaway", clerk_jwt_key=clerk.pem)
    me = {"Authorization": f"Bearer {clerk.token(phone=PHONE)}"}
    them = {"Authorization": f"Bearer {clerk.token(phone=neighbour)}"}
    with TestClient(create_app(settings, Catalogue(LISTINGS, "test"))) as c:
        edit = {"max_monthly": 2400, "min_beds": 1.5, "areas": [" Annex ", "Leslieville"], "pets": ["cat"],
                "must_have": ["laundry"], "move_in": "2026-12-01", "email": " sara@example.com ",
                "shareable": ["name", "pets"], "use_saved_search": True}
        p = c.patch("/api/me/profile", headers=me, json=edit).json()
        assert (p["phone"], p["email"], p["use_saved_search"], p["shareable"]) == (
            PHONE, "sara@example.com", True, ["name", "pets"])
        assert {k: p["preferences"][k] for k in ("max_monthly", "min_beds", "areas", "pets", "must_have",
                                                 "move_in")} == {
            "max_monthly": 2400, "min_beds": 1.5, "areas": ["Annex", "Leslieville"], "pets": ["cat"],
            "must_have": ["laundry"], "move_in": "2026-12-01"}
        assert c.get("/api/me/profile", headers=me).json() == p   # stored
        # A field left out stays; null clears; [] empties a list.
        cleared = {"max_monthly": None, "email": None, "pets": []}
        p2 = c.patch("/api/me/profile", headers=me, json=cleared).json()
        assert p2["preferences"]["max_monthly"] is None and p2["preferences"]["pets"] == []
        assert p2["email"] is None
        assert p2["preferences"]["areas"] == ["Annex", "Leslieville"] and p2["use_saved_search"] is True
        # A bad field saves nothing, not even the good ones beside it.
        bad = c.patch("/api/me/profile", headers=me, json={"max_monthly": 1000, "email": "nope"})
        assert bad.status_code == 422 and c.get("/api/me/profile", headers=me).json() == p2
        # Another renter's token edits their profile, never this one.
        theirs = c.patch("/api/me/profile", headers=them, json={"max_monthly": 9999}).json()
        assert (theirs["phone"], theirs["preferences"]["max_monthly"]) == (neighbour, 9999)
        assert c.get("/api/me/profile", headers=me).json() == p2
        stranger = {"Authorization": f"Bearer {clerk.token(phone='+14165550888')}"}
        r = c.patch("/api/me/profile", headers=stranger, json={"max_monthly": 1})
        assert (r.status_code, r.json()["detail"]) == (404, "unknown number")


def test_an_email_edit_leaves_the_saved_search_alone(url, clerk):
    """U-12a review: an email-only PATCH created a profile row and marked it updated."""
    neighbour = "+14165550166"
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("insert into users (phone_e164) values (%s)", (neighbour,))
    settings = Settings(database_url=url, web_url="http://localhost:3000", transport="stub",
                        clerk_secret_key="sk_test_throwaway", clerk_jwt_key=clerk.pem)
    them = {"Authorization": f"Bearer {clerk.token(phone=neighbour)}"}
    with TestClient(create_app(settings, Catalogue(LISTINGS, "test"))) as c:
        edit = {"email": "x@y.ca", "use_saved_search": True}
        p = c.patch("/api/me/profile", headers=them, json=edit).json()
        assert (p["email"], p["use_saved_search"], p["updated_at"]) == ("x@y.ca", True, None)
    with psycopg.connect(url) as conn:
        rows = conn.execute("select count(*) from user_profiles p join users u using (user_id) "
                            "where u.phone_e164 = %s", (neighbour,)).fetchone()
    assert rows == (0,)


OTHER = "+14165550199"


@pytest.fixture
async def other_renter(url):
    """A second stored renter, who never had a call."""
    db = await Database.open(url, max_size=1)
    await db.users.upsert(OTHER)
    await db.close()
    return OTHER


def test_my_listings_shows_only_the_signed_in_renters_calls(url, clerk, other_renter):
    """U-12c: each listing Landline called for this renter, from stored rows, with its facts and sources."""
    settings = Settings(database_url=url, web_url="http://localhost:3000", transport="stub",
                        clerk_secret_key="sk_test_throwaway", clerk_jwt_key=clerk.pem)
    with TestClient(create_app(settings, Catalogue(LISTINGS, "test"))) as c:
        def mine(phone: str):
            return c.get("/api/me/listings", headers={"Authorization": f"Bearer {clerk.token(phone=phone)}"})

        got = {x["listing_id"]: x for x in mine(PHONE).json()}
        assert set(got) == {"t:1", "t:2"}
        booked, unanswered = got["t:1"], got["t:2"]
        assert (booked["address"], booked["ended"], booked["monthly_cost"]) == ("1 Admin St", "talked", 2100)
        assert booked["call_id"] == LISTING_CALLS[0]
        assert [(f["field"], f["value"], f["quote"]) for f in booked["facts"]] == [
            ("parking_cost", "$180 a month per spot", "Parking is $180 a month.")]
        assert (booked["viewing"]["slot_text"], booked["viewing"]["status"]) == ("Saturday at two", "booked")
        assert (unanswered["ended"], unanswered["facts"], unanswered["viewing"]) == ("no_answer", [], None)
        assert mine(other_renter).json() == []                       # another renter sees none of these
        r = mine("+14165550999")
        assert (r.status_code, r.json()["detail"]) == (404, "unknown number")


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


def _wav(content: bytes) -> tuple[int, int, tuple[int, ...]]:
    with wave.open(io.BytesIO(content)) as w:
        frames = w.readframes(w.getnframes())
        return w.getnchannels(), w.getframerate(), struct.unpack(f"<{len(frames) // 2}h", frames)


def test_a_call_plays_back_from_its_recording_line_by_line(url):
    with _client(url) as c:
        call = c.get(f"/api/admin/calls/{RENTER_CALL}", headers=AUTH).json()
        assert call["audio"] is True
        assert [(t["audio_start_ms"], t["audio_end_ms"]) for t in call["transcript"]] == [(0, 1), (2, 4)]
        path = f"/api/admin/calls/{RENTER_CALL}/audio"
        assert c.get(path).status_code == 401
        r = c.get(path, headers=AUTH)
        assert r.status_code == 200 and r.headers["content-type"] == "audio/wav"
        channels, rate, samples = _wav(r.content)
        assert (channels, rate) == (2, 8000)                      # the renter left, the agent right
        assert samples[0::2] == (0,) * 16 + (-32124,) * 16
        assert samples[1::2] == (32124,) * 16 + (0,) * 16
        renter = call["transcript"][1]                            # just the renter's line
        r = c.get(path, headers=AUTH, params={"track": "caller", "from_ms": renter["audio_start_ms"],
                                              "to_ms": renter["audio_end_ms"]})
        assert _wav(r.content) == (1, 8000, (-32124,) * 16)
        assert c.get(path, headers=AUTH, params={"track": "nope"}).status_code == 422
        # A call with nothing recorded says so.
        assert c.get(f"/api/admin/calls/{LISTING_CALLS[0]}", headers=AUTH).json()["audio"] is False
        assert c.get(f"/api/admin/calls/{LISTING_CALLS[0]}/audio", headers=AUTH).status_code == 404
