"""Places (D-04): a named place becomes a point, cheapest way first, and the board re-ranks around it. The
network steps run against a fake transport; nothing leaves the machine."""
import json
import re
from datetime import UTC, datetime
from urllib.parse import parse_qs

import httpx
import pytest

from landline.contracts import Listing, PreferenceDelta, PreferencesUpdated, SessionStarted
from landline.contracts.tools import AnchorRef
from landline.db.catalogue import Catalogue
from landline.orchestrator import SessionRuntime
from landline.orchestrator.places import Places, _street
from landline.scoring import rank
from landline.scoring.features import FeatureMatrix, Hood

NOW = datetime(2026, 10, 24, tzinfo=UTC)
HOODS = [Hood(1, "Annex", 43.670, -79.405, ()), Hood(2, "Yonge-Bay Corridor", 43.650, -79.381, ())]
STATIONS = [{"id": "s1", "name": "Union", "lat": 43.6453, "lng": -79.3806},
            {"id": "s2", "name": "St. George", "lat": 43.6681, "lng": -79.3997}]
KING_AND_BAY = (43.6486561, -79.3802633)


class Net:
    """A fake Overpass and Nominatim that records what was asked."""

    def __init__(self, overpass=None, nominatim=None, status=200):
        self.overpass, self.nominatim, self.status, self.calls = overpass, nominatim, status, []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if self.status != 200:
            return httpx.Response(self.status)
        if "overpass" in request.url.host:
            return httpx.Response(200, json={"elements": self.overpass or []})
        return httpx.Response(200, json=self.nominatim or [])


def _places(net: Net | None = None, contact: str | None = "team@example.com") -> Places:
    client = httpx.AsyncClient(transport=httpx.MockTransport(net or Net()))
    return Places(FeatureMatrix.build([], HOODS).named, STATIONS, contact=contact, client=client)


async def test_a_neighbourhood_or_a_station_needs_no_network():
    net = Net()
    places = _places(net)
    annex = await places.resolve("the Annex")
    assert (annex.lat, annex.lng, annex.how) == (43.670, -79.405, "neighbourhood")
    union = await places.resolve("Union station")
    assert (union.lat, union.how) == (43.6453, "station")
    assert (await places.resolve("St George")).how == "station"
    assert net.calls == []


async def test_an_apostrophe_or_its_absence_finds_the_same_station():
    queens_park = {"id": "s3", "name": "Queen's Park", "lat": 43.6597, "lng": -79.3902}
    places = Places(FeatureMatrix.build([], HOODS).named, [*STATIONS, queens_park], contact=None)
    for said in ("Queens Park", "Queen's Park", "Queen’s Park", "Queens Park station"):
        point = await places.resolve(said)
        assert point is not None and (point.lat, point.how) == (43.6597, "station"), said


def test_an_apostrophe_or_its_absence_finds_the_same_neighbourhood():
    named = FeatureMatrix.build([], [Hood(3, "Parkwoods-O'Connor Hills", 43.75, -79.31, ())]).named
    # "o connor" is how refs.area passes "O'Connor" on, and "O Connor" how a transcript may write it.
    for said in ("OConnor", "O'Connor", "O’Connor Hills", "O Connor", "o connor"):
        assert [h.name for h in named([said])] == ["Parkwoods-O'Connor Hills"], said


async def test_an_intersection_is_where_the_two_streets_meet():
    net = Net(overpass=[{"lat": KING_AND_BAY[0], "lon": KING_AND_BAY[1]}])
    places = _places(net)
    point = await places.resolve("King and Bay")
    assert (point.lat, point.lng, point.how) == (*KING_AND_BAY, "intersection")
    query = parse_qs(net.calls[0].content.decode())["data"][0]
    assert '"name"~"^King( ' in query and '"name"~"^Bay( ' in query and "node(w.a)(w.b)" in query
    assert "team@example.com" in net.calls[0].headers["user-agent"]
    assert await places.resolve("king  AND bay") == point and len(net.calls) == 1   # remembered


async def test_anything_else_goes_to_the_geocoder_bounded_to_toronto():
    net = Net(nominatim=[{"lat": "43.6645", "lon": "-79.4207"}])
    point = await _places(net).resolve("Christie Pits")
    assert (point.lat, point.lng, point.how) == (43.6645, -79.4207, "geocoder")
    params = dict(net.calls[0].url.params)
    assert params["q"] == "Christie Pits, Toronto" and params["bounded"] == "1"


