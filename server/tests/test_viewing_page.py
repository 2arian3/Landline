"""The confirm page's API (O-08d): GET /api/viewings/{token}, POST …/confirm with {email}, POST …/decline. The
token is the only key. With no database, fakes stand in for viewing_confirmations, the session and O-08c's
Decisions; the last test runs the real ones on Postgres (a throwaway schema; skips without one)."""
import os
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace

from fastapi.testclient import TestClient

from landline.app import create_app
from landline.config import Settings
from landline.db.catalogue import Catalogue
from landline.orchestrator.viewings import Answer
from tests.test_viewings import CID, LISTINGS, SAT_2PM, _booking, db  # noqa: F401

TOKEN, OTHER = "tok_good", "tok_unknown"
CONFIRMED = Answer("confirmed", "confirmed")


@dataclass
class Row:
    token: str = TOKEN
    session_id: str = "s_page000001"
    listing_id: str = "t:1"
    starts_at: datetime = SAT_2PM
    status: str = "pending"


class FakeDb:
    def __init__(self, row: Row | None, name: str | None = "Sam Lee"):
        plan = {"shareable": {"name": name}} if name is not None else {"shareable": {}}
        snapshot = {"state": {"plan": plan}}
        self.confirmations = SimpleNamespace(get=self._get)
        self.sessions = SimpleNamespace(get=self._session)
        self.row, self.snapshot = row, snapshot

    async def _get(self, token):
        return self.row if self.row and token == self.row.token else None

    async def _session(self, session_id):
        return SimpleNamespace(snapshot=self.snapshot)


class FakeDecisions:
    def __init__(self, answer: Answer):
        self.answer, self.calls = answer, []

    async def confirm(self, token, email):
        self.calls.append(("confirm", token, email))
        return self.answer if token == TOKEN else Answer("not_found")

    async def decline(self, token):
        self.calls.append(("decline", token))
        return self.answer if token == TOKEN else Answer("not_found")


@contextmanager
def _client(row: Row | None = None, answer: Answer = CONFIRMED,
            name: str | None = "Sam Lee"):
    settings = Settings(database_url=None, web_url="http://localhost:3000", transport="stub")
    with TestClient(create_app(settings, Catalogue(LISTINGS, "test"))) as c:
        c.app.state.db = FakeDb(row, name)
        c.app.state.viewing_decisions = FakeDecisions(answer)
        yield c


def test_a_good_token_shows_the_time_the_address_and_a_shared_first_name_only():
    with _client(Row()) as c:
        got = c.get(f"/api/viewings/{TOKEN}").json()
        assert got == {"status": "pending", "starts_at": "2026-10-31T18:00:00Z",
                       "when": "Saturday, Oct 31 at 2 pm", "address": "112 Markham St",
                       "renter_first_name": "Sam"}


def test_a_name_the_renter_didnt_share_stays_off_the_page():
    with _client(Row(), name=None) as c:
        assert c.get(f"/api/viewings/{TOKEN}").json()["renter_first_name"] is None


def test_an_unknown_token_is_404_for_every_route():
    with _client(Row()) as c:
        for r in (c.get(f"/api/viewings/{OTHER}"), c.post(f"/api/viewings/{OTHER}/decline"),
                  c.post(f"/api/viewings/{OTHER}/confirm", json={"email": "agent@example.com"})):
            assert (r.status_code, r.json()["detail"]) == (404, "unknown link")


def test_a_confirm_takes_a_checked_email_and_a_bad_one_never_reaches_confirm():
    with _client(Row()) as c:
        for bad in ("agent@", "not an email", "", " @example.com"):
            r = c.post(f"/api/viewings/{TOKEN}/confirm", json={"email": bad})
            assert (r.status_code, r.json()["detail"]) == (422, "not an email address"), bad
        assert c.app.state.viewing_decisions.calls == []
        r = c.post(f"/api/viewings/{TOKEN}/confirm", json={"email": "  agent@example.com "})
        assert (r.status_code, r.json()) == (200, {"status": "confirmed", "invite_sent": False})
        assert c.app.state.viewing_decisions.calls == [("confirm", TOKEN, "agent@example.com")]


def test_a_decline_needs_nothing_else():
    with _client(Row(), answer=Answer("declined", "declined")) as c:
        r = c.post(f"/api/viewings/{TOKEN}/decline")
        assert (r.status_code, r.json()) == (200, {"status": "declined", "invite_sent": False})


def test_a_token_already_decided_says_how():
    with _client(Row(status="confirmed"), answer=Answer("already_decided", "confirmed")) as c:
        assert c.get(f"/api/viewings/{TOKEN}").json()["status"] == "confirmed"
        for r in (c.post(f"/api/viewings/{TOKEN}/decline"),
                  c.post(f"/api/viewings/{TOKEN}/confirm", json={"email": "agent@example.com"})):
            decided = {"message": "already decided", "status": "confirmed"}
            assert (r.status_code, r.json()["detail"]) == (409, decided)


async def test_the_page_answers_once_on_the_real_tables(db, schema):   # noqa: F811
    """O-08c's Decisions and viewing_confirmations behind the routes, with no Google keys and no texts."""
    await _booking(db)
    token = (await db.confirmations.for_call(CID)).token
    url = f"{os.environ['TEST_DATABASE_URL']}?options=-csearch_path%3D{schema[0]},public"
    settings = Settings(database_url=url, web_url="http://localhost:3000", transport="stub")
    with TestClient(create_app(settings, Catalogue(LISTINGS, "test"))) as c:
        got = c.get(f"/api/viewings/{token}").json()
        assert (got["status"], got["address"], got["when"]) == ("pending", "112 Markham St",
                                                                  "Saturday, Oct 31 at 2 pm")
        assert c.post(f"/api/viewings/{token}/confirm", json={"email": "agent@"}).status_code == 422
        assert c.post(f"/api/viewings/{token}/confirm", json={"email": "agent@example.com"}).json() == {
            "status": "confirmed", "invite_sent": False}   # no Google keys here: no invite
        again = c.post(f"/api/viewings/{token}/decline")
        assert (again.status_code, again.json()["detail"]["status"]) == (409, "confirmed")
        assert c.get(f"/api/viewings/{token}").json()["status"] == "confirmed"
        assert c.get("/api/viewings/tok_nobody").status_code == 404
    row = await db.confirmations.get(token)
    assert (row.status, row.agent_email) == ("confirmed", "agent@example.com")
    assert row.starts_at == datetime(2026, 10, 31, 18, 0, tzinfo=UTC)
