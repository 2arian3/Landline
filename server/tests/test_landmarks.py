"""Toronto's big workplaces and campuses, and chains (places.py). "Near Cohere" or "UofT" lands on the
building in pipeline/data/toronto_landmarks.json with no network; "TD Bank" alone asks which branch; and a
geocoder answer far out of downtown for a building's name is held back as a question. The network is a fake
(httpx.MockTransport) answering as OpenStreetMap did; nothing leaves the machine."""
import asyncio
import itertools
import json
import re
from datetime import UTC, datetime
from urllib.parse import parse_qs

import httpx
import pytest

from landline.agents.concierge import tools
from landline.contracts import Listing, SessionStarted
from landline.db.catalogue import Catalogue
from landline.orchestrator import SessionRuntime, places
from landline.orchestrator.places import LANDMARKS, Places, Point, Unplaced, _key
from landline.scoring.features import FeatureMatrix, Hood, haversine_km

NOW = datetime(2026, 10, 28, 4, 50, tzinfo=UTC)   # Wednesday 00:50 in Toronto
SID = "s_landmark01"
COHERE = (43.650743, -79.39107)          # 171 John Street
ST_GEORGE = (43.660723, -79.39592)       # Simcoe Hall, 27 King's College Circle
KING_AND_BAY = (43.6487, -79.3817)
BLOOR_AND_BATHURST = (43.6652, -79.4112)
CHRISTIE_PITS = (43.6645, -79.4207)
SCARBOROUGH_TOWN_CENTRE = (43.7757, -79.2578)
HOODS = [Hood(1, "Annex", 43.670, -79.405, ())]
STATIONS = [{"id": "s1", "name": "Union", "lat": 43.6453, "lng": -79.3806}]
# Nominatim's answer for "TD Centre, Toronto", bounded to Toronto, on 2026-10-29: the wrong TD Centre.
TD_COMPUTER_CENTRE = {
    "lat": "43.7803680", "lon": "-79.2451988", "name": "TD Computer Centre", "type": "yes",
    "display_name": "TD Computer Centre, 740, Progress Avenue, Woburn North, Scarborough—Guildwood, "
                    "Scarborough, Toronto, Ontario, M1H 1H9, Canada",
    "address": {"building": "TD Computer Centre", "house_number": "740", "road": "Progress Avenue",
                "city_block": "Woburn North", "quarter": "Scarborough—Guildwood", "suburb": "Scarborough",
                "city": "Toronto", "state": "Ontario", "postcode": "M1H 1H9", "country": "Canada"}}
SAID = "TD Computer Centre on Progress Avenue in Scarborough"
CALLS = itertools.count(1)   # each tool call its own id, as Higgs gives them


class Net:
    """A fake Overpass (two streets meet when both are named in the query) and Nominatim (`found`, by the
    start of the query), recording every request."""

    def __init__(self, found: dict[str, dict] | None = None,
                 meets: dict[tuple[str, str], tuple] | None = None):
        self.found, self.meets, self.calls = found or {}, meets or {}, []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if "overpass" in request.url.host:
            query = parse_qs(request.content.decode())["data"][0]
            meet = [{"lat": p[0], "lon": p[1]} for (a, b), p in self.meets.items()
                    if a in query and b in query]
            return httpx.Response(200, json={"elements": meet})
        q = parse_qs(request.url.query.decode())["q"][0]
        return httpx.Response(200, json=[hit for start, hit in self.found.items() if q.startswith(start)])

    def asked(self, host: str) -> list[httpx.Request]:
        return [c for c in self.calls if host in c.url.host]


def _places(net: Net | None = None, *, landmarks: list[dict] | None = None, contact="team@example.com"):
    client = httpx.AsyncClient(transport=httpx.MockTransport(net or Net()))
    return Places(FeatureMatrix.build([], HOODS).named, STATIONS, contact=contact, client=client,
                  landmarks=landmarks)


@pytest.fixture(autouse=True)
def _no_wait(monkeypatch):
    monkeypatch.setattr(places, "MIN_GAP_S", 0)


# ── the list ─────────────────────────────────────────────────────────────────

