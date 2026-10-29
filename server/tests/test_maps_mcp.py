"""Google's Maps MCP client (O-07a) against a fake server. Nothing leaves the machine.

The fixtures in fixtures/maps_mcp/ are shaped from the live server's tools/list output schema, not recorded:
there was no key yet. `scripts/maps_mcp_probe.py --live` records real answers as recorded_*.json, and the
last test here replays them when they're there.
"""
import asyncio
import copy
import importlib.util
import json
import logging
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from landline.config import Settings
from landline.orchestrator import maps_mcp
from landline.orchestrator.maps_mcp import (
    DISCLOSURE,
    WALK_WARNING,
    MapsMCP,
    Nearby,
    Place,
    duration_s,
    from_settings,
    place_name,
    to_minutes,
)

FIX = Path(__file__).parent / "fixtures" / "maps_mcp"
KEY = "AIzaSyFAKE-test-key-0000"
LAT, LNG = 43.6649, -79.4190   # Bloor St W near Christie
NOW = datetime(2026, 10, 28, 16, 0, tzinfo=UTC)   # noon in Toronto
# The answer the live server gave a tools/call with no key (28 Oct 2026), word for word.
NO_KEY = {"id": 2, "jsonrpc": "2.0", "result": {"content": [{"text": (
    "Method doesn't allow unregistered callers (callers without established identity). Please use API Key or "
    "other form of API consumer identity to call this API."), "type": "text"}], "isError": True}}


def fixture(name: str) -> dict:
    return json.loads((FIX / f"{name}.json").read_text())


def structured(name: str) -> dict:
    return fixture(name)["result"]["structuredContent"]


def answer(content: dict) -> dict:
    """A tool's JSON-RPC answer for `content`, the way the server sends it."""
    return {"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "text", "text": json.dumps(content)}],
                                                  "structuredContent": content, "isError": False}}


class Server:
    """A fake Maps MCP server. It answers each tools/call by tool (and travel mode, for routes) with a
    JSON-RPC message, an httpx.Response or an exception, and records what it was asked."""

    def __init__(self, answers: dict, sse: bool = False):
        self.answers, self.sse = answers, sse
        self.asked: list[tuple[httpx.Request, dict]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.asked.append((request, body))
        tool, args = body["params"]["name"], body["params"]["arguments"]
        key = f"{tool}_{args.get('travelMode', 'DRIVE').lower()}" if tool == "compute_routes" else tool
        reply = self.answers[key]
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, httpx.Response):
            return reply
        msg = {**{k: v for k, v in reply.items() if not k.startswith("_")}, "id": body["id"]}
        if self.sse:
            return httpx.Response(200, headers={"content-type": "text/event-stream"},
                                  text=f"event: message\ndata: {json.dumps(msg)}\n\n")
        return httpx.Response(200, json=msg)

    def args(self, tool: str) -> list[dict]:
        return [b["params"]["arguments"] for _, b in self.asked if b["params"]["name"] == tool]


def client(server, **kw) -> MapsMCP:
    http = httpx.AsyncClient(transport=httpx.MockTransport(server))
    return MapsMCP(KEY, client=http, now=lambda: NOW, **kw)


def shaped() -> dict:
    names = ("search_places", "compute_routes_walk", "compute_routes_drive", "lookup_weather")
    return {name: fixture(name) for name in names}


def warnings(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records
            if r.name == maps_mcp.__name__ and r.levelno >= logging.WARNING]


# --- One call -----------------------------------------------------------------------------------------------

