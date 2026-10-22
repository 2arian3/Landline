"""The listing catalogue, held in memory for the hot path.

Loaded once at boot from Postgres when DATABASE_URL is set, otherwise from the
pipeline's latest JSONL, otherwise from the web fixture, so the API runs on a
laptop with no database at all.
"""
from __future__ import annotations

import json
from pathlib import Path

from landline.config import ROOT
from landline.contracts.listing import Listing

LATEST_JSONL = ROOT / "data" / "stages" / "normalize" / "livrent-latest.jsonl"
FIXTURE = ROOT / "web" / "lib" / "fixtures" / "listings.json"

SELECT = """
select listing_id, source, source_id, url, first_seen, last_seen, status, address, unit_label,
  building_name, city, postal_code, ST_Y(geom::geometry) as lat, ST_X(geom::geometry) as lng,
  geocode_precision, neighbourhood, rent, fees, monthly_cost, beds::float, dens, baths::float, sqft, floor,
  unit_type, building_type, furnished, available_from, parking, parking_cost, pets, laundry, ac,
  utilities_included, amenities, light_score, transit_walk_min, red_flags, title, photos, provenance
from listings where status = 'active'
"""


class Catalogue:
    def __init__(self, listings: list[Listing], origin: str):
        self.listings = listings
        self.origin = origin
        self.by_id = {x.listing_id: x for x in listings}

    @classmethod
    def load(cls, database_url: str | None) -> Catalogue:
        if database_url:
            return cls(_from_db(database_url), "postgres")
        if LATEST_JSONL.exists():
            lines = LATEST_JSONL.read_text().splitlines()
            return cls([Listing.model_validate_json(line) for line in lines if line], LATEST_JSONL.name)
        return cls(_from_fixture(FIXTURE), "fixture")


def _from_db(url: str) -> list[Listing]:
    import psycopg
    from psycopg.rows import dict_row

    with psycopg.connect(url, row_factory=dict_row) as conn:
        rows = conn.execute(SELECT).fetchall()
    return [Listing.model_validate({k: v for k, v in row.items() if v is not None}) for row in rows]


def _from_fixture(path: Path) -> list[Listing]:
    from datetime import UTC, datetime

    if not path.exists():
        return []
    now = datetime.now(UTC)
    out = []
    for row in json.loads(path.read_text()):
        out.append(Listing.model_validate({
            **row, "source_id": row["listing_id"].split(":", 1)[-1], "first_seen": now, "last_seen": now,
        }))
    return out
