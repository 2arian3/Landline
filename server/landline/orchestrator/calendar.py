"""Google Calendar (O-08a): a viewing invite from Landline's own Google account, over httpx.

A service account can't invite guests without a Workspace domain, so invites come from one Landline Google
account, signed in once with OAuth: `scripts/google_oauth.py` prints its refresh token. The client trades
that for an access token, keeps it until shortly before it expires, and inserts a 30-minute event on the
account's primary calendar with sendUpdates=all, so Google emails each attendee the invite.

Without GOOGLE_OAUTH_CLIENT_ID, GOOGLE_OAUTH_CLIENT_SECRET and GOOGLE_OAUTH_REFRESH_TOKEN, `from_settings`
returns None and nothing is sent. A renter with no email gets `add_to_calendar_link` by SMS instead. Times
and emails come from the code that calls this, never from a model (hard rule 2). No token is ever logged or
put in an error.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from urllib.parse import quote, urlencode
from zoneinfo import ZoneInfo

import httpx

from landline.config import Settings

log = logging.getLogger(__name__)

TOKEN_URL = "https://oauth2.googleapis.com/token"
EVENTS_URL = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
RENDER_URL = "https://calendar.google.com/calendar/render"
ZONE = "America/Toronto"
TORONTO = ZoneInfo(ZONE)
VIEWING = timedelta(minutes=30)
TIMEOUT_S = 10
EARLY_S = 60   # a cached access token is refreshed this long before Google says it expires


class CalendarError(Exception):
    """Google refused a token refresh or an insert (`status` is its HTTP status), or couldn't be reached
    (`status` is None). The message has the status and Google's reason, never a token."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class Calendar:
    def __init__(self, client_id: str, client_secret: str, refresh_token: str,
                 client: httpx.AsyncClient | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._client_id, self._client_secret, self._refresh_token = client_id, client_secret, refresh_token
        self.client = client or httpx.AsyncClient(timeout=TIMEOUT_S)
        self._clock = clock
        self._token: str | None = None
        self._expires = 0.0
        self._refreshing = asyncio.Lock()

    async def access_token(self) -> str:
        """An access token for the Calendar API, from the cache while it's still good."""
        async with self._refreshing:   # sessions confirming at once share one refresh
            if self._token and self._clock() < self._expires:
                return self._token
            r = await self._request("POST", TOKEN_URL, "token refresh", data={
                "grant_type": "refresh_token", "client_id": self._client_id,
                "client_secret": self._client_secret, "refresh_token": self._refresh_token})
            body = r.json()
            if not body.get("access_token"):
                raise CalendarError("token refresh returned no access token")
            self._token = body["access_token"]
            self._expires = self._clock() + float(body.get("expires_in") or 3600) - EARLY_S
            return self._token

    async def create_event(self, summary: str, location: str, description: str, starts_at: datetime,
                           attendees: Sequence[str]) -> str:
        """Inserts a 30-minute event in Toronto time that invites `attendees` (Google emails each one) and
        returns its event id. Raises CalendarError when Google refuses it."""
        start, end = _span(starts_at)
        guests = list(dict.fromkeys(e.strip() for e in attendees if e and e.strip()))
        body = {
            "summary": summary, "location": location, "description": description,
            "start": {"dateTime": start.astimezone(TORONTO).isoformat(), "timeZone": ZONE},
            "end": {"dateTime": end.astimezone(TORONTO).isoformat(), "timeZone": ZONE},
            "attendees": [{"email": e} for e in guests],
        }
        token = await self.access_token()
        try:
            r = await self._request("POST", EVENTS_URL, "event insert", params={"sendUpdates": "all"},
                                    json=body, headers={"Authorization": f"Bearer {token}"})
        except CalendarError as e:
            if e.status == 401:   # revoked or expired early: the next call refreshes
                self._token = None
            raise
        event_id = r.json()["id"]
        log.info("calendar event %s created, inviting %d", event_id, len(guests))
        return event_id

    async def _request(self, method: str, url: str, what: str, **kw) -> httpx.Response:
        try:
            r = await self.client.request(method, url, **kw)
        except httpx.HTTPError as e:   # a timeout's message is empty, so its repr says what failed
            raise CalendarError(f"{what} failed ({e!r})") from None
        if r.status_code >= 300:
            raise CalendarError(f"{what} refused ({r.status_code}): {_reason(r)}", r.status_code)
        return r

    async def close(self) -> None:
        await self.client.aclose()


def from_settings(settings: Settings) -> Calendar | None:
    """The client, or None unless all three Google keys are set."""
    keys = (settings.google_oauth_client_id, settings.google_oauth_client_secret,
            settings.google_oauth_refresh_token)
    if not all(keys):
        return None
    return Calendar(*keys)


def add_to_calendar_link(summary: str, location: str, description: str, starts_at: datetime) -> str:
    """An "Add to Google Calendar" link for the same 30 minutes, for a renter with no email. Google's
    template takes UTC times."""
    start, end = _span(starts_at)
    stamp = "%Y%m%dT%H%M%SZ"
    params = {"action": "TEMPLATE", "text": summary,
              "dates": f"{start:{stamp}}/{end:{stamp}}",
              "details": description, "location": location}
    return f"{RENDER_URL}?{urlencode(params, quote_via=quote, safe='/')}"


def _span(starts_at: datetime) -> tuple[datetime, datetime]:
    """The viewing's start and end in UTC, so the 30 minutes are real ones even across a clock change."""
    if starts_at.tzinfo is None:
        raise ValueError("starts_at needs a timezone")
    start = starts_at.astimezone(UTC)
    return start, start + VIEWING


def _reason(r: httpx.Response) -> str:
    """Google's reason for a refusal: the token endpoint's `error` and `error_description`, or the Calendar
    API's `error.message`. Neither carries a token."""
    try:
        body = r.json()
    except ValueError:
        return r.text[:200]
    err = body.get("error") if isinstance(body, dict) else None
    if isinstance(err, dict):
        return str(err.get("message") or err.get("status") or err)
    if err:
        return f"{err}: {body.get('error_description', '')}".rstrip(": ")
    return r.text[:200]
