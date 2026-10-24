"""Places (D-04): a place the renter names ("near King and Bay", "close to Union") becomes a point on the map,
off the hot path; an `AnchorResolved` event then re-ranks the board (docs/ARCHITECTURE.md, Profiles).

In order of trust, cheapest first:
1. a City neighbourhood whose name holds every word of it ("the Annex") → the neighbourhood's middle;
2. a TTC subway station ("Union", "St George station") → the station;
3. an intersection ("King and Bay", "Queen & Spadina") → the node where the two streets meet in
   OpenStreetMap, found through the Overpass API;
4. a street ("Queen Street", "Bloor St E") → the whole street as lines, from Overpass (#102). Its point is
   one on the line, for the map; the runtime has the location term measure from the line;
5. anything else ("University of Toronto") → Nominatim, bounded to Toronto.

Overpass's regular expressions are POSIX: plain groups only, never `(?:…)`.

The network steps identify themselves with a contact address, keep to one request a second, and remember
their answers for the life of the process. Without a contact address they're skipped. A place that can't be
found stays unresolved, so the location term ignores it rather than guessing.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import httpx

from landline.config import ROOT
from landline.scoring.features import Hood, haversine_km

log = logging.getLogger(__name__)

STATIONS = ROOT / "pipeline" / "data" / "ttc_subway_stations.json"
SOUTH, WEST, NORTH, EAST = 43.58, -79.64, 43.86, -79.11   # Toronto, padded
OVERPASS = "https://overpass-api.de/api/interpreter"
NOMINATIM = "https://nominatim.openstreetmap.org/search"
MIN_GAP_S = 1.0            # Nominatim's and Overpass's usage policies: at most one request a second
TIMEOUT_S = 15
THIN_KM = 0.1              # a street's line keeps a vertex every 100 m at most: plenty for a 2.5 km decay

_INTERSECTION = re.compile(r"^\s*(?:the\s+)?(.+?)\s+(?:and|&|at|/)\s+(.+?)\s*$", re.I)
_STREET_TYPE = (r"(street|st|avenue|ave|road|rd|boulevard|blvd|drive|dr|crescent|cres|lane|way|trail|place|"
                r"court|parkway|pkwy)")
_DIRECTION = r"(east|west|north|south|e|w|n|s)"
_STREET_NAME = re.compile(rf"^\s*(?:the\s+)?(?P<core>.+?)\s+{_STREET_TYPE}\.?"
                          rf"(?:\s+(?P<dir>{_DIRECTION})\.?)?\s*$", re.I)
_ROADS = "^(motorway|trunk|primary|secondary|tertiary|unclassified|residential|living_street)$"
_COMPASS = {"e": "east", "w": "west", "n": "north", "s": "south"}
_STATION_WORDS = {"station", "subway", "stn", "the", "ttc"}

Line = tuple[tuple[tuple[float, float], ...], ...]   # paths of (lat, lng)


@dataclass(frozen=True)
class Point:
    lat: float
    lng: float
    how: str             # neighbourhood | station | intersection | street | geocoder
    line: Line = ()      # a street's paths, which the location term measures from (#102)


class Places:
    def __init__(self, neighbourhoods: Callable[[list[str]], list[Hood]], stations: list[dict] | None = None,
                 contact: str | None = None, client: httpx.AsyncClient | None = None):
        self._named = neighbourhoods
        self._stations = {_key(s["name"]): s for s in (stations if stations is not None else load_stations())}
        self.contact = contact
        self._client = client
        self._known: dict[str, Point | None] = {}
        self._gap = asyncio.Lock()
        self._last = 0.0

    async def resolve(self, label: str) -> Point | None:
        key = _key(label)
        if not key:
            return None
        if key not in self._known:
            self._known[key] = await self._resolve(label)
        return self._known[key]

    async def _resolve(self, label: str) -> Point | None:
        if hoods := self._named([label]):
            h = hoods[0]
            return Point(h.lat, h.lng, "neighbourhood")
        station = self._stations.get(_key(" ".join(w for w in _words(label) if w not in _STATION_WORDS)))
        if station:
            return Point(station["lat"], station["lng"], "station")
        if not self.contact:
            return None
        try:
            if m := _INTERSECTION.match(label):
                if p := await self._intersection(m.group(1), m.group(2)):
                    return p
            elif (m := _STREET_NAME.match(label)) and (p := await self._street_line(m["core"], m["dir"])):
                return p
            return await self._geocode(label)
        except (httpx.HTTPError, ValueError, KeyError) as e:
            log.warning("place %r: lookup failed (%s)", label, e)
            return None

    async def _intersection(self, a: str, b: str) -> Point | None:
        bbox = f"({SOUTH},{WEST},{NORTH},{EAST})"
        query = (f'[out:json][timeout:20];way["highway"]["name"~"{_street(a)}",i]{bbox}->.a;'
                 f'way["highway"]["name"~"{_street(b)}",i]{bbox}->.b;node(w.a)(w.b);out;')
        data = (await self._request("POST", OVERPASS, data={"data": query})).json()
        nodes = data.get("elements") or []
        if not nodes:
            return None
        return Point(sum(n["lat"] for n in nodes) / len(nodes), sum(n["lon"] for n in nodes) / len(nodes),
                     "intersection")

    async def _street_line(self, core: str, direction: str | None) -> Point | None:
        """Every road OSM names so inside Toronto, as lines: "Queen Street" is Queen Street West and East,
        "Queen St W" only the west part. The point is the vertex nearest the line's middle."""
        bbox = f"({SOUTH},{WEST},{NORTH},{EAST})"
        query = (f'[out:json][timeout:20];way["highway"~"{_ROADS}"]["name"~"{_named(core, direction)}",i]'
                 f"{bbox};out geom;")
        data = (await self._request("POST", OVERPASS, data={"data": query})).json()
        line = tuple(path for way in data.get("elements") or ()
                     if (path := _thin([(g["lat"], g["lon"]) for g in way.get("geometry") or ()])))
        if not line:
            return None
        points = [p for path in line for p in path]
        mid = (sum(p[0] for p in points) / len(points), sum(p[1] for p in points) / len(points))
        lat, lng = min(points, key=lambda p: haversine_km(*p, *mid))
        return Point(lat, lng, "street", line)

    async def _geocode(self, label: str) -> Point | None:
        params = {"q": f"{label}, Toronto", "format": "jsonv2", "limit": "1", "countrycodes": "ca",
                  "viewbox": f"{WEST},{NORTH},{EAST},{SOUTH}", "bounded": "1"}
        found = (await self._request("GET", NOMINATIM, params=params)).json()
        if not found:
            return None
        return Point(float(found[0]["lat"]), float(found[0]["lon"]), "geocoder")

    async def _request(self, method: str, url: str, **kw) -> httpx.Response:
        async with self._gap:   # one request a second, across every session
            wait = self._last + MIN_GAP_S - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._last = time.monotonic()
            client = self._client or httpx.AsyncClient(timeout=TIMEOUT_S)
            try:
                agent = f"LandlineResearch/0.1 (hackathon prototype; +mailto:{self.contact})"
                resp = await client.request(method, url, headers={"User-Agent": agent}, **kw)
            finally:
                if self._client is None:
                    await client.aclose()
        resp.raise_for_status()
        return resp


