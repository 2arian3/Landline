"""ask_maps (O-07b): area questions answered from Google's Maps MCP server, under option B (Google's place is
never put on our map), or from the walks stored on the listing (D-10a, D-10b). A fake client returns O-07a's
own answer types and counts what it's asked; nothing leaves the machine."""
import asyncio
import json
from datetime import UTC, date, datetime

import pytest

from landline.agents.concierge import tools
from landline.contracts import Listing, ListingFocused, SessionStarted
from landline.db.catalogue import Catalogue
from landline.orchestrator import SessionRuntime
from landline.orchestrator.maps_mcp import DISCLOSURE, WALK_WARNING, Nearby, Place, Route, Weather

T0 = datetime(2026, 10, 28, 15, 0, tzinfo=UTC)
SID = "s_askmaps001"
MAPS_URL = "https://maps.google.com/?cid=123"
PLACE_URL = "https://maps.google.com/?cid=456"
FAR = (43.6505, -79.3910)    # about 2.3 km from listing 1 in a straight line


def _listing(i: int, lat: float, lng: float) -> Listing:
    return Listing(listing_id=f"t:{i}", source="t", source_id=str(i), url=f"https://example.com/{i}",
                   first_seen=T0, last_seen=T0, address=f"{i} Bloor St W", lat=lat, lng=lng, rent=2000 + i,
                   monthly_cost=2000 + i, beds=1, sqft=600)


CATALOGUE = Catalogue([_listing(1, 43.6650, -79.4110), _listing(2, 43.6700, -79.3900),
                       _listing(3, 43.6550, -79.4000)], "test")


class FakeMaps:
    """MapsMCP's nearby / place / route / weather, answering after `delay` seconds. `routes` is (minutes,
    metres) by travel mode; `place_at` None finds no place."""

    def __init__(self, name: str | None = "Shoppers Drug Mart", minutes: int | None = 6, delay: float = 0,
                 within: int = 3, place_name: str | None = "Cohere", place_at: tuple | None = FAR,
                 routes: dict | None = None):
        self.name, self.minutes, self.delay, self.asked = name, minutes, delay, []
        self.within, self.place_name, self.place_at = within, place_name, place_at
        self.routes = {"WALK": (24, 5200), "TRANSIT": (24, 5200), "DRIVE": (15, 6100)} if routes is None \
            else routes

    def _source(self) -> dict:
        return {"attribution": "Google Maps", "attribution_url": "https://maps.google.com", "fetched_at": T0}

    async def nearby(self, lat, lng, what):
        self.asked.append(("nearby", lat, lng, what))
        await asyncio.sleep(self.delay)
        return Nearby(**self._source(), name=self.name, place_id="p1", lat=43.66, lng=-79.41,
                      maps_url=MAPS_URL, summary="…", minutes=self.minutes,
                      distance_m=480 if self.minutes else None,
                      warning=WALK_WARNING if self.minutes is not None else None, within_1km=self.within)

    async def place(self, words):
        self.asked.append(("place", words))
        await asyncio.sleep(self.delay)
        if self.place_at is None:
            return None
        return Place(**self._source(), name=self.place_name, place_id="p2", lat=self.place_at[0],
                     lng=self.place_at[1], maps_url=PLACE_URL, summary="…")

    async def route(self, origin, destination, mode="WALK"):
        self.asked.append(("route", origin, destination, mode))
        await asyncio.sleep(self.delay)
        if mode not in self.routes:
            return None
        minutes, metres = self.routes[mode]
        return Route(**self._source(), mode=mode, minutes=minutes, distance_m=metres,
                     km=round(metres / 1000, 1), warning=WALK_WARNING if mode == "WALK" else None)

    async def weather(self, lat, lng, day):
        self.asked.append(("weather", lat, lng, day))
        return Weather(**self._source(), day=day,
                       summary="Light rain, high 12°C, low 6°C, 60% chance of rain.",
                       condition="Light rain", high_c=12, low_c=6, precip_pct=60)

    async def close(self):
        pass


