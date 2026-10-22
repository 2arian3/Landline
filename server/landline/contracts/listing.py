"""Listing data as the pipeline writes it and the API serves it.

Every field the renter can see has an entry in `provenance` saying where it
came from. Numbers that combine other numbers (`monthly_cost`) are computed in
code, never taken from a model. Unknown stays unknown: a missing value is None
or "unknown", never a guess.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

Source = Literal["scraped", "llm", "city", "call", "derived", "synthetic"]
UnitType = Literal["apartment", "condo", "house", "townhouse", "basement", "room", "other"]
Parking = Literal["included", "extra", "none", "unknown"]
Pets = Literal["any", "cats_only", "dogs_only", "small_only", "none", "case_by_case", "unknown"]
Laundry = Literal["in_suite", "shared", "none", "unknown"]
Precision = Literal["rooftop", "street", "postal", "none"]


class Provenance(BaseModel):
    source: Source
    quote: str | None = None   # verbatim words a value was extracted from, if a model extracted it
    at: datetime | None = None
    version: str | None = None  # extractor or parser version


class Fee(BaseModel):
    name: str                   # "parking", "locker", "utilities"
    amount: int                 # CAD
    period: Literal["monthly", "one_time"]
    mandatory: bool
    quote: str | None = None


class Listing(BaseModel):
    listing_id: str             # "<source>:<source_id>[:<unit>]"
    source: str
    source_id: str
    url: str
    first_seen: datetime
    last_seen: datetime
    status: Literal["active", "stale", "removed"] = "active"

    address: str
    unit_label: str | None = None
    building_name: str | None = None
    city: str = "Toronto"
    postal_code: str | None = None
    lat: float | None = None
    lng: float | None = None
    geocode_precision: Precision = "none"
    neighbourhood: str | None = None

    rent: int
    fees: list[Fee] = Field(default_factory=list)
    monthly_cost: int           # rent + mandatory monthly fees, computed in code

    beds: float                 # 0 = studio
    dens: int = 0
    baths: float | None = None
    sqft: int | None = None
    floor: int | None = None    # -1 basement, 0 ground
    unit_type: UnitType | None = None
    building_type: str | None = None
    furnished: bool | None = None
    available_from: date | None = None

    parking: Parking = "unknown"
    parking_cost: int | None = None
    pets: Pets = "unknown"
    laundry: Laundry = "unknown"
    ac: bool | None = None
    utilities_included: list[str] = Field(default_factory=list)
    amenities: list[str] = Field(default_factory=list)

    light_score: float | None = None
    transit_station: str | None = None
    transit_walk_min: float | None = None
    red_flags: list[str] = Field(default_factory=list)

    title: str | None = None
    photos: list[str] = Field(default_factory=list)
    provenance: dict[str, Provenance] = Field(default_factory=dict)


def monthly_cost(rent: int, fees: list[Fee]) -> int:
    """Rent plus every mandatory monthly fee. The only place this is computed."""
    return rent + sum(f.amount for f in fees if f.mandatory and f.period == "monthly")
