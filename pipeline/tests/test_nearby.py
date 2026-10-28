"""The nearest grocery store and pharmacy (D-10b), with no network: a stand-in for D-10a's GoogleMaps client
serves searchNearby answers from fixtures/maps/ (shaped like Google's documented responses; re-record them
from a live run once the key is in) and fixed walks. The load test runs against Postgres in a throwaway
schema and skips without a reachable TEST_DATABASE_URL."""
import json
import os
import secrets
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo

from landline.db.migrations import apply
from pipeline import nearby

FIXTURES = Path(__file__).parent / "fixtures" / "maps"
URL = os.environ.get("TEST_DATABASE_URL")
TODAY = date(2026, 10, 28)
HOME = (43.65300, -79.41100)


def _recorded(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text())


class FakeCache:
    def __init__(self):
        self.data, self.saved = {}, 0

    def get(self, key):
        return self.data.get(key)

    def put(self, key, value):
        self.data[key] = value

    def save(self):
        self.saved += 1


@dataclass(frozen=True)
class FakeWalk:
    minutes: float
    meters: int
    at: str


class FakeMaps:
    """pipeline.maps.GoogleMaps's post / walk / places_cache, as D-10a defines them."""

    def __init__(self, grocery="places_grocery", pharmacy="places_pharmacy", minutes=None):
        self.answers = {"grocery": _recorded(grocery), "pharmacy": _recorded(pharmacy)}
        self.minutes = minutes if minutes is not None else {"No Frills": 6.32, "Shoppers Drug Mart": 4.0}
        self.places_cache, self.posts, self.walks = FakeCache(), [], []

    def post(self, url, body, field_mask):
        self.posts.append((url, body, field_mask))
        return self.answers["grocery" if "grocery_store" in body["includedTypes"] else "pharmacy"]

    def walk(self, origins, destinations):
        self.walks.append((origins, destinations))
        names = {(p["location"]["latitude"], p["location"]["longitude"]): p["displayName"]["text"]
                 for a in self.answers.values() for p in a.get("places", [])}
        out = {}
        for j, dest in enumerate(destinations):
            m = self.minutes.get(names[dest])
            out[(0, j)] = FakeWalk(m, int(m * 80), "2026-10-28") if m is not None else None
        return out


def test_the_nearest_store_and_pharmacy_come_with_their_walks():
    maps = FakeMaps()
    near = nearby.find(maps, *HOME, today=lambda: TODAY)
    assert near == {"grocery": nearby.Nearby("No Frills", 6.3, "2026-10-28"),
                    "pharmacy": nearby.Nearby("Shoppers Drug Mart", 4.0, "2026-10-28")}
    (url, body, mask), _ = maps.posts
    assert url == nearby.SEARCH_NEARBY and mask == "places.id,places.displayName,places.location"
    assert body["includedTypes"] == ["grocery_store", "supermarket"] and body["maxResultCount"] == 1
    assert body["rankPreference"] == "DISTANCE"
    assert body["locationRestriction"]["circle"] == {"center": {"latitude": 43.653, "longitude": -79.411},
                                                     "radius": 1500.0}
    assert maps.posts[1][1]["includedTypes"] == ["pharmacy", "drugstore"]
    [(origins, destinations)] = maps.walks   # one walking matrix for both
    assert origins == [HOME] and len(destinations) == 2


def test_nothing_within_reach_stays_null_and_nothing_is_walked_to():
    maps = FakeMaps(pharmacy="places_none")
    near = nearby.find(maps, *HOME, today=lambda: TODAY)
    assert near["pharmacy"] is None and near["grocery"].name == "No Frills"
    assert len(maps.walks[0][1]) == 1
    empty = FakeMaps(grocery="places_none", pharmacy="places_none")
    assert nearby.find(empty, *HOME, today=lambda: TODAY) == {"grocery": None, "pharmacy": None}
    assert empty.walks == []


def test_a_store_with_no_walking_route_is_left_out():
    maps = FakeMaps(minutes={"No Frills": None, "Shoppers Drug Mart": 4.0})
    near = nearby.find(maps, *HOME, today=lambda: TODAY)
    assert near["grocery"] is None and near["pharmacy"].minutes == 4.0