@pytest.fixture
async def ctx():
    runtime = SessionRuntime(CATALOGUE)
    runtime.maps = FakeMaps()
    await runtime.start(SessionStarted(session_id=SID, at=T0, idem_key=f"{SID}:started", channel="phone"))
    texts: list[str] = []
    yield tools.ToolContext(session_id=SID, call_id="c_askmaps001", runtime=runtime, text=texts.append)
    await runtime.close()


async def _ask(ctx, args: str, tool_call_id: str = "fc_1") -> str:
    return await tools.handle("ask_maps", args, tool_call_id, ctx)


def _texts(ctx) -> list[str]:
    return ctx.text.__self__


async def _focus(ctx, n: int) -> None:
    await ctx.runtime.emit(ListingFocused(session_id=SID, at=T0, idem_key=f"{SID}:f{n}", listing_id=f"t:{n}",
                                          origin="ui"))


async def test_without_a_maps_client_the_tool_isnt_offered_or_run():
    runtime = SessionRuntime(CATALOGUE)
    assert runtime.maps is None
    assert "ask_maps" not in [t["name"] for t in tools.offered(runtime)]
    ctx = tools.ToolContext(session_id=SID, call_id="c_x", runtime=runtime)
    assert await _ask(ctx, '{"kind": "nearby", "what": "a pharmacy"}') == tools.UNKNOWN
    runtime.maps = FakeMaps()
    assert tools.offered(runtime)[-1]["name"] == "ask_maps"


async def test_the_closest_place_with_the_disclosure_once_the_walk_warning_and_the_texted_link(ctx):
    await _focus(ctx, 1)
    reply = await _ask(ctx, '{"kind": "nearby", "what": "pharmacy"}')
    assert reply == (f"{DISCLOSURE}. The closest pharmacy to 1 Bloor St W is Shoppers Drug Mart, about 6 "
                     "minutes' walk. Google Maps shows 3 within 1 km. Google says its walking routes are in "
                     "beta. I've texted you the Google Maps link.")
    texted = _texts(ctx)[0]
    assert texted.startswith("Landline: The closest pharmacy to 1 Bloor St W is Shoppers Drug Mart")
    assert f"Source: Google Maps {MAPS_URL}" in texted and WALK_WARNING in texted and DISCLOSURE in texted
    again = await _ask(ctx, '{"kind": "nearby", "what": "gym"}', "fc_2")
    assert DISCLOSURE not in again   # said once a call


async def test_a_place_without_a_clear_name_is_never_given_one(ctx):
    ctx.runtime.maps = FakeMaps(name=None)
    await _focus(ctx, 1)
    reply = await _ask(ctx, '{"kind": "nearby", "what": "pharmacy"}')
    assert "There's a pharmacy about 6 minutes' walk from 1 Bloor St W." in reply
    assert "Shoppers" not in reply


async def test_the_listing_comes_from_the_ref_or_the_focus_never_from_the_model(ctx):
    maps = ctx.runtime.maps
    assert await _ask(ctx, '{"kind": "nearby", "what": "pharmacy"}') == \
        "Which listing? Ask the renter for its number or street."   # nothing focused, nothing named
    board = ctx.runtime.get(SID).board
    second = CATALOGUE.by_id[board[1].listing_id]
    await _ask(ctx, '{"kind": "nearby", "what": "pharmacy", "listing_ref": "2"}', "fc_2")
    assert maps.asked[-1] == ("nearby", second.lat, second.lng, "pharmacy")
    await _focus(ctx, 3)
    await _ask(ctx, '{"kind": "nearby", "what": "pharmacy"}', "fc_3")
    assert maps.asked[-1][1:3] == (43.6550, -79.4000)