async def test_a_call_is_one_stateless_post_with_the_key_and_mcp_headers():
    server = Server(shaped())
    out = await client(server)._call("search_places", {"textQuery": "Shoppers Drug Mart"})
    assert out == structured("search_places")
    [(request, body)] = server.asked
    assert str(request.url) == "https://mapstools.googleapis.com/mcp" and request.method == "POST"
    assert request.headers["X-Goog-Api-Key"] == KEY
    assert request.headers["Accept"] == "application/json, text/event-stream"
    assert request.headers["Content-Type"] == "application/json"
    assert request.headers["MCP-Protocol-Version"] == "2026-07-28"
    assert request.headers["Mcp-Method"] == "tools/call" and request.headers["Mcp-Name"] == "search_places"
    assert "Mcp-Session-Id" not in request.headers
    assert body["jsonrpc"] == "2.0" and body["method"] == "tools/call"
    assert body["params"]["name"] == "search_places"
    assert body["params"]["_meta"]["io.modelcontextprotocol/protocolVersion"] == "2026-07-28"


async def test_an_sse_answer_is_read_from_the_event_with_the_request_id():
    msg = {**fixture("compute_routes_walk"), "id": 1}
    msg.pop("_note")
    text = json.dumps(msg, indent=1)   # one JSON over several data: lines
    body = ('event: message\ndata: {"jsonrpc": "2.0", "method": "notifications/progress", "params": {}}\n\n'
            'event: message\ndata: {"jsonrpc": "2.0", "id": 99, "result": {"structuredContent": {}}}\n\n'
            "event: message\n" + "".join(f"data: {line}\r\n" for line in text.splitlines()) + "\r\n")
    server = Server({"compute_routes_walk": httpx.Response(
        200, headers={"content-type": "text/event-stream; charset=utf-8"}, text=body)})
    out = await client(server)._call("compute_routes", {"travelMode": "WALK"})
    assert out == structured("compute_routes_walk")


async def test_text_content_alone_is_read_when_there_is_no_structured_content():
    msg = fixture("compute_routes_drive")
    del msg["result"]["structuredContent"]
    out = await client(Server({"compute_routes_drive": msg}))._call("compute_routes", {"travelMode": "DRIVE"})
    assert out == structured("compute_routes_drive")


async def test_a_missing_key_is_a_403_with_is_error_and_gives_none_with_one_log_line(caplog):
    server = Server({"search_places": httpx.Response(403, json=NO_KEY)})
    assert await client(server)._call("search_places", {"textQuery": "x"}) is None
    [line] = warnings(caplog)
    assert "search_places" in line and "403" in line and "unregistered callers" in line
    assert KEY not in caplog.text


async def test_the_key_is_never_logged_even_when_google_echoes_it(caplog):
    echo = {"jsonrpc": "2.0", "id": 1, "result": {"isError": True, "content": [
        {"type": "text", "text": f"API key not valid: {KEY}"}]}}
    server = Server({"search_places": httpx.Response(400, json=echo)})
    assert await client(server)._call("search_places", {"textQuery": "x"}) is None
    assert KEY not in caplog.text and "[key]" in caplog.text


async def test_a_json_rpc_error_gives_none(caplog):
    err = {"jsonrpc": "2.0", "id": 1, "error": {"code": -32602, "message": "Invalid params"}}
    assert await client(Server({"search_places": err}))._call("search_places", {}) is None
    [line] = warnings(caplog)
    assert "-32602" in line


@pytest.mark.parametrize("failure", [httpx.ReadTimeout("timed out"), httpx.ConnectError("no route to host")])
async def test_a_timeout_or_a_network_error_gives_none(caplog, failure):
    assert await client(Server({"search_places": failure}))._call("search_places", {}) is None
    [line] = warnings(caplog)
    assert "search_places" in line and KEY not in line


async def test_a_slow_answer_is_cut_off_at_the_timeout(caplog):
    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(2)
        return httpx.Response(200, json=fixture("search_places"))

    maps = MapsMCP(KEY, client=httpx.AsyncClient(transport=httpx.MockTransport(slow)), timeout_s=0.05)
    started = time.monotonic()
    assert await maps._call("search_places", {}) is None
    assert time.monotonic() - started < 1
    [line] = warnings(caplog)
    assert "no answer within 0.05s" in line


async def test_html_or_garbage_gives_none():
    server = Server({"search_places": httpx.Response(502, text="<html>Bad Gateway</html>")})
    assert await client(server)._call("search_places", {}) is None


