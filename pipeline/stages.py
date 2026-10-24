"""Pipeline stages: fetch → normalize → (load) → report.

Each stage writes JSONL under data/stages/<stage>/ so any stage can be rerun
alone. Raw pages are kept under data/raw/<source>/<date>/ (gitignored).
"""
from __future__ import annotations

import json
import os
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from landline.contracts.listing import Listing
from pipeline.http import Blocked, Disallowed, PoliteClient
from pipeline.sources import livrent

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"


@dataclass
class RunResult:
    source: str
    pages: int
    listings: list[Listing]
    complete: bool          # reached the end of the results, so "not seen" means gone
    stopped_because: str
    out_path: Path | None = None


def fetch_livrent(client: PoliteClient, max_pages: int, seen_at: datetime) -> RunResult:
    day = seen_at.strftime("%Y-%m-%d")
    raw_dir = DATA / "raw" / livrent.SOURCE / day
    raw_dir.mkdir(parents=True, exist_ok=True)
    by_id: dict[str, Listing] = {}
    pages = 0
    reason = f"hit --max-pages {max_pages}"
    complete = False
    for page in range(1, max_pages + 1):
        url = livrent.CITY_PAGE.format(page=page)
        try:
            html = client.get(url)
        except (Blocked, Disallowed) as exc:
            reason = f"stopped: {exc}"
            break
        pages += 1
        (raw_dir / f"page-{page:03d}.html").write_text(html, encoding="utf-8")
        urls = livrent.building_urls(html)
        new = 0
        for raw in livrent.raw_listings(html):
            listing = livrent.to_listing(raw, seen_at, urls)
            if listing and listing.listing_id not in by_id:
                by_id[listing.listing_id] = listing
                new += 1
        print(f"  page {page}: {new} new, {len(by_id)} total")
        if new == 0:
            reason = f"page {page} had no new listings"
            complete = True
            break
    return RunResult(livrent.SOURCE, pages, list(by_id.values()), complete, reason)


def write_jsonl(result: RunResult, seen_at: datetime) -> Path:
    out = DATA / "stages" / "normalize" / f"{result.source}-{seen_at:%Y%m%dT%H%M}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        for listing in result.listings:
            fh.write(listing.model_dump_json() + "\n")
    result.out_path = out
    return out


def promote_latest(result: RunResult) -> Path:
    """Point <source>-latest.jsonl at this run. Only called after the run passed its checks."""
    assert result.out_path is not None
    latest = result.out_path.parent / f"{result.source}-latest.jsonl"
    latest.write_text(result.out_path.read_text(encoding="utf-8"), encoding="utf-8")
    return latest


def read_jsonl(path: Path) -> list[Listing]:
    return [Listing.model_validate_json(line) for line in path.read_text().splitlines() if line.strip()]


def report(listings: list[Listing]) -> str:
    """Coverage per field, so every run shows what we actually know."""
    n = len(listings)
    if not n:
        return "no listings"
    fields = ["lat", "postal_code", "rent", "beds", "baths", "sqft", "unit_type", "furnished",
              "photos", "utilities_included", "amenities", "floor", "pets", "parking"]
    lines = [f"{n} listings"]
    for f in fields:
        known = sum(1 for x in listings if getattr(x, f) not in (None, [], "unknown"))
        lines.append(f"  {f:<20} {known:>5}  {known / n:6.1%}")
    types = Counter(x.unit_type for x in listings)
    beds = Counter(x.beds for x in listings)
    rents = sorted(x.rent for x in listings)
    lines.append(f"  unit types: {dict(types.most_common())}")
    lines.append(f"  beds: {dict(sorted(beds.items()))}")
    lines.append(f"  rent: min {rents[0]}  median {rents[n // 2]}  max {rents[-1]}")
    flipped = sum(1 for x in listings if (p := x.provenance.get("lng")) and p.source == "derived")
    lines.append(f"  longitude sign flipped: {flipped}")
    return "\n".join(lines)


