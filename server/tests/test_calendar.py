"""Viewing invites (O-08a): the Google Calendar client against a fake Google, and the add-to-calendar link.
Nothing leaves the machine."""
import json
import logging
from datetime import UTC, datetime
from urllib.parse import parse_qs

import httpx
import pytest

from landline.config import Settings
from landline.orchestrator.calendar import Calendar, CalendarError, add_to_calendar_link, from_settings

SAT_2PM = datetime(2026, 10, 31, 18, 0, tzinfo=UTC)   # Saturday 2:00 PM in Toronto (EDT, -04:00)
SECRET, REFRESH, ACCESS = "csecret", "1//refresh-tok", "ya29.access-tok"


class Google:
    """A fake token endpoint and Calendar API that records what was asked."""

    def __init__(self, token_status: int = 200, insert_status: int = 200):
        self.token_status, self.insert_status = token_status, insert_status
        self.tokens: list[httpx.Request] = []
        self.inserts: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2.googleapis.com":
            self.tokens.append(request)
            if self.token_status != 200:
                return httpx.Response(self.token_status, json={
                    "error": "invalid_grant", "error_description": "Token has been expired or revoked."})
            return httpx.Response(200, json={"access_token": ACCESS, "expires_in": 3599, "scope": "x",
                                             "token_type": "Bearer"})
        self.inserts.append(request)
        if self.insert_status != 200:
            return httpx.Response(self.insert_status, json={"error": {
                "code": self.insert_status, "message": "Request had invalid authentication credentials."}})
        return httpx.Response(200, json={"id": f"evt{len(self.inserts)}", "status": "confirmed"})


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _calendar(google: Google, clock: Clock | None = None) -> Calendar:
    return Calendar("cid.apps.googleusercontent.com", SECRET, REFRESH,
                    client=httpx.AsyncClient(transport=httpx.MockTransport(google)), clock=clock or Clock())


async def _book(cal: Calendar, attendees=("agent@example.com", "renter@example.com")) -> str:
    return await cal.create_event("Viewing: 12 Grange Ave", "12 Grange Ave, Toronto",
                                  "Dogs allowed. Parking $150 a month.", SAT_2PM, list(attendees))


async def test_an_insert_invites_both_people_for_30_minutes_in_toronto_time():
    google = Google()
    cal = _calendar(google)
    assert await _book(cal) == "evt1"
    token = parse_qs(google.tokens[0].content.decode())
    assert token == {"grant_type": ["refresh_token"], "client_id": ["cid.apps.googleusercontent.com"],
                     "client_secret": [SECRET], "refresh_token": [REFRESH]}
    insert = google.inserts[0]
    assert insert.method == "POST"
    assert insert.url.copy_with(query=None) == "https://www.googleapis.com/calendar/v3/calendars/primary/events"
    assert insert.url.params["sendUpdates"] == "all"
    assert insert.headers["authorization"] == f"Bearer {ACCESS}"
    assert json.loads(insert.content) == {
        "summary": "Viewing: 12 Grange Ave", "location": "12 Grange Ave, Toronto",
        "description": "Dogs allowed. Parking $150 a month.",
        "start": {"dateTime": "2026-10-31T14:00:00-04:00", "timeZone": "America/Toronto"},
        "end": {"dateTime": "2026-10-31T14:30:00-04:00", "timeZone": "America/Toronto"},
        "attendees": [{"email": "agent@example.com"}, {"email": "renter@example.com"}],
    }
    await cal.close()


async def test_the_access_token_is_cached_until_shortly_before_it_expires():
    google, clock = Google(), Clock()
    cal = _calendar(google, clock)
    await _book(cal)
    clock.now += 3000
    await _book(cal)
    assert len(google.tokens) == 1 and len(google.inserts) == 2
    clock.now += 560   # 3560 s in: inside the last minute of the token's 3599 s
    await _book(cal)
    assert len(google.tokens) == 2
    await cal.close()


async def test_a_refused_refresh_raises_with_googles_reason_and_no_secret(caplog):
    caplog.set_level(logging.DEBUG)
    google = Google(token_status=400)
    cal = _calendar(google)
    with pytest.raises(CalendarError) as err:
        await _book(cal)
    assert err.value.status == 400
    assert "invalid_grant" in str(err.value)
    assert google.inserts == []
    for text in (str(err.value), caplog.text):
        assert SECRET not in text and REFRESH not in text and ACCESS not in text
    await cal.close()


async def test_a_refused_insert_raises_and_a_401_drops_the_cached_token(caplog):
    caplog.set_level(logging.DEBUG)
    google = Google(insert_status=401)
    cal = _calendar(google)
    with pytest.raises(CalendarError) as err:
        await _book(cal)
    assert err.value.status == 401 and "invalid authentication credentials" in str(err.value)
    assert ACCESS not in str(err.value) and ACCESS not in caplog.text
    google.insert_status = 200
    assert await _book(cal) == "evt2"
    assert len(google.tokens) == 2   # refreshed rather than reusing the refused token
    await cal.close()


async def test_an_unreachable_google_raises():
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("")

    cal = Calendar("cid", SECRET, REFRESH, client=httpx.AsyncClient(transport=httpx.MockTransport(down)))
    with pytest.raises(CalendarError, match="ConnectTimeout") as err:
        await _book(cal)
    assert err.value.status is None
    await cal.close()


def test_without_all_three_keys_there_is_no_client():
    base = Settings(database_url=None, web_url="http://localhost:3000", transport="stub")
    assert from_settings(base) is None
    two = Settings(database_url=None, web_url="", transport="stub", google_oauth_client_id="cid",
                   google_oauth_client_secret=SECRET)
    assert from_settings(two) is None
    three = Settings(database_url=None, web_url="", transport="stub", google_oauth_client_id="cid",
                     google_oauth_client_secret=SECRET, google_oauth_refresh_token=REFRESH)
    assert isinstance(from_settings(three), Calendar)


def test_settings_read_the_three_keys(monkeypatch):
    monkeypatch.setattr("landline.config.load_dotenv", lambda *_a, **_k: None)
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "cid")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", SECRET)
    monkeypatch.setenv("GOOGLE_OAUTH_REFRESH_TOKEN", REFRESH)
    s = Settings.load()
    assert (s.google_oauth_client_id, s.google_oauth_client_secret, s.google_oauth_refresh_token) == (
        "cid", SECRET, REFRESH)


def test_the_add_to_calendar_link_has_the_same_30_minutes_in_utc():
    assert add_to_calendar_link("Viewing: 12 Grange Ave", "12 Grange Ave, Toronto",
                                "Dogs allowed. Parking $150/month & up.", SAT_2PM) == (
        "https://calendar.google.com/calendar/render?action=TEMPLATE&text=Viewing%3A%2012%20Grange%20Ave"
        "&dates=20261031T180000Z/20261031T183000Z"
        "&details=Dogs%20allowed.%20Parking%20%24150/month%20%26%20up."
        "&location=12%20Grange%20Ave%2C%20Toronto")


def test_a_time_without_a_zone_is_refused():
    with pytest.raises(ValueError):
        add_to_calendar_link("x", "y", "z", datetime(2026, 10, 31, 14, 0))
