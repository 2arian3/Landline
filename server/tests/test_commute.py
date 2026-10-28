"""TTC minutes to the place the renter names (D-10c): the Routes client, the background step after
AnchorResolved, and the board using the minutes. No network: an httpx.MockTransport stands in for Google."""
import json
from datetime import UTC, datetime

import httpx
import pytest

from landline.contracts import AnchorResolved, Listing, ListingFocused, PreferencesUpdated, SessionStarted
from landline.contracts.tools import AnchorRef, PreferenceDelta
from landline.db.catalogue import Catalogue
from landline.orchestrator import SessionRuntime, commute
from landline.orchestrator.commute import TORONTO, Commute, departure

NOW = datetime(2026, 10, 28, 4, 50, tzinfo=UTC)   # Wednesday 00:50 in Toronto
SID = "s_commute001"
KING_AND_BAY = (43.6487, -79.3817)


def _listing(i: int, lat: float, rent: int = 2000) -> Listing:
    return Listing(listing_id=f"t:{i}", source="t", source_id=str(i), url=f"https://example.com/{i}",
                   first_seen=NOW, last_seen=NOW, address=f"{i} Commute St", lat=lat, lng=-79.3817,
                   rent=rent, monthly_cost=rent, beds=1, sqft=650)


# Two listings the same km from King and Bay (one north, one south) and one a little farther.
LISTINGS = [_listing(1, 43.6587), _listing(2, 43.6387), _listing(3, 43.6600)]
MINUTES = {"t:1": 41.0, "t:2": 13.0, "t:3": 22.5}


class Google:
    """A fake Routes API: TRANSIT minutes from MINUTES by origin, and every request kept."""

    def __init__(self, minutes=None, status: int = 200):
        self.minutes, self.status, self.seen, self.ids = minutes or MINUTES, status, [], {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.seen.append((request, body))
        if self.status != 200:
            return httpx.Response(self.status, json={"error": {"code": self.status}})
        by_lat = {round(x.lat, 5): x.listing_id for x in LISTINGS}
        out = []
        for i, o in enumerate(body["origins"]):
            lid = by_lat[round(o["waypoint"]["location"]["latLng"]["latitude"], 5)]
            m = self.minutes.get(lid)
            out.append({"originIndex": i, "destinationIndex": 0, "status": {},
                        **({"condition": "ROUTE_EXISTS", "duration": f"{int(m * 60)}s"} if m is not None
                           else {"condition": "ROUTE_NOT_FOUND"})})
        return httpx.Response(200, json=out)


def _commute(matrix, fake: Google) -> Commute:
    return Commute(matrix, "test-key", http=httpx.AsyncClient(transport=httpx.MockTransport(fake)),
                   now=lambda: NOW)


@pytest.mark.parametrize(("now", "departs"), [
    (datetime(2026, 10, 28, 0, 50, tzinfo=TORONTO), datetime(2026, 10, 28, 8, 30, tzinfo=TORONTO)),   # Wed
    (datetime(2026, 10, 28, 9, 0, tzinfo=TORONTO), datetime(2026, 10, 29, 8, 30, tzinfo=TORONTO)),    # Thu
    (datetime(2026, 10, 30, 9, 0, tzinfo=TORONTO), datetime(2026, 11, 2, 8, 30, tzinfo=TORONTO)),     # Mon
    (datetime(2026, 10, 31, 12, 0, tzinfo=TORONTO), datetime(2026, 11, 2, 8, 30, tzinfo=TORONTO)),    # Sat
])
def test_the_commute_leaves_the_next_weekday_at_8_30_in_toronto(now, departs):
    assert departure(now) == departs


async def test_one_transit_request_for_the_listings_not_yet_known():
    runtime = SessionRuntime(Catalogue(LISTINGS, "test"))
    fake = Google(minutes={"t:1": 41.0, "t:2": 13.0})   # no transit route from t:3
    client = _commute(runtime.matrix, fake)
    got = await client.minutes(*KING_AND_BAY, ["t:1", "t:2", "t:3", "t:nope"])
    assert got == {"t:1": 41.0, "t:2": 13.0}
    request, body = fake.seen[0]
    assert str(request.url) == commute.ROUTES_URL and request.headers["X-Goog-Api-Key"] == "test-key"
    assert request.headers["X-Goog-FieldMask"] == "originIndex,destinationIndex,duration,status,condition"
    assert body["travelMode"] == "TRANSIT" and len(body["origins"]) == 3 and len(body["destinations"]) == 1
    assert body["departureTime"] == "2026-10-28T08:30:00-04:00"
    runtime.matrix.use_commute(*KING_AND_BAY, got)
    fake.seen.clear()
    assert await client.minutes(*KING_AND_BAY, ["t:1", "t:2"]) == {}   # cached per (place, listing)
    assert fake.seen == []
    await client.close()


async def test_a_refused_request_leaves_the_km():
    runtime = SessionRuntime(Catalogue(LISTINGS, "test"))
    client = _commute(runtime.matrix, Google(status=403))
    assert await client.minutes(*KING_AND_BAY, ["t:1"]) == {}
    assert runtime.matrix.commute_min(*KING_AND_BAY) == {}
    await client.close()


async def _session(runtime: SessionRuntime) -> None:
    await runtime.start(SessionStarted(session_id=SID, at=NOW, idem_key=f"{SID}:start", channel="judge"))
    delta = PreferenceDelta(max_monthly=3000, priorities=["location"],
                            anchors=[AnchorRef(label="King and Bay")])
    await runtime.emit(PreferencesUpdated(session_id=SID, at=NOW, idem_key=f"{SID}:prefs", delta=delta))
    await runtime.emit(AnchorResolved(session_id=SID, at=NOW, idem_key=f"{SID}:place", label="King and Bay",
                                      lat=KING_AND_BAY[0], lng=KING_AND_BAY[1]))


def _with_fake(runtime: SessionRuntime, fake: Google) -> None:
    runtime.commute.http = httpx.AsyncClient(transport=httpx.MockTransport(fake))
    runtime.commute.now = lambda: NOW


async def test_without_a_key_nothing_changes():
    runtime = SessionRuntime(Catalogue(LISTINGS, "test"))
    assert runtime.commute is None
    await _session(runtime)
    await runtime.idle()
    assert runtime.matrix.commute_min(*KING_AND_BAY) == {}
    assert all("min by TTC" not in r for c in runtime.get(SID).board for r in c.reasons)
    await runtime.close()


async def test_the_minutes_land_in_the_background_and_the_next_rebuild_uses_them():
    runtime = SessionRuntime(Catalogue(LISTINGS, "test"), maps_key="test-key")
    fake = Google()
    _with_fake(runtime, fake)
    await _session(runtime)
    by_km = [c.listing_id for c in runtime.get(SID).board]
    await runtime.idle()   # the background step after AnchorResolved
    assert runtime.matrix.commute_min(*KING_AND_BAY) == {0: 41.0, 1: 13.0, 2: 22.5}
    assert len(fake.seen) == 1 and len(fake.seen[0][1]["origins"]) == 3
    # Without a re-rank event of its own (the base behaviour), the board picks them up at its next commit.
    version = runtime.get(SID).version
    view = await runtime.emit(ListingFocused(session_id=SID, at=NOW, idem_key=f"{SID}:tap", listing_id="t:3",
                                             origin="ui"))
    assert view.version == version + 1
    by_ttc = [c.listing_id for c in view.board]
    assert by_ttc[0] == "t:2" and by_ttc != by_km
    card = next(c for c in view.board if c.listing_id == "t:2")
    assert "13 min by TTC to King and Bay" in card.reasons
    await runtime.close()