def test_no_key_means_no_client(monkeypatch):
    def settings(key):
        return Settings(database_url=None, web_url="http://localhost:3000", transport="stub",
                        google_maps_api_key=key)

    assert from_settings(settings(None)) is None
    assert from_settings(settings("  ")) is None
    assert isinstance(from_settings(settings(KEY)), MapsMCP)
    monkeypatch.setenv("GOOGLE_MAPS_API_KEY", f"  {KEY}  ")
    assert Settings.load().google_maps_api_key == KEY


# --- Reading Google's answers -------------------------------------------------------------------------------

def test_minutes_come_from_googles_duration_in_code():
    assert duration_s("812s") == 812.0
    assert duration_s("812.5s") == 812.5
    assert to_minutes(duration_s("812s")) == 14     # 13.5 minutes
    assert to_minutes(90) == 2 and to_minutes(89) == 1 and to_minutes(20) == 1 and to_minutes(0) == 1
    for bad in (None, "", "13m", "PT13M", 812, "s"):
        assert duration_s(bad) is None


BULLETS = ("Here are a few pharmacies near you:\n\n"
           "*   **Shoppers Drug Mart** [0] is a pharmacy and convenience store on Bloor Street West.\n"
           "*   **Rexall** [1] is about a 10-minute walk away.")


@pytest.mark.parametrize(("summary", "index", "query", "name"), [
    # Gemini-style list: the bold name of each item
    (BULLETS, 0, "", "Shoppers Drug Mart"),
    (BULLETS, 1, "", "Rexall"),
    ("* **No Frills** [0] and **FreshCo** [1] are both discount grocers.", 1, "", "FreshCo"),
    ("1. **Loblaws** - a large supermarket at 650 Dupont St [0]", 0, "", "Loblaws"),
    ("Near **Christie Pits**, there is **Shoppers Drug Mart** [0].", 0, "", "Shoppers Drug Mart"),
    # prose: the name right before its citation
    ("The closest grocery store is No Frills [0], a discount supermarket. Loblaws [1] is also nearby.", 0, "",
     "No Frills"),
    ("The closest grocery store is No Frills [0], a discount supermarket. Loblaws [1] is also nearby.", 1, "",
     "Loblaws"),
    ("**Closest option:** Shoppers Drug Mart [0], about a 5-minute walk.", 0, "", "Shoppers Drug Mart"),
    ("Try Shoppers Drug Mart [0] for prescriptions.", 0, "", "Shoppers Drug Mart"),
    ("A Shoppers Drug Mart [0] sits at the corner.", 0, "", "Shoppers Drug Mart"),
    ("Pusateri’s Fine Foods [0] is a specialty grocer.", 0, "", "Pusateri’s Fine Foods"),
    ("7-Eleven [0] is open 24 hours.", 0, "", "7-Eleven"),
    ("The Beer Store [0] is on Bloor.", 0, "", "The Beer Store"),
    # past an address or where it is, to the name
    ("You can find Shoppers Drug Mart at 523 Bloor St W [0], open 8 AM to 10 PM.", 0, "",
     "Shoppers Drug Mart"),
    ("Shoppers Drug Mart, 360 Bloor St W [0] is open until 10 PM.", 0, "", "Shoppers Drug Mart"),
    ("Metro (425 Bloor St W) [0] is a full-service grocery store.", 0, "", "Metro"),
    ("Rexall on Queen St W [0] is open late.", 0, "", "Rexall"),
    ("The closest is the Shoppers Drug Mart at Bloor and Christie [0].", 0, "", "Shoppers Drug Mart"),
    ("You can shop at Loblaws [0] for groceries.", 0, "", "Loblaws"),
    # citation at the end of the sentence: only a name that shares a word with the question
    ("Shoppers Drug Mart is a pharmacy about 400 metres away, open until 10 PM [0].", 0, "Shoppers",
     "Shoppers Drug Mart"),
    ("There are two Shoppers Drug Mart locations nearby [0][1].", 0, "Shoppers", "Shoppers Drug Mart"),
    # not clear, so None rather than a guess
    ("Shoppers Drug Mart is a pharmacy about 400 metres away, open until 10 PM [0].", 0, "pharmacy", None),
    ("There are several grocery options within walking distance [0][1][2].", 0, "grocery store", None),
    ("Good options include No Frills and Loblaws [0, 1].", 0, "grocery store", None),
    ("The nearest one is at 360 Bloor St W [0].", 0, "", None),
    ("There's a pharmacy at Bloor and Christie [0].", 0, "", None),
    ("There's a pharmacy near Christie Pits [0].", 0, "", None),
    ("Located in Toronto [0], this store is open late.", 0, "", None),
    ("Rated 4.2 stars on Google [0].", 0, "", None),
    (BULLETS, 2, "", None),
    ("", 0, "Shoppers", None),
])
def test_a_place_name_is_read_from_the_summary_or_left_none(summary, index, query, name):
    assert place_name(summary, index, query) == name


