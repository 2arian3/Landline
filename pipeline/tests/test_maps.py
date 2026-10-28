"""Google Maps walking minutes to the TTC (D-10a): the client, its cache and pace, the station step, and the
load into Postgres. No network: requests go to an httpx.MockTransport. fixtures/maps/walk_matrix.json has the
shape of Google's documented computeRouteMatrix response (elements in any order, a zero index left out, a
route not found, an element error); replace it with a recorded one after the first live run."""
import json
from pathlib import Path

import httpx
import pytest

from pipeline import __main__ as cli
from pipeline import geo, maps
from pipeline.tests.test_geo import HOODS, STATIONS, conn  # noqa: F401  (conn is a fixture)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "maps"
DAY = "2026-10-28"
# Three stations along Bloor, west to east, and two listings just south of them.
BLOOR = [{"id": "s:christie", "name": "Christie", "lat": 43.6643, "lng": -79.4185},
         {"id": "s:bathurst", "name": "Bathurst", "lat": 43.6660, "lng": -79.4112},
         {"id": "s:spadina", "name": "Spadina", "lat": 43.6672, "lng": -79.4037}]
HOMES = [{"listing_id": "t:a", "lat": 43.6600, "lng": -79.4160},
         {"listing_id": "t:b", "lat": 43.6610, "lng": -79.4150}]


class Google:
    """A fake Routes API: answers from `reply(body)` and keeps every request."""

    def __init__(self, reply=None, status: int = 200, places=None):
        self.reply, self.status, self.seen, self.places = reply, status, [], places

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.seen.append((request, body))
        if request.url.host == "places.googleapis.com":   # D-10b's searchNearby: nothing close by default
            return httpx.Response(200, json=self.places(body) if self.places else {})
        if self.status != 200:
            error = {"code": self.status, "status": "RESOURCE_EXHAUSTED"}
            return httpx.Response(self.status, json={"error": error})
        return httpx.Response(200, json=self.reply(body) if self.reply else [])


def _fixture(body: dict) -> list:
    return json.loads((FIXTURES / "walk_matrix.json").read_text())


def _by_distance(body: dict) -> list:
    """Every pair routable: one second per metre of straight line × 1.2, for the bigger tests."""
    out = []
    for i, o in enumerate(body["origins"]):
        for j, d in enumerate(body["destinations"]):
            a, b = (_ll(o), _ll(d))
            metres = maps._km(a, b) * 1000 * 1.2
            out.append({"originIndex": i, "destinationIndex": j, "status": {}, "condition": "ROUTE_EXISTS",
                        "distanceMeters": round(metres), "duration": f"{round(metres)}s"})
    return out


def _ll(w: dict) -> tuple[float, float]:
    p = w["waypoint"]["location"]["latLng"]
    return p["latitude"], p["longitude"]


def _client(tmp_path: Path, fake: Google, clock=None, sleep=None) -> maps.GoogleMaps:
    http = httpx.Client(transport=httpx.MockTransport(fake))
    return maps.GoogleMaps("test-key", cache_dir=tmp_path, http=http, clock=clock or (lambda: 0.0),
                           sleep=sleep or (lambda s: None), today=lambda: DAY)


def test_without_a_key_the_stage_says_so_and_changes_nothing(monkeypatch, capsys):
    monkeypatch.setenv("GOOGLE_MAPS_API_KEY", "")
    monkeypatch.setattr(cli, "load_dotenv", lambda: None)
    assert maps.from_env() is None
    assert cli.main(["maps", "--load"]) == 0
    assert capsys.readouterr().out.strip() == maps.SKIP


def test_the_nearest_stations_are_by_straight_line():
    near = maps.nearest_stations((43.6600, -79.4160), BLOOR, k=2)
    assert [s["name"] for s in near] == ["Christie", "Bathurst"]


