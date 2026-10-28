"""GET /api/me/listings (U-12c): every listing Landline called for the signed-in renter, newest first, built
from stored rows only (calls, and each session's snapshot of the Auditor's outcomes). With no database here:
the builder on rows shaped like the database's, and the route's sign-in gates. The database case is in
test_admin.py."""
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from landline.api.admin import Place
from landline.api.me import listings
from landline.app import create_app
from landline.config import Settings

T0 = datetime(2026, 10, 24, 16, 0, tzinfo=UTC)


def _place(lid: str) -> Place:
    return Place(listing_id=lid, address=f"{lid[-1]} Main St", unit_label="Unit 4" if lid == "t:3" else None)


def _call(cid: str, lid: str, minutes: int, **kw) -> dict:
    return {"call_id": cid, "session_id": "s_1", "role": "listing", "listing_id": lid, "state": "ended",
            "end_reason": "done", "created_at": T0 + timedelta(minutes=minutes), "answered_at": None, **kw}


SNAPSHOT = {
    "state": {
        "outcomes": {
            "t:1": {"call_id": "c_1", "listing_id": "t:1", "end_reason": "done",
                    "facts": [{"field": "parking_cost", "value": "$180 a month per spot",
                               "quote": "Parking is $180 a month.", "speaker": "Agent heard",
                               "at": "2026-10-24T16:05:00Z"}],
                    "red_flags": [],
                    "viewing": {"starts_at": "2026-10-25T18:00:00Z", "slot_text": "Sunday at two"}},
            "t:2": {"call_id": "c_2", "listing_id": "t:2", "end_reason": "done", "facts": [],
                    "red_flags": ["Wants a deposit by e-transfer before anyone sees the unit."]},
            "t:4": {"call_id": "c_old", "listing_id": "t:4", "end_reason": "done", "facts": []},
        },
    },
    "view": {"board": [{"listing_id": "t:1", "viewing": {"status": "confirmed"}}]},
}
SESSIONS = [{"session_id": "s_1", "snapshot": SNAPSHOT}]


def test_each_dialled_call_with_its_facts_flags_and_viewing_newest_first():
    calls = [_call("c_1", "t:1", 1, answered_at=T0 + timedelta(minutes=2)),
             _call("c_2", "t:2", 3),
             _call("c_3", "t:3", 5, end_reason="voicemail", state="voicemail"),
             _call("c_4", "t:4", 7, end_reason="no_answer"),        # outcome is another call's: not shown
             _call("c_5", "t:5", 9, state="queued", end_reason=None),   # never dialled
             _call("c_6", "t:6", 9, state="ended", end_reason="blocked"),
             {**_call("c_r", "", 0), "role": "renter", "listing_id": None}]
    got = listings(SESSIONS, calls, _place)
    assert [(x.call_id, x.ended) for x in got] == [("c_4", "no_answer"), ("c_3", "voicemail"),
                                                   ("c_2", "talked"), ("c_1", "talked")]
    first = got[-1]
    assert (first.address, first.called_at) == ("1 Main St", T0 + timedelta(minutes=2))
    [fact] = first.facts
    assert (fact.field, fact.value) == ("parking_cost", "$180 a month per spot")
    assert fact.quote == "Parking is $180 a month."   # its source: the agent's own words
    assert first.viewing is not None and (first.viewing.slot_text, first.viewing.status) == ("Sunday at two",
                                                                                          "confirmed")
    assert got[2].red_flags == ["Wants a deposit by e-transfer before anyone sees the unit."]
    assert got[2].viewing is None and got[0].facts == [] and got[1].unit_label == "Unit 4"


def test_a_viewing_without_a_stored_status_reads_as_booked():
    snapshot = {"state": {"outcomes": {}, "viewings": {"t:1": {"starts_at": "2026-10-25T18:00:00Z",
                                                               "slot_text": "Sunday at two"}}}}
    [x] = listings([{"session_id": "s_1", "snapshot": snapshot}], [_call("c_1", "t:1", 1)], _place)
    assert x.viewing is not None and x.viewing.status == "booked"


def test_a_renter_with_no_calls_has_no_listings():
    assert listings([], [], _place) == []


def _client(clerk=None) -> TestClient:
    settings = Settings(database_url=None, web_url="http://localhost:3000", transport="stub",
                        clerk_secret_key="sk_test_throwaway" if clerk else None,
                        clerk_jwt_key=clerk.pem if clerk else None)
    return TestClient(create_app(settings))


def test_the_route_needs_sign_in(clerk):
    with _client() as c:
        assert c.get("/api/me/listings").json()["detail"] == "sign-in is off"
    with _client(clerk) as c:
        assert c.get("/api/me/listings").status_code == 401
        bad = c.get("/api/me/listings", headers={"Authorization": f"Bearer {clerk.token(expires_in=-60)}"})
        assert bad.status_code == 401
        ok = c.get("/api/me/listings", headers={"Authorization": f"Bearer {clerk.token()}"})
        assert (ok.status_code, ok.json()["detail"]) == (404, "history is off")   # verified; no database here