# --- nearby, route, weather ---------------------------------------------------------------------------------

async def test_nearby_finds_a_place_and_the_walk_to_it():
    server = Server(shaped())
    place = await client(server).nearby(LAT, LNG, "  Shoppers  Drug Mart ")
    [search] = server.args("search_places")
    assert search == {"textQuery": "Shoppers Drug Mart",
                      "locationBias": {"circle": {"center": {"latitude": LAT, "longitude": LNG},
                                                  "radiusMeters": 1500}},
                      "regionCode": "CA", "pageSize": 5}
    [walk] = server.args("compute_routes")
    assert walk == {"origin": {"latLng": {"latitude": LAT, "longitude": LNG}},
                    "destination": {"placeId": "ChIJshaped0ShoppersBloorWest"}, "travelMode": "WALK"}
    link = "https://maps.google.com/?cid=ChIJshaped0ShoppersBloorWest"
    assert place == Nearby(
        name="Shoppers Drug Mart", place_id="ChIJshaped0ShoppersBloorWest", lat=43.6652, lng=-79.4078,
        maps_url=link, summary=structured("search_places")["summary"], minutes=14, distance_m=1010,
        warning=WALK_WARNING, within_1km=1, attribution="Google Maps", attribution_url=link, fetched_at=NOW)
    assert place.keep_until == NOW + timedelta(days=30)


def _places(*at: tuple[float, float] | None) -> dict:
    """A search answer with one place at each point (None: a place with no location)."""
    places = [{"id": f"p{i}", **({"location": {"latitude": p[0], "longitude": p[1]}} if p else {})}
              for i, p in enumerate(at)]
    return answer({"summary": " ".join(f"**Store {i}** [{i}]." for i in range(len(at))), "places": places})


@pytest.mark.parametrize("at, near", [
    ([(43.6655, -79.4170), (43.6700, -79.4190), (43.6800, -79.4300)], 2),   # about 170 m, 570 m, 1.9 km
    ([(43.6650, -79.4180)] * 5, 5),
    ([(43.6800, -79.4300), None], 0),
])
async def test_nearby_counts_the_matches_within_1_km_in_a_straight_line(at, near):
    server = Server({**shaped(), "search_places": _places(*at)})
    place = await client(server).nearby(LAT, LNG, "grocery store")
    assert place.within_1km == near


async def test_a_named_place_is_googles_first_match_for_the_words_in_toronto():
    search = copy.deepcopy(fixture("search_places"))
    content = search["result"]["structuredContent"]
    content["summary"] = "**Cohere** [0] is an AI company with offices on John Street."
    search["result"]["structuredContent"] = content
    server = Server({**shaped(), "search_places": search})
    maps = client(server)
    place = await maps.place(LAT, LNG, "  Cohere ")
    assert server.args("search_places") == [{
        "textQuery": "Cohere Toronto",
        "locationBias": {"circle": {"center": {"latitude": LAT, "longitude": LNG}, "radiusMeters": 5000}},
        "regionCode": "CA", "pageSize": 1}]
    first = content["places"][0]
    link = first["googleMapsLinks"]["placeUrl"]
    assert place == Place(name="Cohere", place_id=first["id"], lat=43.6652, lng=-79.4078, maps_url=link,
                          summary=content["summary"], attribution="Google Maps",
                          attribution_url=first["attribution"]["url"], fetched_at=NOW)
    assert not server.args("compute_routes")   # finding it routes nothing
    await maps.route((LAT, LNG), place, "WALK")
    assert server.args("compute_routes")[-1]["destination"] == {"placeId": first["id"]}
    await maps.place(LAT, LNG, "University of Toronto")
    assert server.args("search_places")[-1]["textQuery"] == "University of Toronto"


