"""The feature matrix: every catalogue listing as precomputed columns, built once at boot.

Ranking reads these columns and never touches a Listing, which keeps a re-rank of
2k listings well under 5 ms. A value the listing doesn't state is None (unknown).
Amenities count as evidence that something is there; a missing amenity proves
nothing, so it stays unknown.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from math import asin, cos, radians, sin, sqrt

from landline.contracts import Listing

MIN_SQFT = 150   # below this a stated size is a data error (one listing says 2 sq ft); treated as unknown
STRONG = ("call", "city")   # provenance that can exclude a listing; a listing's own text only ranks it


@dataclass(frozen=True)
class Hood:
    """A City neighbourhood (D-05): where its middle is, and which neighbourhoods touch it."""
    id: int
    name: str
    lat: float
    lng: float
    adjacent: tuple[int, ...] = ()


@dataclass
class FeatureMatrix:
    ids: list[str]
    listings: list[Listing]
    monthly_cost: list[int]
    beds: list[float]               # beds + 0.5 × dens
    sqft: list[int | None]
    lat: list[float | None]
    lng: list[float | None]
    light: list[float | None]
    transit_min: list[float | None]
    floor: list[int | None]
    unit_type: list[str | None]
    parking: list[str | None]       # included / extra / none; "extra" when only an amenity mentions it
    pets: list[str | None]
    laundry: list[str | None]       # in_suite / shared / none
    ac: list[bool | None]
    elevator: list[bool | None]
    step_free: list[bool | None]
    dishwasher: list[bool | None]
    outdoor_space: list[bool | None]
    available_from: list[date | None]
    neighbourhood: list[str | None]  # lower case
    strong: list[frozenset[str]]     # fields whose value comes from a call or the City record
    index: dict[str, int] = field(default_factory=dict)
    strong_rows: dict[str, frozenset[int]] = field(default_factory=dict)   # field → rows with such evidence
    hoods: dict[int, Hood] = field(default_factory=dict)   # the City's neighbourhoods, by id (D-05)
    _distances: dict[tuple[float, float], list[float | None]] = field(default_factory=dict, repr=False)
    # Utility columns by (feature, the preference values it depends on); filled by rank.py.
    _utilities: dict[tuple, tuple[list, list, float | None]] = field(default_factory=dict, repr=False)

    @classmethod
    def build(cls, listings: list[Listing], hoods: Iterable[Hood] = ()) -> FeatureMatrix:
        """Rows are kept in listing-id order, so a stable sort on score alone breaks ties by id.
        `hoods` lets a named area count its neighbours and surroundings (docs/SCORING.md, Location)."""
        cols: dict[str, list] = {name: [] for name in cls.__dataclass_fields__ if name not in _DERIVED}
        for x in sorted(listings, key=lambda x: x.listing_id):
            amenities = {a.strip().lower() for a in x.amenities}
            row = {
                "ids": x.listing_id,
                "listings": x,
                "monthly_cost": x.monthly_cost,
                "beds": x.beds + 0.5 * x.dens,
                "sqft": x.sqft if x.sqft and x.sqft >= MIN_SQFT else None,
                "lat": x.lat,
                "lng": x.lng,
                "light": x.light_score,
                "transit_min": x.transit_walk_min,
                "floor": x.floor,
                "unit_type": x.unit_type,
                "parking": _known(x.parking) or ("extra" if amenities & _PARKING else None),
                "pets": _known(x.pets),
                "laundry": _known(x.laundry) or ("in_suite" if amenities & _IN_SUITE else
                                                 "shared" if "laundry" in amenities else None),
                "ac": x.ac if x.ac is not None else (True if "air conditioning" in amenities else None),
                "elevator": True if "elevator" in amenities else None,
                "step_free": True if "wheelchair accessible" in amenities else None,
                "dishwasher": True if "dishwasher" in amenities else None,
                "outdoor_space": True if amenities & _OUTDOOR else None,
                "available_from": x.available_from,
                "neighbourhood": x.neighbourhood.lower() if x.neighbourhood else None,
                "strong": frozenset(k for k, p in x.provenance.items() if p.source in STRONG),
            }
            for name, value in row.items():
                cols[name].append(value)
        matrix = cls(**cols)
        matrix.hoods = {h.id: h for h in hoods}
        matrix.index = {lid: i for i, lid in enumerate(matrix.ids)}
        by_field: dict[str, set[int]] = {}
        for i, fields in enumerate(matrix.strong):
            for name in fields:
                by_field.setdefault(name, set()).add(i)
        matrix.strong_rows = {name: frozenset(rows) for name, rows in by_field.items()}
        return matrix

    def __len__(self) -> int:
        return len(self.ids)

    def named(self, areas: Iterable[str]) -> list[Hood]:
        """The neighbourhoods an area names: every word of it in the neighbourhood's name, so
        "Liberty Village" finds "Fort York-Liberty Village" and "the Annex" finds "Annex"."""
        out = []
        for area in areas:
            wanted = _words(area) - _FILLER
            if wanted:
                out += [h for h in self.hoods.values() if wanted <= _words(h.name) and h not in out]
        return out

    def distances_km(self, lat: float, lng: float) -> list[float | None]:
        """Distance from every listing to one anchor. Computed once per anchor, then cached."""
        key = (round(lat, 5), round(lng, 5))
        if key not in self._distances:
            self._distances[key] = [
                haversine_km(lat, lng, a, b) if a is not None and b is not None else None
                for a, b in zip(self.lat, self.lng, strict=True)
            ]
        return self._distances[key]


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    p1, p2 = radians(lat1), radians(lat2)
    h = sin((p2 - p1) / 2) ** 2 + cos(p1) * cos(p2) * sin(radians(lng2 - lng1) / 2) ** 2
    return 2 * 6371.0 * asin(sqrt(h))


def _known(value: str) -> str | None:
    return None if value == "unknown" else value


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower().replace("st.", "st")))


_FILLER = {"the", "area", "neighbourhood", "neighborhood", "around", "near", "in"}
_DERIVED = {"index", "strong_rows", "hoods", "_distances", "_utilities"}
_PARKING = {"parking", "secured underground parking", "underground parking"}
_IN_SUITE = {"insuite laundry", "in-suite laundry", "in suite laundry", "ensuite laundry"}
_OUTDOOR = {"balcony", "patio / deck / terrace", "terrace", "patio", "yard", "deck"}