def test_a_request_walks_with_the_key_and_a_field_mask(tmp_path):
    fake = Google(_fixture)
    _client(tmp_path, fake).walk([(43.66, -79.416)], [(43.6643, -79.4185)])
    request, body = fake.seen[0]
    assert str(request.url) == maps.ROUTES_URL
    assert request.headers["X-Goog-Api-Key"] == "test-key"
    assert request.headers["X-Goog-FieldMask"] == maps.MATRIX_FIELDS == (
        "originIndex,destinationIndex,duration,distanceMeters,status,condition")
    assert body["travelMode"] == "WALK" and "routingPreference" not in body
    origin = {"latLng": {"latitude": 43.66, "longitude": -79.416}}
    assert body["origins"] == [{"waypoint": {"location": origin}}]


def test_the_shortest_walk_wins_even_to_a_farther_station_and_no_route_is_skipped(tmp_path):
    fake = Google(_fixture)
    walks = maps.station_walks(_client(tmp_path, fake), HOMES, BLOOR)
    # t:a: 11.5 min to Christie (nearest), 8.3 min to Bathurst, no route to Spadina: Bathurst wins.
    assert walks["t:a"] == {"station_id": "s:bathurst", "station": "Bathurst", "minutes": 8.3, "at": DAY}
    # t:b: an error for one station, no route to another, 18.7 min to the third.
    assert walks["t:b"]["station"] == "Spadina" and walks["t:b"]["minutes"] == 18.7
    assert len(fake.seen) == 1   # both share the same three stations: one request, 6 elements


def test_a_listing_with_no_walking_route_keeps_its_estimate(tmp_path):
    fake = Google(lambda body: [{"originIndex": 0, "destinationIndex": j, "status": {},
                                 "condition": "ROUTE_NOT_FOUND"} for j in range(3)])
    assert maps.station_walks(_client(tmp_path, fake), HOMES[:1], BLOOR) == {}


def test_a_rerun_comes_from_the_cache_and_costs_nothing(tmp_path):
    first = Google(_fixture)
    client = _client(tmp_path, first)
    walks = maps.station_walks(client, HOMES, BLOOR)
    client.close()
    assert (client.requests, client.elements) == (1, 6)
    again = Google(lambda body: pytest.fail("asked Google for a cached walk"))
    rerun = _client(tmp_path, again)
    assert maps.station_walks(rerun, HOMES, BLOOR) == walks
    assert (rerun.requests, rerun.elements, again.seen) == (0, 0, [])


def test_listings_that_share_stations_share_a_request_and_no_element_is_wasted(tmp_path):
    far = {"listing_id": "t:north", "lat": 43.7800, "lng": -79.4150}   # its nearest three are elsewhere
    stations = [*BLOOR, {"id": "s:finch", "name": "Finch", "lat": 43.7806, "lng": -79.4148},
                {"id": "s:sheppard", "name": "Sheppard-Yonge", "lat": 43.7615, "lng": -79.4111},
                {"id": "s:york", "name": "York Mills", "lat": 43.7440, "lng": -79.4065}]
    fake = Google(_by_distance)
    client = _client(tmp_path, fake)
    walks = maps.station_walks(client, [*HOMES, far], stations)
    assert (client.requests, client.elements) == (2, 9)   # 3 listings × their 3 stations, nothing else
    assert walks["t:north"]["station"] == "Finch"


def test_requests_stay_within_625_elements_and_3000_a_minute(tmp_path):
    now, slept = [0.0], []

    def sleep(s: float) -> None:
        slept.append(s)
        now[0] += s

    homes = [{"listing_id": f"t:{i}", "lat": 43.6600 + i * 1e-5, "lng": -79.4160} for i in range(1100)]
    fake = Google(_by_distance)
    client = _client(tmp_path, fake, clock=lambda: now[0], sleep=sleep)
    walks = maps.station_walks(client, homes, BLOOR)
    sizes = [len(body["origins"]) * len(body["destinations"]) for _, body in fake.seen]
    assert len(walks) == 1100 and sum(sizes) == 3300 and max(sizes) <= maps.MAX_ELEMENTS
    assert slept == [60.0]   # 3,300 elements: the 5th request (past 3,000) waits out the minute