def test_the_list_is_from_openstreetmap_and_each_name_is_one_place():
    data = json.loads(LANDMARKS.read_text())
    assert data["licence"].startswith("ODbL") and "OpenStreetMap" in data["source"]
    owners: dict[str, set[str]] = {}
    for x in data["landmarks"]:
        for name in (x["name"], *x["aliases"]):
            owners.setdefault(_key(name), set()).add(x["id"])
    assert {name: ids for name, ids in owners.items() if len(ids) > 1} == {}
    for x in data["landmarks"]:
        assert re.fullmatch(r"(node|way|relation)/\d+", x["osm"]) and x["address"], x["name"]
        assert haversine_km(x["lat"], x["lng"], *places.UNION) < 40, x["name"]   # UTM is 30 km out
    ids = {x["id"] for x in data["landmarks"]}
    assert {"cohere", "td-centre", "royal-bank-plaza", "scotia-plaza", "cibc-square",
            "first-canadian-place", "university-of-toronto-st-george-campus",
            "university-of-toronto-scarborough", "university-of-toronto-mississauga",
            "toronto-metropolitan-university", "ocad-university", "george-brown-college-st-james-campus",
            "george-brown-college-casa-loma-campus", "humber-college-north-campus",
            "york-university-keele-campus", "sickkids", "toronto-general-hospital", "mount-sinai-hospital",
            "mars-discovery-district", "queens-park", "toronto-city-hall", "shopify-toronto",
            "google-toronto", "amazon-toronto", "microsoft-toronto"} <= ids
    cohere = next(x for x in data["landmarks"] if x["id"] == "cohere")
    assert cohere["address"].startswith("171 John Street") and "cohere.com" in cohere["office_source"]


@pytest.mark.parametrize(("said", "where"), [
    ("Cohere", COHERE), ("Cohere's office", COHERE), ("my work at Cohere", COHERE),
    ("cohere toronto", COHERE), ("UofT", ST_GEORGE), ("U of T", ST_GEORGE),
    ("University of Toronto", ST_GEORGE),
    ("u of t st. george campus", ST_GEORGE), ("TMU", (43.657869, -79.380534)),
    ("Ryerson", (43.657869, -79.380534)), ("U of T Scarborough", (43.786621, -79.189108)),
])
async def test_a_workplace_or_campus_comes_from_the_list_with_no_network(said, where):
    net = Net()
    point = await _places(net).resolve(said)
    assert (point.lat, point.lng, point.how) == (*where, "landmark")
    assert net.calls == []


async def test_td_centre_is_at_king_and_bay_not_in_scarborough():
    net = Net(found={"TD Centre": TD_COMPUTER_CENTRE})
    point = await _places(net).resolve("TD Centre")
    assert point.how == "landmark" and haversine_km(point.lat, point.lng, *KING_AND_BAY) < 0.3
    assert net.calls == []


# ── chains ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(("said", "brand"), [
    ("TD Bank", "TD"), ("TD", "TD"), ("a TD branch", "TD"), ("RBC", "RBC"), ("the nearest Tim Hortons",
                                                                                "Tim Hortons"),
    ("a Starbucks", "Starbucks"), ("Shoppers", "Shoppers Drug Mart"), ("Loblaws", "Loblaws"),
])
async def test_a_chain_named_alone_asks_which_branch(said, brand):
    net = Net(found={said: TD_COMPUTER_CENTRE})
    found = await _places(net).resolve(said)
    assert isinstance(found, Unplaced) and found.reason == "chain"
    assert f'"Which {brand}, the one at King and Bay?"' in found.ask
    assert net.calls == []   # not one branch, picked by the geocoder


@pytest.mark.parametrize("said", ["TD at Bloor and Bathurst", "the TD on Bloor and Bathurst",
                                  "a TD branch near Bloor & Bathurst"])
async def test_a_chain_with_where_it_is_looks_up_the_place(said):
    net = Net(meets={("Bloor", "Bathurst"): BLOOR_AND_BATHURST})
    point = await _places(net).resolve(said)
    assert (point.lat, point.lng, point.how) == (*BLOOR_AND_BATHURST, "intersection")
    [query] = [parse_qs(c.content.decode())["data"][0] for c in net.asked("overpass")]
    assert "TD" not in query


async def test_a_chain_with_a_station_or_a_landmark_needs_no_network():
    net = Net()
    assert (await _places(net).resolve("the Starbucks near Union station")).how == "station"
    assert (await _places(net).resolve("Tim Hortons at Cohere")).how == "landmark"
    assert net.calls == []


