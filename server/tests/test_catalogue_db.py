"""The boot catalogue from Postgres (db/catalogue.py) loads the nearest store and pharmacy (D-10b, X-04's
columns), and the read API serves them. Its SELECT used to leave the four columns out, so /api/listings/{id}
said null for listings that had them. Runs in a throwaway schema (conftest.py); skips without
TEST_DATABASE_URL."""
import os

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.conninfo import make_conninfo

from landline.app import create_app
from landline.config import Settings
from landline.db.catalogue import Catalogue

# livrent:166012, 798 Ossington, as production has it.
STORES = ("Pitch Vision", 1.6, "Guardian Bloorcourt Pharmacy", 4.9)
FIELDS = ("grocery_name", "grocery_walk_min", "pharmacy_name", "pharmacy_walk_min")


@pytest.fixture
def url(schema) -> str:
    """The test schema's connection string, with one listing that has a store and pharmacy and one without."""
    dsn = make_conninfo(os.environ["TEST_DATABASE_URL"], options=f"-csearch_path={schema[0]},public")
    with psycopg.connect(dsn) as conn:
        conn.execute("truncate listings cascade")
        for lid, stores in (("t:stores", STORES), ("t:none", (None, None, None, None))):
            conn.execute(
                "insert into listings (listing_id, source, source_id, url, first_seen, last_seen, status, "
                "address, rent, monthly_cost, beds, geom, grocery_name, grocery_walk_min, pharmacy_name, "
                "pharmacy_walk_min) values (%s, 't', %s, 'https://example.com/x', now(), now(), 'active', "
                "'798 Ossington Ave', 2400, 2400, 1, "
                "ST_SetSRID(ST_MakePoint(-79.42, 43.66), 4326)::geography, %s, %s, %s, %s)",
                (lid, lid, *stores))
    return dsn


def _stores(x) -> tuple:
    return x.grocery_name, x.grocery_walk_min, x.pharmacy_name, x.pharmacy_walk_min


def test_the_catalogue_loads_the_nearest_store_and_pharmacy(url):
    catalogue = Catalogue.load(url)
    assert catalogue.origin == "postgres"
    assert _stores(catalogue.by_id["t:stores"]) == pytest.approx(STORES)   # walk minutes are reals
    assert _stores(catalogue.by_id["t:none"]) == (None, None, None, None)


def test_the_read_api_serves_them(url):
    settings = Settings(database_url=None, web_url="http://localhost:3000", transport="stub")
    with TestClient(create_app(settings, Catalogue.load(url))) as c:
        body = c.get("/api/listings/t:stores").json()
        assert tuple(body[f] for f in FIELDS) == pytest.approx(STORES)
        assert all(c.get("/api/listings/t:none").json()[f] is None for f in FIELDS)


async def test_the_listings_repository_reads_them_too(db, url):
    found = await db.listings.get("t:stores")
    assert _stores(found) == pytest.approx(STORES)
    assert _stores(await db.listings.get("t:none")) == (None, None, None, None)