def test_a_rerun_asks_google_nothing_new():
    maps = FakeMaps()
    nearby.find(maps, *HOME, today=lambda: TODAY)
    nearby.find(maps, *HOME, today=lambda: TODAY)
    assert len(maps.posts) == 2   # one per kind, the first time only
    assert maps.places_cache.get("grocery:43.65300,-79.41100")["at"] == "2026-10-28"


def test_provenance_goes_only_on_the_values_that_were_found():
    near = {"grocery": nearby.Nearby("No Frills", 6.3, "2026-10-28"), "pharmacy": None}
    r = nearby.row("t:1", near)
    assert (r["grocery_name"], r["grocery_walk_min"], r["pharmacy_name"], r["pharmacy_walk_min"]) == (
        "No Frills", 6.3, None, None)
    prov = json.loads(r["provenance"])
    assert set(prov) == {"grocery_name", "grocery_walk_min"}
    assert prov["grocery_name"] == {"source": "derived", "via": "Google Maps", "at": "2026-10-28",
                                    "version": "google-maps-v1"}


@pytest.fixture
def conn():
    if not URL:
        pytest.skip("TEST_DATABASE_URL is not set")
    try:
        admin = psycopg.connect(URL, autocommit=True, connect_timeout=3)
    except psycopg.OperationalError as e:
        pytest.skip(f"no database at TEST_DATABASE_URL: {e}")
    name = f"t_{secrets.token_hex(4)}"
    with admin:
        admin.execute("create extension if not exists postgis with schema public")
        admin.execute("create extension if not exists vector with schema public")
        admin.execute(sql.SQL("create schema {}").format(sql.Identifier(name)))
        try:
            apply(admin, schema=name)
            with psycopg.connect(make_conninfo(URL, options=f"-csearch_path={name},public")) as c:
                for lid in ("t:near", "t:alone"):
                    c.execute("insert into listings (listing_id, source, source_id, url, first_seen, "
                              "last_seen, status, address, rent, monthly_cost, beds, provenance) values "
                              "(%s, 't', %s, 'u', now(), now(), 'active', '1 Map St', 2000, 2000, 1, %s)",
                              (lid, lid, json.dumps({"rent": {"source": "scraped"}})))
                c.commit()
                yield c
        finally:
            admin.execute(sql.SQL("drop schema {} cascade").format(sql.Identifier(name)))


def _stores(conn, lid):
    cur = conn.execute("select grocery_name, grocery_walk_min, pharmacy_name, pharmacy_walk_min, provenance "
                       "from listings where listing_id = %s", (lid,))
    return cur.fetchone()


def test_the_step_writes_both_stores_and_clears_them_when_none_is_close(conn):
    counts = nearby.run(conn, FakeMaps(), [("t:near", *HOME)], load=True, today=lambda: TODAY)
    assert counts == {"listings": 1, "grocery": 1, "pharmacy": 1, "neither": 0}
    gname, gmin, pname, pmin, prov = _stores(conn, "t:near")
    assert (gname, gmin, pname, pmin) == ("No Frills", 6.3, "Shoppers Drug Mart", 4.0)
    assert prov["pharmacy_walk_min"] == {"source": "derived", "via": "Google Maps", "at": "2026-10-28",
                                         "version": "google-maps-v1"}
    assert prov["rent"] == {"source": "scraped"}   # other fields' provenance is kept
    # A later run that finds nothing close clears the four fields and their provenance.
    empty = FakeMaps(grocery="places_none", pharmacy="places_none")
    counts = nearby.run(conn, empty, [("t:near", *HOME), ("t:alone", 43.7, -79.3)], load=True,
                        today=lambda: TODAY)
    assert counts["neither"] == 2 and empty.places_cache.saved == 1
    assert _stores(conn, "t:near")[:4] == (None, None, None, None)
    assert set(_stores(conn, "t:near")[4]) == {"rent"}
    assert nearby.report(counts).startswith("nearby: 2 listings; a grocery store within 1.5 km for 0")
