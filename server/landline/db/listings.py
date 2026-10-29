"""Listings and ingest runs, read side. The pipeline writes both (pipeline/stages.py).

Listings come back as the contract `Listing`, like the boot catalogue
(db/catalogue.py), so the API and the runtime read one shape.
"""
from __future__ import annotations

from datetime import datetime

from psycopg.rows import dict_row

from landline.contracts import Listing
from landline.db.base import Conn, Repo
from landline.db.rows import IngestRun

# The same columns as the boot catalogue's SELECT in db/catalogue.py.
COLUMNS = """
listing_id, source, source_id, url, first_seen, last_seen, status, address, unit_label, building_name, city,
postal_code, ST_Y(geom::geometry) as lat, ST_X(geom::geometry) as lng, geocode_precision, neighbourhood, rent,
fees, monthly_cost, beds::float, dens, baths::float, sqft, floor, unit_type, building_type, furnished,
available_from, parking, parking_cost, pets, laundry, ac, utilities_included, amenities, light_score,
transit_walk_min, red_flags, title, photos, provenance, grocery_name, grocery_walk_min, pharmacy_name,
pharmacy_walk_min
"""


def _listing(row: dict) -> Listing:
    return Listing.model_validate({k: v for k, v in row.items() if v is not None})


class Listings(Repo):
    async def get(self, listing_id: str, conn: Conn = None) -> Listing | None:
        async with self._use(conn) as c, c.cursor(row_factory=dict_row) as cur:
            await cur.execute(f"select {COLUMNS} from listings where listing_id = %s", (listing_id,))
            row = await cur.fetchone()
        return _listing(row) if row else None

    async def list_active(self, conn: Conn = None) -> list[Listing]:
        async with self._use(conn) as c, c.cursor(row_factory=dict_row) as cur:
            await cur.execute(f"select {COLUMNS} from listings where status = 'active' order by listing_id")
            return [_listing(row) for row in await cur.fetchall()]

    async def first_seen_after(self, since: datetime, conn: Conn = None) -> list[str]:
        """Active listings that appeared after `since`: "new since your last call" (O-05)."""
        async with self._use(conn) as c:
            cur = await c.execute("select listing_id from listings where status = 'active' "
                                  "and first_seen > %s order by first_seen, listing_id", (since,))
            return [r[0] for r in await cur.fetchall()]


class IngestRuns(Repo):
    async def latest(self, source: str, conn: Conn = None) -> IngestRun | None:
        return await self._one(IngestRun, "select * from ingest_runs where source = %s "
                               "order by started_at desc, run_id desc limit 1", (source,), conn)