async def test_a_walk_a_drive_and_the_weather_in_words(ctx):
    await _focus(ctx, 1)
    ctx.runtime.maps.place_name = "Christie Pits"
    walk = await _ask(ctx, '{"kind": "walk", "to": "Christie Pits"}')
    assert "1 Bloor St W is about 24 minutes' walk from Christie Pits (5.2 km)." in walk and "in beta" in walk
    drive = await _ask(ctx, '{"kind": "drive", "to": "Christie Pits"}', "fc_2")
    assert drive.startswith("1 Bloor St W is about 15 minutes by car (6.1 km) from Christie Pits; "
                            "that's not walking distance.")
    assert "in beta" not in drive
    assert [a[3] for a in ctx.runtime.maps.asked if a[0] == "route"] == ["WALK", "DRIVE"]
    rain = await _ask(ctx, '{"kind": "weather", "day": "Saturday"}', "fc_3")
    assert rain.startswith("Saturday near 1 Bloor St W: Light rain, high 12°C, low 6°C, 60% chance of rain.")
    assert ctx.runtime.maps.asked[-1][3].weekday() == 5
    assert await _ask(ctx, '{"kind": "walk"}', "fc_4") == "Ask the renter where to."


def test_a_forecast_day_in_words():
    wednesday = date(2026, 10, 28)
    assert tools._day("Saturday", wednesday) == date(2026, 10, 31)
    assert tools._day("tomorrow", wednesday) == date(2026, 10, 29)
    assert tools._day(None, wednesday) == wednesday


async def test_in_the_browser_nothing_is_texted_but_the_disclosure_is_still_said(ctx):
    ctx.text = None
    await _focus(ctx, 1)
    reply = await _ask(ctx, '{"kind": "nearby", "what": "pharmacy"}')
    assert reply.startswith(DISCLOSURE) and "texted" not in reply


async def test_a_slow_answer_comes_between_turns(ctx, monkeypatch):
    monkeypatch.setattr(tools, "MAPS_WAIT_S", 0.01)
    ctx.runtime.maps = FakeMaps(delay=0.05)
    notes: list[str] = []

    async def note(said: str) -> None:
        notes.append(said)

    ctx.note = note
    await _focus(ctx, 1)
    assert await _ask(ctx, '{"kind": "nearby", "what": "pharmacy"}') == tools.CHECKING_MAP
    await ctx.runtime.idle()
    assert len(notes) == 1 and notes[0].startswith(f"{tools.MAP_ANSWER} {DISCLOSURE}. The closest pharmacy")


async def test_a_late_answer_after_hang_up_or_a_newer_question_is_dropped(ctx, monkeypatch):
    monkeypatch.setattr(tools, "MAPS_WAIT_S", 0.01)
    ctx.runtime.maps = FakeMaps(delay=0.05)
    notes: list[str] = []

    async def note(said: str) -> None:
        notes.append(said)

    ctx.note = note
    await _focus(ctx, 1)
    await _ask(ctx, '{"kind": "nearby", "what": "pharmacy"}')
    await _ask(ctx, '{"kind": "nearby", "what": "gym"}', "fc_2")   # a newer question
    await ctx.runtime.idle()
    assert len(notes) == 1 and "The closest gym" in notes[0]   # only the newer question's answer
    notes.clear()
    texted = len(_texts(ctx))
    await _ask(ctx, '{"kind": "nearby", "what": "pharmacy"}', "fc_3")
    ctx.hang_up = True
    await ctx.runtime.idle()
    assert notes == [] and len(_texts(ctx)) == texted   # nothing said or texted after the goodbye


# How far, how close and how many: the stored walks first (D-10a, D-10b), then Google

AT = (43.6650, -79.4110)     # listing 1
STATION = {"transit_station": "Christie", "transit_walk_min": 6.4}
STORES = {"grocery_name": "No Frills", "grocery_walk_min": 4.0, "pharmacy_name": "Shoppers Drug Mart",
          "pharmacy_walk_min": 13.6}
NEAR = (43.6629, -79.3957)       # about 1.3 km from listing 1 in a straight line
FAR_AWAY = (43.6420, -79.3800)   # about 3.2 km
CHRISTIE = "The nearest subway station to 1 Bloor St W is Christie, about 6 minutes' walk; that's very close."
GROCERY = "The closest {} to 1 Bloor St W is No Frills, about 4 minutes' walk; that's very close."
PHARMACY = "The closest {} to 1 Bloor St W is Shoppers Drug Mart, about 14 minutes' walk; that's walkable."


