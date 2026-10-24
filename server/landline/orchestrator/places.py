"""Places (D-04): a place the renter names ("near King and Bay", "close to Union") becomes a point on the map,
off the hot path; an `AnchorResolved` event then re-ranks the board (docs/ARCHITECTURE.md, Profiles).

In order of trust, cheapest first:
1. a City neighbourhood whose name holds every word of it ("the Annex") → the neighbourhood's middle;
2. a TTC subway station ("Union", "St George station") → the station;
3. an intersection ("King and Bay", "Queen & Spadina") → the node where the two streets meet in
   OpenStreetMap, found through the Overpass API;
4. anything else ("University of Toronto") → Nominatim, bounded to Toronto.

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
from landline.scoring.features import Hood

log = logging.getLogger(__name__)

STATIONS = ROOT / "pipeline" / "data" / "ttc_subway_stations.json"
SOUTH, WEST, NORTH, EAST = 43.58, -79.64, 43.86, -79.11   # Toronto, padded
OVERPASS = "https://overpass-api.de/api/interpreter"
NOMINATIM = "https://nominatim.openstreetmap.org/search"
MIN_GAP_S = 1.0            # Nominatim's and Overpass's usage policies: at most one request a second
TIMEOUT_S = 15

_INTERSECTION = re.compile(r"^\s*(?:the\s+)?(.+?)\s+(?:and|&|at|/)\s+(.+?)\s*$", re.I)
_STREET_TYPE = (r"(?:street|st|avenue|ave|road|rd|boulevard|blvd|drive|dr|crescent|cres|lane|way|trail|place|"
                r"court|parkway|pkwy)")
_DIRECTION = r"(?:east|west|north|south|e|w|n|s)"
_STATION_WORDS = {"station", "subway", "stn", "the", "ttc"}


@dataclass(frozen=True)
class Point:
    lat: float
    lng: float
    how: str             # neighbourhood | station | intersection | geocoder


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
    core = r"\s+".join(re.escape(w) for w in words) or re.escape(name.strip())
    return rf"^{core}( {_STREET_TYPE})?( {_DIRECTION})?$"


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower().replace("st.", "st"))


def _key(text: str) -> str:
    return " ".join(_words(text))
