"""The nearest grocery store and pharmacy for each listing, with the walk to each (D-10b).

A step of the `maps` stage (pipeline/maps.py, D-10a), on its Google client. For each located active listing,
Places API (New) searchNearby finds the nearest grocery store and the nearest pharmacy (a Shoppers Drug Mart
counts) within 1.5 km, ranked by distance; one Routes matrix call then gives the walk to both. The names and
minutes go in listings.grocery_* and pharmacy_* (X-04), with provenance `derived`, "Google Maps" and the fetch
date. Nothing within 1.5 km, or no walking route to it, leaves that store's two fields null: never a made-up
value. Every answer is cached, so a rerun sends nothing.
"""
from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date
from typing import Any

SEARCH_NEARBY = "https://places.googleapis.com/v1/places:searchNearby"
FIELD_MASK = "places.id,places.displayName,places.location"   # only what we read (and are billed for)
RADIUS_M = 1500.0
KINDS = {"grocery": ("grocery_store", "supermarket"), "pharmacy": ("pharmacy", "drugstore")}
FIELDS = ("grocery_name", "grocery_walk_min", "pharmacy_name", "pharmacy_walk_min")
VERSION = "Google Maps"


@dataclass(frozen=True)
class Place:
    name: str
    lat: float
    lng: float
    at: str           # the fetch date, YYYY-MM-DD


@dataclass(frozen=True)
class Nearby:
    """One kind of store for one listing: what it is and how long the walk is."""
    name: str
    minutes: float
    at: str


def nearest(client: Any, kind: str, lat: float, lng: float,
            today: Callable[[], date] = date.today) -> Place | None:
    """The nearest place of this kind within RADIUS_M, from the cache or one searchNearby request."""
    key = f"{kind}:{lat:.5f},{lng:.5f}"
    hit = client.places_cache.get(key)
    if hit is None:
        body = {"includedTypes": list(KINDS[kind]), "maxResultCount": 1, "rankPreference": "DISTANCE",
                "locationRestriction": {"circle": {"center": {"latitude": lat, "longitude": lng},
                                                   "radius": RADIUS_M}}}
        hit = {"at": today().isoformat(), "place": _first(client.post(SEARCH_NEARBY, body, FIELD_MASK))}
        client.places_cache.put(key, hit)
    found = hit.get("place")
    return Place(found["name"], found["lat"], found["lng"], hit["at"]) if found else None


def _first(response: Any) -> dict | None:
    """The first place in a searchNearby answer, or None when Google found nothing in the circle."""
    for place in (response or {}).get("places") or []:
        name, at = (place.get("displayName") or {}).get("text"), place.get("location") or {}
        if name and "latitude" in at and "longitude" in at:
            return {"name": name, "lat": at["latitude"], "lng": at["longitude"]}
    return None


def find(client: Any, lat: float, lng: float,
         today: Callable[[], date] = date.today) -> dict[str, Nearby | None]:
    """The nearest grocery store and pharmacy, each with its walk; one walking matrix for both."""
    places = {kind: nearest(client, kind, lat, lng, today) for kind in KINDS}
    found = [(kind, p) for kind, p in places.items() if p is not None]
    walks = client.walk([(lat, lng)], [(p.lat, p.lng) for _, p in found]) if found else {}
    out: dict[str, Nearby | None] = dict.fromkeys(KINDS)
    for j, (kind, p) in enumerate(found):
        walk = walks.get((0, j))
        if walk is not None:   # no walking route: left out, like nothing close
            out[kind] = Nearby(p.name, round(walk.minutes, 1), walk.at)
    return out


UPDATE = """
update listings set grocery_name = %(grocery_name)s, grocery_walk_min = %(grocery_walk_min)s,
  pharmacy_name = %(pharmacy_name)s, pharmacy_walk_min = %(pharmacy_walk_min)s,
  provenance = (provenance - 'grocery_name' - 'grocery_walk_min' - 'pharmacy_name' - 'pharmacy_walk_min')
               || %(provenance)s::jsonb
where listing_id = %(listing_id)s
"""


def row(listing_id: str, near: dict[str, Nearby | None]) -> dict:
    """The update for one listing: the four fields, and provenance for the ones that have a value."""
    values: dict[str, Any] = {"listing_id": listing_id}
    provenance: dict[str, dict] = {}
    for kind, n in near.items():
        values[f"{kind}_name"] = n.name if n else None
        values[f"{kind}_walk_min"] = n.minutes if n else None
        if n is not None:
            for field in (f"{kind}_name", f"{kind}_walk_min"):
                provenance[field] = {"source": "derived", "at": n.at, "version": VERSION}
    values["provenance"] = json.dumps(provenance)
    return values


def run(conn: Any, client: Any, listings: Iterable[tuple[str, float, float]], load: bool,
        today: Callable[[], date] = date.today) -> dict:
    """The `maps` stage's D-10b step: `listings` are (listing_id, lat, lng) of the located active ones.
    With `load`, each listing's four fields are written (and cleared where nothing is close). Returns counts
    for the stage's report."""
    counts = {"listings": 0, "grocery": 0, "pharmacy": 0, "neither": 0}
    for listing_id, lat, lng in listings:
        near = find(client, lat, lng, today)
        counts["listings"] += 1
        for kind, n in near.items():
            counts[kind] += n is not None
        counts["neither"] += all(n is None for n in near.values())
        if load:
            conn.execute(UPDATE, row(listing_id, near))
    client.places_cache.save()
    if load:
        conn.commit()
    return counts


def report(c: dict) -> str:
    n = c["listings"] or 1
    return (f"nearby: {c['listings']} listings; a grocery store within 1.5 km for {c['grocery']} "
            f"({c['grocery'] / n:.0%}), a pharmacy for {c['pharmacy']} ({c['pharmacy'] / n:.0%}), "
            f"neither for {c['neither']}")
