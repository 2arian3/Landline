"""Google Maps numbers for each listing (D-10, #237), as one pipeline stage.

- D-10a: the walk to the TTC. For each active listing, a Routes API computeRouteMatrix (travelMode WALK) to
  its 3 nearest subway stations by straight line; the shortest walk sets transit_station_id and
  transit_walk_min. Today's straight-line estimate (pipeline/geo.py) stays wherever Google finds no route.
- D-10b: the nearest grocery store and pharmacy (pipeline/nearby.py), a step of the same stage, through the
  same client and cache.

`GoogleMaps` is the one client: the API key, a field mask on every request, a disk cache under
pipeline/cache/maps/ so a rerun costs nothing, and a pace that keeps the route matrix under 3,000 elements a
minute (625 a request). Without GOOGLE_MAPS_API_KEY the stage prints a skip line and changes nothing.

Provenance for what it writes: source `derived`, `via` "Google Maps walking time", and the day Google was
asked (`at`). geo --load resets the station and the walk to the estimate, so the refresh runs this right
after it; the cache makes that rerun free.

    python -m pipeline maps            # ask Google (or the cache) and print what would change
    python -m pipeline maps --load     # ... and update $DATABASE_URL
"""
from __future__ import annotations

import json
import math
import os
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

VERSION = "google-maps-v1"
ROUTES_URL = "https://routes.googleapis.com/distanceMatrix/v2:computeRouteMatrix"
MATRIX_FIELDS = "originIndex,destinationIndex,duration,distanceMeters,status,condition"
MAX_ELEMENTS = 625          # origins × destinations in one request (not TRANSIT)
ELEMENTS_PER_MIN = 3000     # the Routes API's route-matrix quota
TIMEOUT_S = 30
NEAREST = 3                 # stations asked about per listing, nearest first by straight line
WALK_VIA = "Google Maps walking time"
CACHE_DIR = Path(__file__).resolve().parent / "cache" / "maps"
SKIP = "maps: GOOGLE_MAPS_API_KEY isn't set; skipping (the straight-line estimates stay)"

LatLng = tuple[float, float]


class MapsError(RuntimeError):
    """Google refused a request (HTTP status, quota, key). The stage stops; what's cached so far is kept."""


@dataclass(frozen=True)
class Walk:
    minutes: float
    meters: int
    at: str                 # the day Google answered, YYYY-MM-DD


class Cache:
    """One JSON file of answers by key. Written atomically on `save`."""

    def __init__(self, path: Path):
        self.path = path
        self._data: dict[str, dict] = json.loads(path.read_text()) if path.exists() else {}
        self._dirty = False

    def get(self, key: str) -> dict | None:
        return self._data.get(key)

    def put(self, key: str, value: dict) -> None:
        self._data[key] = value
        self._dirty = True

    def save(self) -> None:
        if not self._dirty:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, sort_keys=True, indent=0))
        tmp.replace(self.path)
        self._dirty = False