def _stored(ctx, monkeypatch, **fields) -> None:
    """Listing 1 with stored walks, for this test only."""
    by_id = ctx.runtime.catalogue.by_id
    monkeypatch.setitem(by_id, "t:1", by_id["t:1"].model_copy(update=fields))


def _calls(ctx) -> list[str]:
    return [a[0] if a[0] != "route" else f"route {a[3]}" for a in ctx.runtime.maps.asked]


@pytest.mark.parametrize("args", [
    {"kind": "transit", "to": "TTC station"}, {"kind": "walk", "to": "the TTC"},
    {"kind": "transit", "to": "the subway"}, {"kind": "walk", "to": "the nearest subway station"},
    {"kind": "transit", "to": "closest station"}, {"kind": "transit", "to": "public transit"},
    {"kind": "drive", "to": "TTC stations"}, {"kind": "nearby", "what": "a subway station"},
    {"kind": "transit", "to": "the nearest TTC stop"},
    {"kind": "walk", "to": "Christie Station"}])
async def test_the_ttc_comes_from_the_stored_walk_without_asking_google(ctx, monkeypatch, args):
    _stored(ctx, monkeypatch, **STATION)
    await _focus(ctx, 1)
    reply = await _ask(ctx, json.dumps(args))
    assert reply == CHRISTIE
    assert _calls(ctx) == [] and _texts(ctx) == []   # nothing from Google: no disclosure, nothing to text


async def test_another_station_is_routed_to_and_no_stored_walk_asks_google(ctx, monkeypatch):
    _stored(ctx, monkeypatch, **STATION)
    await _focus(ctx, 1)
    await _ask(ctx, '{"kind": "transit", "to": "Union station"}')
    assert _calls(ctx) == ["route TRANSIT"]   # Union, from the station list: no search
    ctx.runtime.maps.asked.clear()
    _stored(ctx, monkeypatch, transit_station=None, transit_walk_min=None)
    ctx.runtime.maps.name = "Christie Station"
    reply = await _ask(ctx, '{"kind": "transit", "to": "TTC station"}', "fc_2")
    assert ctx.runtime.maps.asked == [("nearby", *AT, "subway station")]
    assert "The closest subway station to 1 Bloor St W is Christie Station, about 6 minutes' walk." in reply


@pytest.mark.parametrize("what, said", [
    ("pharmacy", PHARMACY.format("pharmacy")), ("a drugstore", PHARMACY.format("drugstore")),
    ("grocery store", GROCERY.format("grocery store")),
    ("the nearest supermarket", GROCERY.format("supermarket"))])
async def test_a_stored_grocery_store_or_pharmacy_answers_without_asking_google(ctx, monkeypatch, what, said):
    _stored(ctx, monkeypatch, **STORES)
    await _focus(ctx, 1)
    assert await _ask(ctx, json.dumps({"kind": "nearby", "what": what})) == said
    assert _calls(ctx) == []


@pytest.mark.parametrize("what", ["Shoppers", "No Frills", "grocery stores", "pharmacies", "a gym"])
async def test_a_brand_a_count_or_another_kind_asks_google(ctx, monkeypatch, what):
    _stored(ctx, monkeypatch, **STORES)
    await _focus(ctx, 1)
    await _ask(ctx, json.dumps({"kind": "nearby", "what": what}))
    assert _calls(ctx) == ["nearby"]


async def test_no_stored_walk_asks_google_and_google_finding_nothing_falls_back(ctx, monkeypatch):
    await _focus(ctx, 1)
    await _ask(ctx, '{"kind": "nearby", "what": "grocery store"}')
    assert _calls(ctx) == ["nearby"]   # nothing stored

    async def nothing(*args):
        return None

    _stored(ctx, monkeypatch, **STORES)
    monkeypatch.setattr(ctx.runtime.maps, "nearby", nothing)
    reply = await _ask(ctx, '{"kind": "nearby", "what": "grocery stores"}', "fc_2")
    assert reply == GROCERY.format("grocery store")