UPSERT = """
insert into listings (listing_id, source, source_id, url, first_seen, last_seen, status, address,
  unit_label, building_name, city, postal_code, geom, geocode_precision, rent, fees, monthly_cost, beds,
  dens, baths, sqft, floor, unit_type, building_type, furnished, utilities_included, amenities, photos,
  provenance, raw)
values (%(listing_id)s, %(source)s, %(source_id)s, %(url)s, %(first_seen)s, %(last_seen)s, 'active',
  %(address)s, %(unit_label)s, %(building_name)s, %(city)s, %(postal_code)s,
  case when %(lat)s::float8 is null then null
       else ST_SetSRID(ST_MakePoint(%(lng)s::float8, %(lat)s::float8), 4326)::geography end,
  %(geocode_precision)s, %(rent)s, %(fees)s, %(monthly_cost)s, %(beds)s, %(dens)s, %(baths)s, %(sqft)s,
  %(floor)s, %(unit_type)s, %(building_type)s, %(furnished)s, %(utilities_included)s, %(amenities)s,
  %(photos)s, %(provenance)s, '{}'::jsonb)
on conflict (listing_id) do update set
  url = excluded.url, last_seen = excluded.last_seen, status = 'active', address = excluded.address,
  building_name = excluded.building_name, postal_code = excluded.postal_code, geom = excluded.geom,
  geocode_precision = excluded.geocode_precision, rent = excluded.rent, monthly_cost = excluded.monthly_cost,
  beds = excluded.beds, baths = excluded.baths, sqft = excluded.sqft, unit_type = excluded.unit_type,
  building_type = excluded.building_type, furnished = excluded.furnished,
  utilities_included = excluded.utilities_included, amenities = excluded.amenities,
  photos = excluded.photos, provenance = listings.provenance || excluded.provenance
returning (xmax = 0) as inserted
"""


def load(result: RunResult, started: datetime, database_url: str | None = None) -> dict:
    """Upsert into Postgres and record the run. Marks unseen rows stale only after a complete run."""
    import psycopg
    from psycopg.types.json import Jsonb

    url = database_url or os.environ["DATABASE_URL"]
    stats = {"inserted": 0, "updated": 0, "marked_stale": 0}
    with psycopg.connect(url) as conn:
        run_id = conn.execute(
            "insert into ingest_runs (source, started_at) values (%s, %s) returning run_id",
            (result.source, started),
        ).fetchone()[0]
        conn.commit()
        try:
            with conn.transaction():
                for listing in result.listings:
                    row = listing.model_dump(mode="json")
                    row["first_seen"] = listing.first_seen
                    row["last_seen"] = listing.last_seen
                    for key in ("fees", "photos", "provenance"):
                        row[key] = Jsonb(row[key])
                    inserted = conn.execute(UPSERT, row).fetchone()[0]
                    stats["inserted" if inserted else "updated"] += 1
                if result.complete:
                    stats["marked_stale"] = conn.execute(
                        "update listings set status = 'stale' where source = %s and status = 'active' "
                        "and last_seen < %s",
                        (result.source, started),
                    ).rowcount
            status = "ok" if result.complete else "partial"
            conn.execute(
                "update ingest_runs set finished_at = now(), status = %s, pages = %s, fetched = %s, "
                "inserted = %s, updated = %s, marked_stale = %s, error = %s where run_id = %s",
                (status, result.pages, len(result.listings), stats["inserted"], stats["updated"],
                 stats["marked_stale"], None if result.complete else result.stopped_because, run_id),
            )
            conn.commit()
        except Exception as exc:
            conn.rollback()
            conn.execute(
                "update ingest_runs set finished_at = now(), status = 'failed', error = %s where run_id = %s",
                (str(exc)[:500], run_id),
            )
            conn.commit()
            raise
    return stats


FIXTURE_FIELDS = ("listing_id", "source", "url", "address", "building_name", "postal_code", "lat", "lng",
                  "rent", "monthly_cost", "beds", "baths", "sqft", "unit_type", "building_type", "furnished",
                  "utilities_included", "amenities")


def export_fixture(listings: list[Listing], path: Path, limit: int) -> None:
    """A small, geocoded sample in the API's catalogue shape, for UI work offline."""
    sample = sorted((x for x in listings if x.lat is not None), key=lambda x: x.listing_id)[:limit]
    rows = []
    for x in sample:
        row = {k: getattr(x, k) for k in FIXTURE_FIELDS}
        row["photos"] = x.photos[:4]
        rows.append(row)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, separators=(",", ":")), encoding="utf-8")


def now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)
