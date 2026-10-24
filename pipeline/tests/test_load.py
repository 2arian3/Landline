"""The load stage against Postgres, in a throwaway schema. Skips without a reachable TEST_DATABASE_URL."""
import os
import secrets
from datetime import UTC, datetime

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo

from landline.contracts.listing import Listing, Provenance
from landline.db.migrations import apply
from pipeline import stages

URL = os.environ.get("TEST_DATABASE_URL")
AT = datetime(2026, 10, 24, tzinfo=UTC)


@pytest.fixture
def schema_url():
    if not URL:
        pytest.skip("TEST_DATABASE_URL is not set")
    try:
        conn = psycopg.connect(URL, autocommit=True, connect_timeout=3)
    except psycopg.OperationalError as e:
        pytest.skip(f"no database at TEST_DATABASE_URL: {e}")
    name = f"t_{secrets.token_hex(4)}"
    with conn:
        conn.execute("create extension if not exists postgis with schema public")
        conn.execute("create extension if not exists vector with schema public")
        conn.execute(sql.SQL("create schema {}").format(sql.Identifier(name)))
        try:
            apply(conn, schema=name)
            yield make_conninfo(URL, options=f"-csearch_path={name},public")
        finally:
            conn.execute(sql.SQL("drop schema {} cascade").format(sql.Identifier(name)))


def _listing(lat, lng) -> Listing:
    scraped = Provenance(source="scraped", at=AT, version="livrent-v1")
    fields = {"rent": 2400, **({"lat": lat, "lng": lng} if lat is not None else {})}
    return Listing(listing_id="livrent:1", source="livrent", source_id="1", url="https://liv.rent/x",
                   first_seen=AT, last_seen=AT, address="10 Example Ave", rent=2400, monthly_cost=2400,
                   beds=1, lat=lat, lng=lng, geocode_precision="street" if lat is not None else "none",
                   provenance={k: scraped for k in fields})


def test_a_nulled_point_drops_its_old_coordinate_provenance(schema_url):
    stages.load(stages.RunResult("livrent", 1, [_listing(43.65, -79.39)], False, "test"), AT, schema_url)
    with psycopg.connect(schema_url) as conn:   # another stage's key, which a reload must keep
        conn.execute("""update listings set provenance = provenance || '{"pets": {"source": "llm"}}'""")
    stages.load(stages.RunResult("livrent", 1, [_listing(None, None)], False, "test"), AT, schema_url)
    with psycopg.connect(schema_url) as conn:
        geom, provenance = conn.execute("select geom, provenance from listings").fetchone()
    assert geom is None
    assert "lat" not in provenance and "lng" not in provenance
    assert provenance["rent"]["source"] == "scraped" and provenance["pets"]["source"] == "llm"
