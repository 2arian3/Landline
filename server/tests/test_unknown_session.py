"""A link that matches no call (PR9, edge states): a session id in the shape code mints for a call, which the
runtime doesn't hold and Postgres never stored, is 404, not a new empty demo board. Any other id is still a
demo session made on first sight, as /map's `demo-…` ids and the scripts' smoke ids need."""
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from landline.app import create_app
from landline.config import Settings
from landline.contracts import Listing, SessionStarted
from landline.db.catalogue import Catalogue

NOW = datetime(2026, 10, 28, 22, 0, tzinfo=UTC)
LOST = "s_Ab3-x_9QzK"   # `s_` + 10 url-safe characters, as voice/calls.py mints them
LISTING = Listing(listing_id="t:1", source="t", source_id="1", url="https://example.com/1", first_seen=NOW,
                  last_seen=NOW, address="1 Main St", lat=43.65, lng=-79.38, rent=2400, monthly_cost=2400,
                  beds=1)


def _client() -> TestClient:
    settings = Settings(database_url=None, web_url="http://localhost:3000", transport="stub")
    return TestClient(create_app(settings, Catalogue([LISTING], "test")))


def test_a_minted_id_that_matches_no_call_is_404_for_the_snapshot_the_stream_and_a_tap():
    with _client() as c:
        for r in (c.get(f"/api/sessions/{LOST}"), c.get(f"/api/sessions/{LOST}/stream"),
                  c.post(f"/api/sessions/{LOST}/focus", json={"listing_id": "t:1"}),
                  c.post(f"/api/sessions/{LOST}/feedback", json={"listing_id": "t:1", "reaction": "like"})):
            assert (r.status_code, r.json()["detail"]) == (404, "unknown session")
        assert LOST not in c.app.state.sessions.views   # nothing was made under its id


def test_a_live_session_with_that_shape_still_answers():
    with _client() as c:
        sid = "s_live000001"
        started = SessionStarted(session_id=sid, at=NOW, idem_key=f"{sid}:started", channel="judge")
        c.portal.call(c.app.state.runtime.start, started)
        assert c.get(f"/api/sessions/{sid}").json()["session_id"] == sid


def test_any_other_id_is_still_a_demo_session_made_on_first_sight():
    with _client() as c:
        for sid in ("demo-1a2b3c4d", "smoke-375-mhb2x", "s_back", "s_toolongforaminted"):
            r = c.get(f"/api/sessions/{sid}")
            assert (r.status_code, r.json()["version"], r.json()["phase"]) == (200, 1, "open"), sid
