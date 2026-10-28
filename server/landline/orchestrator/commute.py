"""TTC minutes to the place the renter names (D-10c, #237).

When a place resolves (AnchorResolved), a background task asks Google's Routes API for TRANSIT minutes from
each of the top 50 matching listings to it, in one computeRouteMatrix request (transit allows at most 100
elements), departing the next weekday at 08:30 Toronto time so they are a morning commute. The minutes go in
the feature matrix's cache per (place, listing) (`FeatureMatrix.use_commute`); the ranking only ever reads
that cache (hard rule 7), scoring location by minutes where it has them and by km elsewhere, and the cards
say "24 min by TTC to King and Bay".

Without GOOGLE_MAPS_API_KEY there is no Commute and nothing changes. Any error, timeout or refusal leaves the
km in place.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

import httpx

from landline.contracts import AnchorResolved, Event, SessionView
from landline.scoring import FeatureMatrix

log = logging.getLogger(__name__)

ROUTES_URL = "https://routes.googleapis.com/distanceMatrix/v2:computeRouteMatrix"
FIELDS = "originIndex,destinationIndex,duration,status,condition"
TOP = 50                  # listings asked about per place: the top of the board it produced
MAX_ELEMENTS = 100        # computeRouteMatrix with TRANSIT
TIMEOUT_S = 10
TORONTO = ZoneInfo("America/Toronto")
DEPART = time(8, 30)      # a weekday morning commute


def departure(now: datetime) -> datetime:
    """The next weekday at 08:30 Toronto time after `now`."""
    local = now.astimezone(TORONTO)
    day = local.date()
    while True:
        at = datetime.combine(day, DEPART, TORONTO)
        if at > local and at.weekday() < 5:
            return at
        day += timedelta(days=1)


class Commute:
    """Fills the matrix's TTC minutes for places renters name. One per process, like the matrix."""

    def __init__(self, matrix: FeatureMatrix, api_key: str, http: httpx.AsyncClient | None = None,
                 now: Callable[[], datetime] = lambda: datetime.now(UTC)):
        self.matrix, self.api_key, self.now = matrix, api_key, now
        self.http = http or httpx.AsyncClient(timeout=TIMEOUT_S)
        self.requests = self.elements = 0

    async def minutes(self, lat: float, lng: float, listing_ids: list[str]) -> dict[str, float]:
        """TTC minutes from each listing not yet known to the place at (lat, lng), in one request. Empty when
        all are known, or on any error (logged)."""
        known, index, m = self.matrix.commute_min(lat, lng), self.matrix.index, self.matrix
        todo = [lid for lid in dict.fromkeys(listing_ids)
                if lid in index and index[lid] not in known and m.lat[index[lid]] is not None][:MAX_ELEMENTS]
        if not todo:
            return {}
        body = {"origins": [_waypoint(m.lat[index[lid]], m.lng[index[lid]]) for lid in todo],
                "destinations": [_waypoint(lat, lng)], "travelMode": "TRANSIT",
                "departureTime": departure(self.now()).isoformat()}
        try:
            resp = await self.http.post(ROUTES_URL, json=body, headers={"X-Goog-Api-Key": self.api_key,
                                                                        "X-Goog-FieldMask": FIELDS})
        except httpx.HTTPError as e:
            log.warning("commute to (%.5f, %.5f): %s", lat, lng, e)
            return {}
        self.requests += 1
        self.elements += len(todo)
        if resp.status_code != 200:
            log.warning("commute to (%.5f, %.5f): HTTP %s %s", lat, lng, resp.status_code, resp.text[:200])
            return {}
        out: dict[str, float] = {}
        for e in resp.json() or []:
            i = e.get("originIndex", 0)
            failed = (e.get("status") or {}).get("code") or e.get("condition") != "ROUTE_EXISTS"
            if failed or "duration" not in e:
                continue
            if 0 <= i < len(todo):
                out[todo[i]] = round(float(str(e["duration"]).rstrip("s")) / 60, 1)
        return out

    async def on_anchor(self, event: Event, view: SessionView) -> bool:
        """The runtime's AnchorResolved subscriber: the top of the board this place produced gets its TTC
        minutes. Returns whether any were new. The board uses them at its next rebuild."""
        if not isinstance(event, AnchorResolved):
            return False
        got = await self.minutes(event.lat, event.lng, list(view.matches[:TOP]))
        new = self.matrix.use_commute(event.lat, event.lng, got)
        if got:
            log.info("commute to %r for %s: %d listings, %d new", event.label, event.session_id, len(got),
                     new)
        return new > 0

    async def close(self) -> None:
        await self.http.aclose()


def _waypoint(lat: float, lng: float) -> dict:
    return {"waypoint": {"location": {"latLng": {"latitude": lat, "longitude": lng}}}}
