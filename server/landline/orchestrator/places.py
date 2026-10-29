"""Places (D-04): a place the renter names ("near King and Bay", "close to Union") becomes a point on the map,
off the hot path; an `AnchorResolved` event then re-ranks the board (docs/ARCHITECTURE.md, Profiles).

In order of trust, cheapest first:
1. one of Toronto's big workplaces and campuses ("Cohere", "UofT", "TD Centre"), by any of its names in
   pipeline/data/toronto_landmarks.json → its building;
2. a City neighbourhood whose name holds every word of it ("the Annex") → the neighbourhood's middle;
3. a TTC subway station ("Union", "St George station") → the station;
4. an intersection ("King and Bay", "Queen & Spadina") → the node where the two streets meet in
   OpenStreetMap, found through the Overpass API;
5. a street ("Queen Street", "Bloor St E") → the whole street as lines, from Overpass (#102). Its point is
   one on the line, for the map; the runtime has the location term measure from the line;
6. anything else ("Christie Pits") → Nominatim, bounded to Toronto.

Two answers are a question back to the renter (`Unplaced`), never a guess:
- a chain or a bank named alone ("TD Bank", "a Tim Hortons") has many branches, so no one of them is picked.
  Named with a place ("the TD at Bloor and Bathurst"), the place is looked up instead;
- the geocoder's answer for a name like a building's that lands far out of downtown (`hold`).

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
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

import httpx

from landline.config import ROOT
from landline.scoring.features import Hood, haversine_km

log = logging.getLogger(__name__)

STATIONS = ROOT / "pipeline" / "data" / "ttc_subway_stations.json"
LANDMARKS = ROOT / "pipeline" / "data" / "toronto_landmarks.json"
SOUTH, WEST, NORTH, EAST = 43.58, -79.64, 43.86, -79.11   # Toronto, padded
OVERPASS = "https://overpass-api.de/api/interpreter"
NOMINATIM = "https://nominatim.openstreetmap.org/search"
MIN_GAP_S = 1.0            # Nominatim's and Overpass's usage policies: at most one request a second
TIMEOUT_S = 15
THIN_KM = 0.1              # a street's line keeps a vertex every 100 m at most: plenty for a 2.5 km decay
UNION = (43.6453, -79.3806)   # Union station (ttc_subway_stations.json): downtown, for `hold`
FAR_KM = 12.0              # past this from Union is out of downtown: Yorkdale is 10.6 km, Kennedy 13.5

_INTERSECTION = re.compile(r"^\s*(?:the\s+)?(.+?)\s+(?:and|&|at|/)\s+(.+?)\s*$", re.I)
_STREET_TYPE = (r"(street|st|avenue|ave|road|rd|boulevard|blvd|drive|dr|crescent|cres|lane|way|trail|place|"
                r"court|parkway|pkwy)")
_DIRECTION = r"(east|west|north|south|e|w|n|s)"
_STREET_NAME = re.compile(rf"^\s*(?:the\s+)?(?P<core>.+?)\s+{_STREET_TYPE}\.?"
                          rf"(?:\s+(?P<dir>{_DIRECTION})\.?)?\s*$", re.I)
_ROADS = "^(motorway|trunk|primary|secondary|tertiary|unclassified|residential|living_street)$"
_COMPASS = {"e": "east", "w": "west", "n": "north", "s": "south"}
_STATION_WORDS = {"station", "subway", "stn", "the", "ttc"}
# Words around a landmark's name that don't change which one it is: "the Cohere office", "my work at Cohere".
_LANDMARK_WORDS = {"the", "my", "our", "work", "job", "at", "in", "near", "by", "toronto", "downtown",
                   "office", "offices", "hq", "headquarters", "building", "campus", "main"}
# Chains and banks with many branches in Toronto, as people say them, and the name to ask about.
CHAINS = {
    "td": "TD", "td bank": "TD", "td canada trust": "TD", "rbc": "RBC", "royal bank": "RBC",
    "rbc royal bank": "RBC", "scotiabank": "Scotiabank", "scotia": "Scotiabank", "scotia bank": "Scotiabank",
    "cibc": "CIBC", "bmo": "BMO", "bank of montreal": "BMO", "tim hortons": "Tim Hortons",
    "tims": "Tim Hortons", "timmies": "Tim Hortons", "starbucks": "Starbucks", "second cup": "Second Cup",
    "mcdonalds": "McDonald's",
    "shoppers": "Shoppers Drug Mart", "shoppers drug mart": "Shoppers Drug Mart", "rexall": "Rexall",
    "loblaws": "Loblaws", "no frills": "No Frills", "metro": "Metro", "sobeys": "Sobeys",
    "food basics": "Food Basics", "freshco": "FreshCo", "farm boy": "Farm Boy", "longos": "Longo's",
    "whole foods": "Whole Foods", "lcbo": "LCBO", "beer store": "The Beer Store", "dollarama": "Dollarama",
    "walmart": "Walmart", "canadian tire": "Canadian Tire", "costco": "Costco", "goodlife": "GoodLife",
    "goodlife fitness": "GoodLife",
}
# Words around a chain's name that still leave it any branch: "a TD branch", "the nearest Starbucks".
_CHAIN_WORDS = {"a", "an", "the", "my", "any", "some", "one", "that", "this", "nearest", "closest", "local",
                "bank", "branch", "store", "location", "shop", "outlet", "cafe", "restaurant", "pharmacy",
                "office"}
# A chain named with where it is: "the TD at Bloor and Bathurst", "Starbucks on Ossington".
_CHAIN_AT = re.compile(r"^\s*(?P<chain>.+?)\s+(?:at|on|near|by|off|around|beside|next to|close to)\s+"
                       r"(?P<place>.+?)\s*$", re.I)

Line = tuple[tuple[tuple[float, float], ...], ...]   # paths of (lat, lng)


@dataclass(frozen=True)
class Point:
    lat: float
    lng: float
    how: str             # neighbourhood | station | intersection | street | geocoder
    line: Line = ()      # a street's paths, which the location term measures from (#102)
    named: str = ""      # the geocoder's words for what it found ("TD Computer Centre on Progress Avenue")
    name: str = ""       # and its name alone ("TD Computer Centre"), when it has one


@dataclass(frozen=True)
class Unplaced:
    """A place left off the map on purpose, and what to ask the renter instead."""
    label: str
    reason: str          # chain | far
    ask: str


class Places:
    def __init__(self, neighbourhoods: Callable[[list[str]], list[Hood]], stations: list[dict] | None = None,
                 contact: str | None = None, client: httpx.AsyncClient | None = None,
                 landmarks: list[dict] | None = None):
        self._named = neighbourhoods
        self._stations = {_key(s["name"]): s for s in (stations if stations is not None else load_stations())}
        self._landmarks = {_key(name): x for x in (landmarks if landmarks is not None else load_landmarks())
                           for name in (x["name"], *x.get("aliases", ()))}
        self.contact = contact
        self._client = client
        self._known: dict[str, asyncio.Future[Point | None]] = {}
        self._held: dict[str, Point] = {}   # a held answer, by its words: the renter may say it's the one
        self._gap = asyncio.Lock()
        self._last = 0.0

    def known(self, label: str) -> Point | Unplaced | None:
        """What the built-in lists say, with no network: a landmark's building, or a chain named without
        where (Unplaced). None for anything else."""
        where = place_of(label)
        if landmark := _match(self._landmarks, where):
            return Point(landmark["lat"], landmark["lng"], "landmark")
        if held := _match(self._held, where):   # "Do you mean …?" "Yes": the answer it held back
            return held
        if brand := chain(where):
            return Unplaced(label, "chain", f'Ask which {brand}, by its street or intersection ("Which '
                                            f'{brand}, the one at King and Bay?").')
        return None

    async def resolve(self, label: str, near: Iterable[tuple[float, float]] = ()) -> Point | Unplaced | None:
        """Where the place is. `near` holds the renter's other places, for `hold`. Two callers asking for one
        place at once share one lookup, and its answer is kept for the life of the process."""
        if (found := self.known(label)) is not None:
            return found
        key = _key(place_of(label))
        if not key:
            return None
        lookup = self._known.get(key)
        if lookup is None or lookup.cancelled():
            lookup = self._known[key] = asyncio.ensure_future(self._resolve(place_of(label)))
        point = await asyncio.shield(lookup)   # a caller's timeout leaves the lookup running for the next one
        if point is not None and (held := hold(label, point, list(near))):
            for words in filter(None, (point.named, point.name)):   # "Yes, TD Computer Centre"
                self._held[_key(words)] = point
            return held
        return point

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
        except Exception as e:   # one line, with what failed: a timeout's message is empty
            log.warning("place %r: lookup failed (%r)", label, e)
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
        params["addressdetails"] = "1"
        found = (await self._request("GET", NOMINATIM, params=params)).json()
        if not found:
            return None
        hit = found[0]
        return Point(float(hit["lat"]), float(hit["lon"]), "geocoder", named=_described(hit),
                     name=hit.get("name") or "")

    async def _request(self, method: str, url: str, **kw) -> httpx.Response:
        async with self._gap:   # one request a second, across every session
            wait = self._last + MIN_GAP_S - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._last = time.monotonic()
            client = self._client or httpx.AsyncClient(timeout=TIMEOUT_S)
            try:
                agent = f"LandlineResearch/0.1 (research prototype; +mailto:{self.contact})"
                resp = await client.request(method, url, headers={"User-Agent": agent}, **kw)
            finally:
                if self._client is None:
                    await client.aclose()
        resp.raise_for_status()
        return resp


def hold(label: str, point: Point | None, near: list[tuple[float, float]]) -> Unplaced | None:
    """The geocoder's answer, held back as a question when it is likely the wrong place of that name.

    The rule: the renter named something like a building or a company (no street number, not an
    intersection or a street), the geocoder put it more than FAR_KM from Union, and their other places are
    all downtown, or there are none. Nominatim gives "TD Centre" as the TD Computer Centre in Scarborough;
    a renter whose search is downtown more likely meant the towers at King and Bay. So the list doesn't use
    it, and the renter is asked. A far place among other far ones ("near Scarborough Town Centre", with
    Scarborough in the search) is kept."""
    if point is None or point.how != "geocoder" or _far_km(point.lat, point.lng) <= FAR_KM:
        return None
    where = place_of(label)
    if re.search(r"\d", where) or _INTERSECTION.match(where) or _STREET_NAME.match(where):
        return None
    if any(_far_km(lat, lng) > FAR_KM for lat, lng in near):
        return None
    found, km = point.named, round(_far_km(point.lat, point.lng))
    return Unplaced(label, "far", f'Ask "Do you mean {found}?" ({km} km from downtown). If yes, call '
                                  f'update_preferences again with the anchor "{found}"; if not, ask for a '
                                  "street or intersection near it.")


def place_of(label: str) -> str:
    """The place in a chain's name and where it is: "the TD at Bloor and Bathurst" → "Bloor and Bathurst".
    Anything else as said."""
    m = _CHAIN_AT.match(label)
    return m["place"] if m and chain(m["chain"]) else label


def chain(label: str) -> str | None:
    """The chain a label names with nothing to say which branch ("TD Bank", "a Tim Hortons"), else None."""
    words = _words(label)
    for key in (" ".join(words), " ".join(w for w in words if w not in _CHAIN_WORDS)):
        if key in CHAINS:
            return CHAINS[key]
    return None


def load_stations(path: Path = STATIONS) -> list[dict]:
    return json.loads(path.read_text())["stations"] if path.exists() else []


def load_landmarks(path: Path = LANDMARKS) -> list[dict]:
    return json.loads(path.read_text())["landmarks"] if path.exists() else []


def _match[T](names: dict[str, T], label: str) -> T | None:
    """A place by its name or an alias, the way stations match (case, apostrophes, spaces), then with the
    words around it dropped ("the Cohere office") and a possessive ("Cohere's")."""
    for said in (label, re.sub(r"['’]s\b", "", label)):
        words = _words(said)
        for key in (" ".join(words), " ".join(w for w in words if w not in _LANDMARK_WORDS)):
            if key and (found := names.get(key)) is not None:
                return found
    return None


def _far_km(lat: float, lng: float) -> float:
    return haversine_km(lat, lng, *UNION)


def _described(hit: dict) -> str:
    """Nominatim's answer in words: "TD Computer Centre on Progress Avenue in Scarborough"."""
    address = hit.get("address") or {}
    area = next((address[k] for k in ("suburb", "neighbourhood", "quarter", "city_district")
                 if address.get(k)), None)
    name = hit.get("name") or f"a {hit.get('type') or 'place'}"
    return name + (f" on {address['road']}" if address.get("road") else "") + (f" in {area}" if area else "")