async def test_an_intersection_with_no_meeting_point_falls_back_to_the_geocoder():
    net = Net(overpass=[], nominatim=[{"lat": "43.7", "lon": "-79.4"}])
    assert (await _places(net).resolve("Yonge & Nowhere")).how == "geocoder"
    assert [c.url.host for c in net.calls] == ["overpass-api.de", "nominatim.openstreetmap.org"]


async def test_without_a_contact_or_when_lookups_fail_the_place_stays_unresolved():
    offline = Net()
    assert await _places(offline, contact=None).resolve("King and Bay") is None and offline.calls == []
    assert await _places(Net(status=503)).resolve("Casa Loma") is None
    assert await _places(Net()).resolve("Nowhere at all") is None


async def test_a_lookup_that_times_out_is_one_line_that_names_the_timeout(caplog):
    """#184: a timeout's message is empty, so the log read "lookup failed ()"."""
    def slow(request):
        raise httpx.ReadTimeout("", request=request)
    client = httpx.AsyncClient(transport=httpx.MockTransport(slow))
    places = Places(FeatureMatrix.build([], HOODS).named, STATIONS, contact="team@example.com", client=client)
    assert await places.resolve("Christy Street") is None
    [line] = [r for r in caplog.records if "lookup failed" in r.getMessage()]
    assert "ReadTimeout(" in line.getMessage() and line.exc_info is None   # no traceback


@pytest.mark.parametrize(("said", "matches", "misses"), [
    ("King", ["King Street West", "King Street East", "King Street"],
     ["Kingston Road", "King Edward Avenue"]),
    ("Bay Street", ["Bay Street"], ["Baycrest Avenue"]),
    ("Queen St W", ["Queen Street West"], ["Queens Quay West"]),
    ("Queen's Quay", ["Queens Quay West", "Queen's Quay East"], ["Queen Street West"]),
    ("Queens Park Crescent", ["Queen's Park Crescent East", "Queen's Park"], ["Queens Quay West"]),
    ("O’Connor Drive", ["O'Connor Drive", "OConnor Drive"], ["Connor Street"]),
])
def test_a_street_as_people_say_it_matches_its_full_name(said, matches, misses):
    pattern = re.compile(_street(said), re.I)
    assert all(pattern.search(m) for m in matches) and not any(pattern.search(m) for m in misses)


async def test_naming_a_place_re_ranks_the_board_around_it():
    near, far = KING_AND_BAY, (43.78, -79.42)
    listings = [Listing(listing_id=f"t:{n}", source="t", source_id=str(n), url="https://example.com",
                        first_seen=NOW, last_seen=NOW, address=f"{n} Place St", lat=lat, lng=lng, rent=rent,
                        monthly_cost=rent, beds=1)
                for n, (lat, lng), rent in ((1, far, 1800), (2, near, 2100))]
    places = _places(Net(overpass=[{"lat": KING_AND_BAY[0], "lon": KING_AND_BAY[1]}]))
    rt = SessionRuntime(Catalogue(listings, "test"), places=places)
    sid = "s_place00001"
    await rt.start(SessionStarted(session_id=sid, at=NOW, idem_key=f"{sid}:start", channel="judge"))
    before = await rt.emit(PreferencesUpdated(session_id=sid, at=NOW, idem_key=f"{sid}:p1",
                                              delta=PreferenceDelta(max_monthly=2400)))
    assert [c.listing_id for c in before.board] == ["t:1", "t:2"]   # cheaper first, no place yet
    await rt.emit(PreferencesUpdated(session_id=sid, at=NOW, idem_key=f"{sid}:p2",
                                     delta=PreferenceDelta(anchors=[AnchorRef(label="King and Bay")])))
    await rt.idle()   # the lookup runs after the commit, then AnchorResolved
    after = rt.get(sid)
    [anchor] = after.preferences.anchors
    assert (anchor.lat, anchor.lng) == KING_AND_BAY
    assert [c.listing_id for c in after.board] == ["t:2", "t:1"]
    assert after.agent_says == "2 Place St moves to the top. 1 Place St drops to second."
    assert "right by King and Bay" in after.board[0].reasons


# A street is a line, not a point (#102)

QUEEN_W = ((43.6480, -79.4200), (43.6480, -79.4050), (43.6480, -79.3900))
QUEEN_E = ((43.6560, -79.3500), (43.6560, -79.3200))


def _ways(*paths) -> list[dict]:
    return [{"type": "way", "geometry": [{"lat": lat, "lon": lng} for lat, lng in path]} for path in paths]