class GoogleMaps:
    """Google Maps Platform over httpx, with the stage's cache and pace. `requests` and `elements` count what
    was billed in this run."""

    def __init__(self, api_key: str, cache_dir: Path = CACHE_DIR, http: httpx.Client | None = None,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep,
                 today: Callable[[], str] | None = None):
        self.api_key = api_key
        self.http = http or httpx.Client(timeout=TIMEOUT_S)
        self.clock, self.sleep = clock, sleep
        self.today = today or (lambda: datetime.now(UTC).date().isoformat())
        self.routes_cache = Cache(cache_dir / "routes.json")
        self.places_cache = Cache(cache_dir / "places.json")   # D-10b's searchNearby answers
        self.requests = self.elements = 0
        self._sent: deque[tuple[float, int]] = deque()       # (when, elements) in the last minute

    def close(self) -> None:
        self.save()
        self.http.close()

    def save(self) -> None:
        self.routes_cache.save()
        self.places_cache.save()

    def post(self, url: str, body: dict, field_mask: str, *, elements: int = 0) -> Any:
        """One POST with the key and a field mask, paced by route-matrix elements. Raises MapsError."""
        self._pace(elements)
        try:
            resp = self.http.post(url, json=body, headers={"X-Goog-Api-Key": self.api_key,
                                                           "X-Goog-FieldMask": field_mask})
        except httpx.HTTPError as e:
            raise MapsError(f"{url}: {e}") from e
        self.requests += 1
        self.elements += elements
        if resp.status_code != 200:
            raise MapsError(f"{url}: HTTP {resp.status_code}: {resp.text[:300]}")
        return resp.json()

    def _pace(self, elements: int) -> None:
        if not elements:
            return
        while True:
            now = self.clock()
            while self._sent and now - self._sent[0][0] >= 60:
                self._sent.popleft()
            if sum(n for _, n in self._sent) + elements <= ELEMENTS_PER_MIN:
                break
            self.sleep(60 - (now - self._sent[0][0]))
        self._sent.append((self.clock(), elements))

    def walk(self, origins: list[LatLng], destinations: list[LatLng]) -> dict[tuple[int, int], Walk | None]:
        """Walking time for every origin × destination, from the cache or the route matrix. None where Google
        finds no walking route. Only origins with a pair not yet cached are sent, in requests of at most
        MAX_ELEMENTS elements."""
        if not destinations or len(destinations) > MAX_ELEMENTS:
            raise ValueError(f"1 to {MAX_ELEMENTS} destinations, got {len(destinations)}")
        out: dict[tuple[int, int], Walk | None] = {}
        todo: list[int] = []
        for i, o in enumerate(origins):
            cached = [self.routes_cache.get(_walk_key(o, d)) for d in destinations]
            if all(c is not None for c in cached):
                out.update({(i, j): _walk(c) for j, c in enumerate(cached)})
            else:
                todo.append(i)
        per_request = max(1, MAX_ELEMENTS // len(destinations))
        for start in range(0, len(todo), per_request):
            chunk = todo[start:start + per_request]
            body = {"origins": [_waypoint(origins[i]) for i in chunk],
                    "destinations": [_waypoint(d) for d in destinations], "travelMode": "WALK"}
            elements = self.post(ROUTES_URL, body, MATRIX_FIELDS, elements=len(chunk) * len(destinations))
            at = self.today()
            got = {(e.get("originIndex", 0), e.get("destinationIndex", 0)): e for e in elements or []}
            for k, i in enumerate(chunk):
                for j, d in enumerate(destinations):
                    e = got.get((k, j))
                    value = _element(e, at) if e is not None else {"route": False, "at": at}
                    self.routes_cache.put(_walk_key(origins[i], d), value)
                    out[(i, j)] = _walk(value)
            self.save()   # a later failure keeps what this request paid for
        return out


def from_env() -> GoogleMaps | None:
    key = (os.getenv("GOOGLE_MAPS_API_KEY") or "").strip()
    return GoogleMaps(key) if key else None


def _waypoint(p: LatLng) -> dict:
    return {"waypoint": {"location": {"latLng": {"latitude": p[0], "longitude": p[1]}}}}


def _walk_key(o: LatLng, d: LatLng) -> str:
    return f"walk:{o[0]:.5f},{o[1]:.5f}>{d[0]:.5f},{d[1]:.5f}"


def _element(e: dict, at: str) -> dict:
    """A matrix element as the cache keeps it: seconds and meters, or no route."""
    if (e.get("status") or {}).get("code") or e.get("condition") != "ROUTE_EXISTS" or "duration" not in e:
        return {"route": False, "at": at}
    return {"route": True, "seconds": float(str(e["duration"]).rstrip("s")),
            "meters": int(e.get("distanceMeters", 0)), "at": at}


def _walk(value: dict) -> Walk | None:
    if not value.get("route"):
        return None
    return Walk(round(value["seconds"] / 60, 1), value["meters"], value["at"])


# ── D-10a: the walk to the TTC ─────────────────────────────────────────────

def nearest_stations(point: LatLng, stations: list[dict], k: int = NEAREST) -> list[dict]:
    """The k stations closest to `point` in a straight line, nearest first (ties by id)."""
    return sorted(stations, key=lambda s: (_km(point, (s["lat"], s["lng"])), s["id"]))[:k]


def station_walks(client: GoogleMaps, listings: list[dict], stations: list[dict],
                  k: int = NEAREST) -> dict[str, dict]:
    """For each listing ({listing_id, lat, lng}), the shortest Google walk among its k nearest stations:
    {listing_id: {station_id, station, minutes, at}}. Listings that share the same nearby stations go in one
    request, so every element billed is one we use. A listing with no walking route to any is left out."""
    groups: dict[tuple[str, ...], list[dict]] = {}
    by_id: dict[str, dict] = {}
    for x in listings:
        near = nearest_stations((x["lat"], x["lng"]), stations, k)
        by_id.update({s["id"]: s for s in near})
        groups.setdefault(tuple(s["id"] for s in near), []).append(x)
    out: dict[str, dict] = {}
    for ids, members in sorted(groups.items()):
        near = [by_id[i] for i in ids]
        walks = client.walk([(x["lat"], x["lng"]) for x in members], [(s["lat"], s["lng"]) for s in near])
        for i, x in enumerate(members):
            options = [(w.minutes, s["id"], s, w) for j, s in enumerate(near) if (w := walks.get((i, j)))]
            if options:
                minutes, _, s, w = min(options)
                out[x["listing_id"]] = {"station_id": s["id"], "station": s["name"], "minutes": minutes,
                                        "at": w.at}
    return out


ACTIVE = """
select listing_id, ST_Y(geom::geometry) as lat, ST_X(geom::geometry) as lng
from listings where status = 'active' and geom is not null order by listing_id
"""

SET_WALK = """
update listings set transit_station_id = %(station_id)s, transit_walk_min = %(minutes)s,
  provenance = provenance || jsonb_build_object(
    'transit_walk_min', jsonb_build_object('source', 'derived', 'via', %(via)s::text, 'at', %(at)s::text,
                                           'version', %(version)s::text),
    'transit_station', jsonb_build_object('source', 'derived', 'via', %(via)s::text, 'at', %(at)s::text,
                                          'version', %(version)s::text))
where listing_id = %(listing_id)s
"""


def active_listings(conn) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(ACTIVE)
        return [{"listing_id": r[0], "lat": r[1], "lng": r[2]} for r in cur.fetchall()]


def load_walks(conn, walks: dict[str, dict]) -> int:
    """Writes each listing's Google walk and its provenance in one transaction. Returns the rows updated."""
    rows = [{**w, "listing_id": lid, "via": WALK_VIA, "version": VERSION} for lid, w in sorted(walks.items())]
    with conn.transaction(), conn.cursor() as cur:
        cur.executemany(SET_WALK, rows)
    return len(rows)


def run(conn, client: GoogleMaps, stations: list[dict], load: bool = False, limit: int = 0) -> dict:
    """The stage: every step over the active listings, in order, each writing in its own transaction (so a
    failure in a later step keeps an earlier one's writes; its answers are cached either way). Returns counts
    for the report."""
    from pipeline import nearby  # D-10b; imported here because it uses this module's VERSION

    listings = active_listings(conn)
    conn.commit()   # end the read, so each step's write below commits on its own
    if limit:
        listings = listings[:limit]
    walks = station_walks(client, listings, stations)
    counts: dict[str, Any] = {"listings": len(listings), "walks": len(walks), "updated": 0}
    if load:
        counts["updated"] = load_walks(conn, walks)
    located = [(x["listing_id"], x["lat"], x["lng"]) for x in listings]
    counts["nearby"] = nearby.run(conn, client, located, load)
    counts.update(requests=client.requests, elements=client.elements)
    return counts


def report(c: dict) -> str:
    from pipeline import nearby

    return (f"maps: {c['listings']} active listings, {c['walks']} with a Google walk to the TTC, "
            f"{c['updated']} updated; {c['requests']} requests, {c['elements']} route-matrix elements billed "
            f"(the rest came from the cache)\n{nearby.report(c['nearby'])}")


def _km(a: LatLng, b: LatLng) -> float:
    """Great-circle distance in km."""
    la1, lo1, la2, lo2 = map(math.radians, (*a, *b))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(h))