@pytest.mark.parametrize("within, said", [(3, "3"), (5, "5 or more"), (1, "1"), (0, "none")])
async def test_a_nearby_answer_counts_the_matches_within_1_km(ctx, within, said):
    ctx.runtime.maps = FakeMaps(name="McDonald's", minutes=3, within=within)
    await _focus(ctx, 1)
    reply = await _ask(ctx, '{"kind": "nearby", "what": "fast food places"}')
    assert (f"The closest fast food place to 1 Bloor St W is McDonald's, about 3 minutes' walk. "
            f"Google Maps shows {said} within 1 km.") in reply


async def test_a_place_on_neither_list_is_found_on_google_then_routed_to_by_ttc(ctx):
    ctx.runtime.maps = FakeMaps(place_name="Evergreen Brick Works", place_at=FAR_AWAY,
                                routes={"TRANSIT": (26, 6000), "WALK": (70, 5600)})
    await _focus(ctx, 1)
    reply = await _ask(ctx, '{"kind": "transit", "to": "the Brick Works"}')
    assert reply == (f"{DISCLOSURE}. 1 Bloor St W is about 26 minutes by TTC (6 km) from Evergreen Brick "
                     "Works; that's not walking distance. I've texted you the Google Maps link.")
    assert _calls(ctx) == ["place", "route TRANSIT"]   # 2 calls: past 2.5 km a walk isn't asked for
    assert ctx.runtime.maps.asked[0] == ("place", "the Brick Works")
    route = ctx.runtime.maps.asked[1]
    assert route[1] == AT and route[2].place_id == "p2"   # from the listing to the place Google found
    texted = _texts(ctx)[0]
    assert f"Source: Google Maps {PLACE_URL}" in texted and WALK_WARNING not in texted


@pytest.mark.parametrize("routes, said", [
    ({"WALK": (8, 450), "TRANSIT": (9, 1900)},
     "1 Bloor St W is about 8 minutes' walk from University of Toronto (450 m); that's very close. By TTC, "
     "it's about 9 minutes."),
    ({"WALK": (16, 1300), "TRANSIT": (11, 1900)},
     "1 Bloor St W is about 16 minutes' walk from University of Toronto (1.3 km); that's walkable. By TTC, "
     "it's about 11 minutes."),
    ({"WALK": (16, 1300)},
     "1 Bloor St W is about 16 minutes' walk from University of Toronto (1.3 km); that's walkable.")])
async def test_a_place_within_walking_distance_gets_the_walk_first(ctx, routes, said):
    ctx.runtime.maps = FakeMaps(place_name="Trinity Bellwoods Park", place_at=NEAR, routes=routes)
    await _focus(ctx, 1)
    reply = await _ask(ctx, '{"kind": "transit", "to": "Trinity Bellwoods"}')
    assert said.replace("University of Toronto", "Trinity Bellwoods Park") in reply and "in beta" in reply
    assert _calls(ctx) == ["place", "route TRANSIT", "route WALK"]


@pytest.mark.parametrize("walk, said", [
    ((27, 2600), "1 Bloor St W is about 14 minutes by TTC (2.9 km) from Cohere, or about 27 minutes' walk."),
    ((34, 2900), "1 Bloor St W is about 14 minutes by TTC (2.9 km) from Cohere; that's not walking "
                 "distance.")])
async def test_between_2_and_2_5_km_the_walk_is_said_too_only_under_30_minutes(ctx, walk, said):
    ctx.runtime.maps = FakeMaps(place_at=FAR, routes={"TRANSIT": (14, 2900), "WALK": walk})
    await _focus(ctx, 1)
    reply = await _ask(ctx, '{"kind": "transit", "to": "Cohere"}')
    assert said in reply and ("in beta" in reply) == (walk[0] < 30)


async def test_a_long_walk_asked_for_still_carries_the_beta_warning(ctx):
    ctx.runtime.maps = FakeMaps(place_at=FAR_AWAY, routes={"WALK": (75, 5600)})
    await _focus(ctx, 1)
    reply = await _ask(ctx, '{"kind": "walk", "to": "Cohere"}')
    assert ("1 Bloor St W is about 75 minutes' walk from Cohere (5.6 km). Google says its walking routes "
            "are in beta.") in reply
    assert WALK_WARNING in _texts(ctx)[0]