def _at(n: int, lat: float, lng: float) -> Listing:
    return Listing(listing_id=f"t:{n}", source="t", source_id=str(n), url="https://example.com",
                   first_seen=NOW, last_seen=NOW, address=f"{n} Place St", lat=lat, lng=lng, rent=2000,
                   monthly_cost=2000, beds=1)


def _name_pattern(request: httpx.Request) -> re.Pattern:
    query = parse_qs(request.content.decode())["data"][0]
    assert "(?:" not in query   # Overpass's regular expressions are POSIX, and reject (?:…) outright
    return re.compile(re.search(r'"name"~"([^"]+)"', query)[1], re.I)


def test_street_patterns_have_no_groups_overpass_rejects():
    assert "(?:" not in _street("King Street West")


async def test_a_street_is_its_whole_line_and_a_direction_only_that_part():
    net = Net(overpass=_ways(QUEEN_W, QUEEN_E))
    places = _places(net)
    queen = await places.resolve("Queen Street")
    assert (queen.how, queen.line) == ("street", (QUEEN_W, QUEEN_E))
    assert (queen.lat, queen.lng) in (*QUEEN_W, *QUEEN_E)   # a point on the street, for the map
    assert "out geom" in parse_qs(net.calls[0].content.decode())["data"][0]
    both = _name_pattern(net.calls[0])
    assert all(both.search(n) for n in ("Queen Street West", "Queen Street East", "Queen Street"))
    assert not any(both.search(n) for n in ("Queens Quay West", "Queen Elizabeth Way"))
    await places.resolve("Queen St W")
    west = _name_pattern(net.calls[1])
    assert west.search("Queen Street West") and not west.search("Queen Street East")


async def test_a_whole_street_matches_with_or_without_its_apostrophe():
    net = Net(overpass=_ways(QUEEN_W))
    assert (await _places(net).resolve("Queens Park Crescent")).how == "street"
    crescent = _name_pattern(net.calls[0])
    assert crescent.search("Queen's Park Crescent East") and crescent.search("Queens Park Crescent West")
    assert not crescent.search("Queen Street West")


def test_a_line_measures_to_its_nearest_point():
    matrix = FeatureMatrix.build([_at(1, 43.6480 - 0.0018, -79.4120),   # 200 m off the middle of a segment
                                  _at(2, 43.6480, -79.4324)])           # 1 km beyond the street's west end
    near, beyond = matrix.line_distances_km((QUEEN_W, QUEEN_E))
    assert near == pytest.approx(0.199, abs=0.002) and beyond == pytest.approx(1.0, abs=0.01)


async def test_close_to_queen_street_measures_from_the_whole_street():
    """#102's done-when: 200 m off Queen St W and 200 m off Queen St E score the same; 3 km north, lower."""
    listings = [_at(1, 43.6480 - 0.0018, -79.4050),   # 200 m south of Queen St W
                _at(2, 43.6560 + 0.0018, -79.3350),   # 200 m north of Queen St E
                _at(3, 43.6480 + 0.0271, -79.4050)]   # 3 km north of Queen St W
    rt = SessionRuntime(Catalogue(listings, "test"), places=_places(Net(overpass=_ways(QUEEN_W, QUEEN_E))))
    sid = "s_queenst001"
    await rt.start(SessionStarted(session_id=sid, at=NOW, idem_key=f"{sid}:start", channel="judge"))
    await rt.emit(PreferencesUpdated(session_id=sid, at=NOW, idem_key=f"{sid}:p1", delta=PreferenceDelta(
        max_monthly=2400, anchors=[AnchorRef(label="Queen Street")])))
    await rt.idle()   # the lookup and the line's distances run after the commit, then AnchorResolved
    view = rt.get(sid)
    [anchor] = view.preferences.anchors
    assert (anchor.lat, anchor.lng) in (*QUEEN_W, *QUEEN_E) and rt.matrix.has_line(anchor.lat, anchor.lng)
    ranking = rank(rt.matrix, view.preferences, keep_all=True)
    location = {f"t:{n}": round(ranking.explain(f"t:{n}").contributions["location"], 6) for n in (1, 2, 3)}
    assert location["t:1"] == location["t:2"] > location["t:3"]
    assert [c.listing_id for c in view.board] == ["t:1", "t:2", "t:3"]


def test_the_station_file_is_read_by_default():
    places = Places(FeatureMatrix.build([], HOODS).named)
    assert places.contact is None and json.dumps(sorted(places._stations)).count("union") == 1