def _street(name: str) -> str:
    """An OSM name pattern for a street as people say it: "King" matches "King Street West", and "Queen's
    Quay" matches "Queens Quay West"."""
    words = [w for w in re.findall(r"[A-Za-z0-9']+", name.replace("’", "'")) if w.strip("'")]
    while words and re.fullmatch(_STREET_TYPE + r"|" + _DIRECTION, words[-1], re.I):
        words.pop()
    core = " ".join(_apostrophe_optional(w) for w in words) or re.escape(name.strip())
    return rf"^{core}( {_STREET_TYPE})?( {_DIRECTION})?$"


def _named(core: str, direction: str | None) -> str:
    """The OSM name pattern for a street the renter named: with a direction, only that part of it."""
    words = " ".join(_apostrophe_optional(w) for w in re.findall(r"[A-Za-z0-9']+", core.replace("’", "'"))
                     if w.strip("'"))
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


def _apostrophe_optional(word: str) -> str:
    """A word's pattern whose apostrophe may be there or not, and may come before a final s: "Queens" and
    "Queen's" both give Queen'?s, "O'Connor" gives O'?Connor. OSM's names don't agree on it."""
    word = word.strip("'")
    parts = word.split("'")
    if len(parts) == 1 and len(word) > 2 and word[-1] in "sS":
        parts = [word[:-1], word[-1]]
    return "'?".join(re.escape(p) for p in parts)


_APOSTROPHE = re.compile(r"['’]")   # "Queen's", "Queens" and "Queen’s" are one word


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", _APOSTROPHE.sub("", text.lower()).replace("st.", "st"))


def _key(text: str) -> str:
    return " ".join(_words(text))