@pytest.mark.parametrize("args", [{"kind": "transit", "to": "the nearest pharmacy"},
                                  {"kind": "walk", "to": "a grocery store"}])
async def test_how_far_the_closest_store_is_however_its_asked(ctx, monkeypatch, args):
    await _focus(ctx, 1)
    await _ask(ctx, json.dumps(args))
    assert _calls(ctx) == ["nearby"]   # the closest one, not a place search
    _stored(ctx, monkeypatch, **STORES)
    ctx.runtime.maps.asked.clear()
    assert (await _ask(ctx, json.dumps(args), "fc_2")).startswith("The closest ")
    assert _calls(ctx) == []


async def test_a_place_with_no_clear_name_no_match_or_no_route(ctx):
    ctx.runtime.maps = FakeMaps(place_name=None, place_at=FAR_AWAY, routes={"TRANSIT": (26, 6000)})
    await _focus(ctx, 1)
    reply = await _ask(ctx, '{"kind": "transit", "to": "my office"}')
    assert "1 Bloor St W is about 26 minutes by TTC (6 km) from the place Google found for my office" in reply
    ctx.runtime.maps = FakeMaps(place_at=None)
    reply = await _ask(ctx, '{"kind": "transit", "to": "Trinity Bellwoods"}', "fc_2")
    assert reply == "I couldn't find Trinity Bellwoods on Google Maps."
    assert _calls(ctx) == ["place"]
    ctx.runtime.maps = FakeMaps(place_at=FAR_AWAY, routes={})
    assert await _ask(ctx, '{"kind": "transit", "to": "Cohere"}', "fc_3") == \
        "I couldn't get a time by TTC to Cohere from Google Maps."


# Our own lists first: Places' landmarks and TTC stations, routed to by their point (one call, no search)

UNION = (43.645642, -79.380208)   # pipeline/data/ttc_subway_stations.json


@pytest.mark.parametrize("to", ["Union", "Union Station", "union station"])
async def test_union_is_union_station_routed_to_by_its_point_in_one_call(ctx, to):
    ctx.runtime.maps = FakeMaps(routes={"TRANSIT": (26, 6000)})
    await _focus(ctx, 1)
    reply = await _ask(ctx, json.dumps({"kind": "transit", "to": to}))
    assert ("1 Bloor St W is about 26 minutes by TTC (6 km) from Union station; that's not walking "
            "distance.") in reply
    assert ctx.runtime.maps.asked == [("route", AT, UNION, "TRANSIT")]   # 3.3 km: no walk, and no search
    assert "Source: Google Maps https://maps.google.com" in _texts(ctx)[0]   # the route's own source


@pytest.mark.parametrize("to", ["U of T", "UofT", "University of Toronto", "the Cohere office"])
async def test_a_landmark_by_any_of_its_names_is_routed_to_without_a_search(ctx, to):
    ctx.runtime.maps = FakeMaps(routes={"TRANSIT": (12, 2100), "WALK": (18, 1500)})
    await _focus(ctx, 1)
    reply = await _ask(ctx, json.dumps({"kind": "transit", "to": to}))
    name = "Cohere" if "Cohere" in to else "University of Toronto, St. George campus"
    assert f"1 Bloor St W is about 18 minutes' walk from {name}" in reply and "that's walkable" in reply
    assert _calls(ctx) == ["route TRANSIT", "route WALK"]   # both within 2.5 km: the walk too
    assert all(isinstance(a[2], tuple) for a in ctx.runtime.maps.asked)   # to the landmark's point


async def test_a_chain_named_alone_is_its_closest_branch(ctx):
    ctx.runtime.maps = FakeMaps(name="Tim Hortons", minutes=4, within=2)
    await _focus(ctx, 1)
    reply = await _ask(ctx, '{"kind": "transit", "to": "Tim Hortons"}')
    assert "The closest Tim Hortons to 1 Bloor St W is Tim Hortons, about 4 minutes' walk." in reply
    assert _calls(ctx) == ["nearby"]