# ── a far answer for a building's name ───────────────────────────────────────

async def test_a_far_geocoder_answer_for_a_buildings_name_is_held_back():
    """TD Centre with no list: what production did before it, and what any name the list lacks still does."""
    net = Net(found={"TD Centre": TD_COMPUTER_CENTRE})
    unlisted = _places(net, landmarks=[])
    for near in ([], [KING_AND_BAY]):   # nothing else known, or the renter's other places are downtown
        held = await unlisted.resolve("TD Centre", near)
        assert isinstance(held, Unplaced) and held.reason == "far"
        assert f'"Do you mean {SAID}?"' in held.ask and f'with the anchor "{SAID}"' in held.ask
    # With a place of theirs out there too, it's likely the one they meant.
    kept = await unlisted.resolve("TD Centre", [KING_AND_BAY, SCARBOROUGH_TOWN_CENTRE])
    assert isinstance(kept, Point) and kept.how == "geocoder"
    assert len(net.asked("nominatim")) == 1   # one lookup; whether to hold it is per call
    # "Do you mean …?" "Yes": the words it asked with place it, or its name alone, with no lookup.
    assert unlisted.known(SAID) == unlisted.known("the TD Computer Centre") == kept


async def test_an_address_or_a_place_near_downtown_is_not_held():
    far = {"lat": "43.780368", "lon": "-79.2451988", "address": {"road": "Progress Avenue"}}
    near = {"lat": str(CHRISTIE_PITS[0]), "lon": str(CHRISTIE_PITS[1])}
    net = Net(found={"740 Progress Avenue": far, "Christie Pits": near})
    p = _places(net)
    assert (await p.resolve("740 Progress Avenue")).how == "geocoder"   # a street number says which
    assert (await p.resolve("Christie Pits")).how == "geocoder"


async def test_two_callers_at_once_share_one_lookup():
    net = Net(found={"Christie Pits": {"lat": str(CHRISTIE_PITS[0]), "lon": str(CHRISTIE_PITS[1])}})
    p = _places(net)
    a, b = await asyncio.gather(p.resolve("Christie Pits"), p.resolve("christie  pits"))
    assert a == b and len(net.calls) == 1


# ── the Concierge ────────────────────────────────────────────────────────────

def _listing(i: int, lat: float, lng: float) -> Listing:
    return Listing(listing_id=f"t:{i}", source="t", source_id=str(i), url=f"https://example.com/{i}",
                   first_seen=NOW, last_seen=NOW, address=f"{i} Landmark St", lat=lat, lng=lng, rent=2400,
                   monthly_cost=2400, beds=1, sqft=650)


# The same rent and size, so where they are decides: by Cohere, midtown, and Scarborough.
LISTINGS = [_listing(1, 43.7790, -79.2470), _listing(2, 43.7000, -79.4100), _listing(3, 43.6520, -79.3930)]
MINUTES = {"t:1": 58.0, "t:2": 27.0, "t:3": 6.0}


class Google:
    """A fake Routes API: TRANSIT minutes by origin, and every request kept."""

    def __init__(self):
        self.seen: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.seen.append(body)
        by_lat = {round(x.lat, 5): x.listing_id for x in LISTINGS}
        ids = [by_lat[round(o["waypoint"]["location"]["latLng"]["latitude"], 5)] for o in body["origins"]]
        return httpx.Response(200, json=[
            {"originIndex": i, "destinationIndex": 0, "status": {}, "condition": "ROUTE_EXISTS",
             "duration": f"{int(MINUTES[lid] * 60)}s"} for i, lid in enumerate(ids)])


async def _concierge(runtime: SessionRuntime, renter_said: str) -> tools.ToolContext:
    await runtime.start(SessionStarted(session_id=SID, at=NOW, idem_key=f"{SID}:start", channel="judge"))
    return tools.ToolContext(session_id=SID, call_id="c_landmark01", runtime=runtime, renter_said=renter_said)


async def _say(ctx: tools.ToolContext, name: str, args: dict) -> str:
    return await tools.handle(name, json.dumps(args), f"fc_{next(CALLS)}", ctx)