async def test_a_named_place_with_no_clear_name_or_no_match():
    server = Server({**shaped(), "search_places": _places((43.6505, -79.3910))})
    server.answers["search_places"]["result"]["structuredContent"]["summary"] = "Here's what I found [0]."
    place = await client(server).place(LAT, LNG, "my office")
    assert place.name is None and place.place_id == "p0"
    empty = Server({"search_places": answer({"summary": "Nothing.", "places": []})})
    assert await client(empty).place(LAT, LNG, "Cohere") is None
    assert await client(Server({})).place(LAT, LNG, "  ") is None


async def test_nearby_takes_the_closest_match_and_its_name():
    search = copy.deepcopy(fixture("search_places"))
    content = search["result"]["structuredContent"]
    content["summary"] = ("* **Loblaws** [0] is a large supermarket.\n"
                          "* **No Frills** [1] is a discount grocer.")
    content["places"] = content["places"][:2]
    content["places"][0]["location"] = {"latitude": 43.6800, "longitude": -79.4300}   # about 1.9 km
    content["places"][1]["location"] = {"latitude": 43.6655, "longitude": -79.4170}   # about 170 m
    search["result"]["structuredContent"] = content
    server = Server({**shaped(), "search_places": search})
    place = await client(server).nearby(LAT, LNG, "grocery store")
    assert place.name == "No Frills" and place.place_id == content["places"][1]["id"]
    assert server.args("compute_routes")[0]["destination"] == {"placeId": content["places"][1]["id"]}


async def test_nearby_keeps_the_place_when_only_the_walk_fails():
    server = Server({**shaped(), "compute_routes_walk": httpx.Response(403, json=NO_KEY)})
    place = await client(server).nearby(LAT, LNG, "Shoppers Drug Mart")
    assert place.name == "Shoppers Drug Mart" and place.maps_url
    assert place.minutes is None and place.distance_m is None and place.warning is None


async def test_nearby_with_no_match_or_no_search_is_none():
    empty = answer({"summary": "I couldn't find any matching places.", "places": []})
    server = Server({**shaped(), "search_places": empty})
    assert await client(server).nearby(LAT, LNG, "unicorn stable") is None
    assert not server.args("compute_routes")
    refused = Server({"search_places": httpx.Response(403, json=NO_KEY)})
    assert await client(refused).nearby(LAT, LNG, "x") is None
    assert await client(Server({})).nearby(LAT, LNG, "   ") is None


async def test_route_drives_to_an_address_read_near_the_listing():
    server = Server(shaped())
    drive = await client(server).route((LAT, LNG), "Union Station, Toronto", "drive")
    assert server.args("compute_routes") == [{
        "origin": {"latLng": {"latitude": LAT, "longitude": LNG}},
        "destination": {"address": "Union Station, Toronto"}, "travelMode": "DRIVE",
        "locationBias": {"latitude": LAT, "longitude": LNG}}]
    assert (drive.mode, drive.minutes, drive.distance_m, drive.km) == ("DRIVE", 15, 5230, 5.2)
    assert drive.warning is None
    assert drive.attribution == "Google Maps" and drive.fetched_at == NOW