def load_stations(path: Path = STATIONS) -> list[dict]:
    return json.loads(path.read_text())["stations"] if path.exists() else []


def _street(name: str) -> str:
    """An OSM name pattern for a street as people say it: "King" matches "King Street West"."""
    words = [w for w in re.findall(r"[A-Za-z0-9']+", name)]
    while words and re.fullmatch(_STREET_TYPE + r"|" + _DIRECTION, words[-1], re.I):
        words.pop()
    core = " ".join(re.escape(w) for w in words) or re.escape(name.strip())
    return rf"^{core}( {_STREET_TYPE})?( {_DIRECTION})?$"


def _named(core: str, direction: str | None) -> str:
    """The OSM name pattern for a street the renter named: with a direction, only that part of it."""
    words = " ".join(re.escape(w) for w in re.findall(r"[A-Za-z0-9']+", core))
    if not direction:
        return rf"^{words}( {_STREET_TYPE})?( (east|west|north|south))?$"
    side = direction.lower()
    return rf"^{words}( {_STREET_TYPE})? {_COMPASS.get(side, side)}$"


def _thin(path: list[tuple[float, float]]) -> tuple[tuple[float, float], ...]:
    """A path with its first and last vertex, and the ones between at least THIN_KM apart."""
    if len(path) < 3:
        return tuple(path)
    kept = [path[0]]
    for p in path[1:-1]:
        if haversine_km(*kept[-1], *p) >= THIN_KM:
            kept.append(p)
    return (*kept, path[-1])


_APOSTROPHE = re.compile(r"['’]")   # "Queen's", "Queens" and "Queen’s" are one word


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", _APOSTROPHE.sub("", text.lower()).replace("st.", "st"))


def _key(text: str) -> str:
    return " ".join(_words(text))