async def test_near_cohere_re_sorts_the_board_around_171_john_with_the_ttc_commute():
    runtime = SessionRuntime(Catalogue(LISTINGS, "test"), maps_key="test-key")   # no contact: no lookups
    google = Google()
    runtime.commute.http = httpx.AsyncClient(transport=httpx.MockTransport(google))
    runtime.commute.now = lambda: NOW
    ctx = await _concierge(runtime, "I'm looking for a place near Cohere, where I work")
    said = await _say(ctx, "update_preferences", {"anchors": [{"label": "Cohere"}]})
    assert "Couldn't find" not in said and "Looking up" not in said
    view = runtime.get(SID)
    [anchor] = view.preferences.anchors
    assert (anchor.label, anchor.lat, anchor.lng) == ("Cohere", *COHERE)   # in the same commit as the reply
    assert [c.listing_id for c in view.board] == ["t:3", "t:2", "t:1"]   # nearest 171 John first
    await runtime.idle()   # D-10c: TTC minutes to Cohere, then CommuteResolved re-ranks
    [body] = google.seen
    assert body["destinations"][0]["waypoint"]["location"]["latLng"] == {"latitude": COHERE[0],
                                                                         "longitude": COHERE[1]}
    top = runtime.get(SID).board[0]
    assert top.listing_id == "t:3" and "6 min by TTC to Cohere" in top.reasons
    await runtime.close()


async def test_td_bank_alone_is_left_out_and_the_reply_asks_which():
    runtime = SessionRuntime(Catalogue(LISTINGS, "test"))
    ctx = await _concierge(runtime, "near Cohere")
    await _say(ctx, "update_preferences", {"anchors": [{"label": "Cohere"}]})
    ctx.renter_said = "actually, near TD Bank"
    said = await _say(ctx, "update_preferences", {"anchors": [{"label": "TD Bank"}]})
    assert "TD Bank has many branches, so it isn't on the map. Ask which TD, by its street or " \
           'intersection ("Which TD, the one at King and Bay?").' in said
    # Nothing replaced Cohere: the search keeps it until they say which TD.
    assert [(a.label, a.lat) for a in runtime.get(SID).preferences.anchors] == [("Cohere", COHERE[0])]
    before = runtime.get(SID).version
    said = await _say(ctx, "focus_area", {"place": "a Tim Hortons"})
    assert said.endswith("Nothing changed.") and "Which Tim Hortons" in said
    assert runtime.get(SID).version == before and runtime.get(SID).focus_area is None
    await runtime.close()


async def test_a_place_not_placed_after_the_reply_comes_as_a_note():
    net = Net(found={"TD Centre": TD_COMPUTER_CENTRE,
                     "Christie Pits": {"lat": str(CHRISTIE_PITS[0]), "lon": str(CHRISTIE_PITS[1])}})
    runtime = SessionRuntime(Catalogue(LISTINGS, "test"), places=_places(net, landmarks=[]))
    ctx = await _concierge(runtime, "near TD Centre")
    notes: list[str] = []

    async def note(text: str) -> None:
        notes.append(text)

    ctx.note = note

    async def named(place: str) -> str:
        ctx.renter_said = f"near {place}"
        said = await _say(ctx, "update_preferences", {"anchors": [{"label": place}]})
        await asyncio.gather(*ctx.lookups)
        await runtime.idle()
        return said

    said = await named("TD Centre")
    assert said.endswith("Looking up TD Centre on the map; the list moves if it's found.")
    [held] = notes
    assert held.startswith(f'{tools.PLACE_LOST} TD Centre isn\'t on the map. Ask "Do you mean {SAID}?" '
                           "(19 km from downtown).")
    assert [(a.label, a.lat) for a in runtime.get(SID).preferences.anchors] == [("TD Centre", None)]
    await named("Narnia")
    assert notes[-1] == f"{tools.PLACE_LOST} Couldn't find Narnia on the map, so the list doesn't use it. " \
                        f"{tools.ASK_PLACE}"
    notes.clear()
    await named("Christie Pits")   # found: the board moves, and the Line says so; no note
    assert notes == [] and runtime.get(SID).preferences.anchors[0].lat == CHRISTIE_PITS[0]
    # "Yes, that one": the words the note asked with place it at once.
    ctx.renter_said = "yes, that one"
    await _say(ctx, "update_preferences", {"anchors": [{"label": SAID}]})
    [anchor] = runtime.get(SID).preferences.anchors
    assert (anchor.label, anchor.lat, anchor.lng) == (SAID, 43.780368, -79.2451988)
    await runtime.close()