def test_a_refused_request_stops_the_stage_and_keeps_what_was_paid_for(tmp_path):
    calls = [0]

    def reply(body):
        calls[0] += 1
        return _by_distance(body)

    fake = Google(reply)
    client = _client(tmp_path, fake)
    client.walk([(43.66, -79.416)], [(43.6643, -79.4185)])
    fake.status = 429
    with pytest.raises(maps.MapsError, match="429"):
        client.walk([(43.70, -79.40)], [(43.6643, -79.4185)])
    assert (maps.Cache(tmp_path / "routes.json").get(maps._walk_key((43.66, -79.416), (43.6643, -79.4185)))
            or {}).get("route") is True


def test_load_sets_the_station_the_walk_and_their_source(conn, tmp_path):  # noqa: F811
    geo.load(conn, HOODS, STATIONS)   # today's straight-line estimate first, as the refresh does
    client = _client(tmp_path, Google(_by_distance))
    counts = maps.run(conn, client, STATIONS, load=True)
    # 2 route matrices for the stations, then D-10b's two searchNearby per listing (nothing close here).
    assert counts == {"listings": 3, "walks": 3, "updated": 3, "requests": 8, "elements": 6,
                      "nearby": {"listings": 3, "grocery": 0, "pharmacy": 0, "neither": 3}}
    assert maps.report(counts).splitlines()[1].startswith("nearby: 3 listings")
    row = conn.execute("select s.name, l.transit_walk_min, l.provenance from listings l join stations s "
                       "on s.id = l.transit_station_id where l.listing_id = 't:next'").fetchone()
    station, minutes, provenance = row
    assert station == "Exhibition" and minutes == pytest.approx(805 * 1.2 / 60, abs=0.3)
    for field in ("transit_walk_min", "transit_station"):
        assert provenance[field] == {"source": "derived", "via": "Google Maps walking time", "at": DAY,
                                     "version": maps.VERSION}
    unplaced = conn.execute("select transit_walk_min from listings where listing_id = 't:nowhere'").fetchone()
    assert unplaced == (None,)
    # geo puts the estimate back, which is why the refresh runs maps right after it (free from the cache).
    geo.load(conn, HOODS, STATIONS)
    assert conn.execute("select provenance->'transit_walk_min'->>'version' from listings "
                        "where listing_id = 't:next'").fetchone() == (geo.VERSION,)
    rerun = _client(tmp_path, Google(lambda body: pytest.fail("not cached")))
    assert maps.run(conn, rerun, STATIONS, load=True)["requests"] == 0


def test_the_stores_step_runs_on_the_same_client_and_cache(conn, tmp_path):  # noqa: F811
    geo.load(conn, HOODS, STATIONS)

    def places(body: dict) -> dict:   # D-10b's recorded searchNearby answers
        name = "places_grocery" if "grocery_store" in body["includedTypes"] else "places_pharmacy"
        return json.loads((FIXTURES / f"{name}.json").read_text())

    client = _client(tmp_path, Google(_by_distance, places=places))
    counts = maps.run(conn, client, STATIONS, load=True)
    assert counts["nearby"] == {"listings": 3, "grocery": 3, "pharmacy": 3, "neither": 0}
    grocery, pharmacy, minutes, source = conn.execute(
        "select grocery_name, pharmacy_name, grocery_walk_min, provenance->'grocery_name' from listings "
        "where listing_id = 't:in'").fetchone()
    assert (grocery, pharmacy) == ("No Frills", "Shoppers Drug Mart") and minutes > 0
    assert source == {"source": "derived", "via": "Google Maps", "at": DAY, "version": maps.VERSION}
    cached = Google(lambda body: pytest.fail("walk not cached"),
                    places=lambda body: pytest.fail("store not cached"))
    assert maps.run(conn, _client(tmp_path, cached), STATIONS, load=True)["requests"] == 0