async def test_a_walk_to_a_found_place_goes_by_its_id_and_carries_the_beta_warning():
    server = Server(shaped())
    maps = client(server)
    place = await maps.nearby(LAT, LNG, "Shoppers Drug Mart")
    walk = await maps.route((LAT, LNG), place, "WALK")
    assert server.args("compute_routes")[-1] == {
        "origin": {"latLng": {"latitude": LAT, "longitude": LNG}},
        "destination": {"placeId": "ChIJshaped0ShoppersBloorWest"}, "travelMode": "WALK"}
    assert (walk.minutes, walk.km, walk.warning) == (14, 1.0, WALK_WARNING)


async def test_a_route_with_no_routes_is_none_and_an_unknown_mode_is_a_bug():
    server = Server({"compute_routes_transit": answer({"routes": []})})
    assert await client(server).route((LAT, LNG), "Union Station, Toronto", "TRANSIT") is None
    with pytest.raises(ValueError):
        await client(server).route((LAT, LNG), "Union Station", "teleport")


async def test_weather_is_one_line_for_the_day():
    server = Server(shaped())
    day = date(2026, 10, 31)
    wx = await client(server).weather(LAT, LNG, day)
    assert server.args("lookup_weather") == [{"location": {"latLng": {"latitude": LAT, "longitude": LNG}},
                                              "date": {"year": 2026, "month": 10, "day": 31},
                                              "unitsSystem": "METRIC"}]
    assert wx.summary == "Light rain, high 12°C, low 6°C, 60% chance of rain."
    assert (wx.day, wx.condition, wx.high_c, wx.low_c, wx.precip_pct) == (day, "Light rain", 12, 6, 60)
    assert wx.attribution == "Google Maps" and wx.attribution_url == "https://www.google.com/maps"


async def test_weather_reads_the_nested_answer_and_a_left_out_zero():
    # The newer answer nests the day under "weather", and Google's JSON leaves out a 0% chance.
    nested = answer({"weather": {
        "maxTemperature": {"degrees": 8.4}, "minTemperature": {"degrees": -1.5},
        "weatherCondition": {"description": {"text": "Sunny"}},
        "precipitation": {"probability": {"type": "SNOW"}},
        "attribution": {"title": "Google Maps", "url": "https://www.google.com/maps"}}})
    wx = await client(Server({"lookup_weather": nested})).weather(LAT, LNG, date(2026, 10, 29))
    assert wx.summary == "Sunny, high 8°C, low -1°C, 0% chance of snow."


async def test_weather_outside_the_forecast_asks_nothing():
    server = Server(shaped())
    maps = client(server)
    assert await maps.weather(LAT, LNG, date(2026, 10, 27)) is None    # yesterday in Toronto
    assert await maps.weather(LAT, LNG, date(2026, 11, 7)) is None     # 10 days ahead
    assert await maps.weather(LAT, LNG, date(2026, 11, 6)) is not None
    assert len(server.asked) == 1


def test_the_disclosure_is_googles_wording():
    assert DISCLOSURE == "AI generated content may include information from Google Maps"


# --- Recorded answers, the probe ---------------------------------------------------------------------------

RECORDED = [FIX / "recorded_search_places.json", FIX / "recorded_compute_routes_walk.json"]


@pytest.mark.skipif(not all(p.exists() for p in RECORDED),
                    reason="no live recording yet: scripts/maps_mcp_probe.py --live")
async def test_a_recorded_live_answer_still_reads():
    search, walk = (json.loads(p.read_text()) for p in RECORDED)
    asked = search["_arguments"]
    center = asked["locationBias"]["circle"]["center"]
    server = Server({"search_places": search, "compute_routes_walk": walk})
    place = await client(server).nearby(center["latitude"], center["longitude"], asked["textQuery"])
    assert place is not None and place.place_id and place.maps_url and place.attribution
    assert place.minutes is not None and place.minutes >= 1


def test_the_probe_without_live_sends_nothing(capsys):
    path = Path(__file__).parents[2] / "scripts" / "maps_mcp_probe.py"
    spec = importlib.util.spec_from_file_location("maps_mcp_probe", path)
    probe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(probe)
    assert probe.main([]) == 0
    assert "--live" in capsys.readouterr().out
