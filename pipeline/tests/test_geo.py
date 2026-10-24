"""Neighbourhoods and stations (D-05) against Postgres, in a throwaway schema.
Skips without a reachable TEST_DATABASE_URL."""
import json
import os
import secrets

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo

from landline.db.migrations import apply
from pipeline import geo

URL = os.environ.get("TEST_DATABASE_URL")


def _square(lng: float, lat: float, size: float = 0.01) -> str:
    ring = [[lng, lat], [lng + size, lat], [lng + size, lat + size], [lng, lat + size], [lng, lat]]
    return json.dumps({"type": "Polygon", "coordinates": [ring]})


# Two neighbourhoods side by side, one far away.
HOODS = [{"id": 1, "name": "Fort York-Liberty Village", "geometry": _square(-79.42, 43.63)},
         {"id": 2, "name": "Niagara", "geometry": _square(-79.41, 43.63)},
         {"id": 3, "name": "Rouge", "geometry": _square(-79.15, 43.80)}]
STATIONS = [{"id": "osm:node/1", "name": "Exhibition", "lat": 43.635, "lng": -79.415},
            {"id": "osm:node/2", "name": "Scarborough Centre", "lat": 43.775, "lng": -79.257}]


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
                for lid, lat, lng in (("t:in", 43.635, -79.415), ("t:next", 43.635, -79.405),
                                      ("t:far", 43.700, -79.300), ("t:nowhere", None, None)):
                    point = (f"ST_SetSRID(ST_MakePoint({lng}, {lat}), 4326)::geography" if lat else "null")
                    c.execute("insert into listings (listing_id, source, source_id, url, first_seen, "
                              "last_seen, status, address, rent, monthly_cost, beds, geom) values "
                              "(%s, 't', %s, 'u', now(), now(), 'active', '1 Geo St', 2000, 2000, 1, "
                              f"{point})",
                              (lid, lid))
                c.commit()
                yield c
        finally:
            admin.execute(sql.SQL("drop schema {} cascade").format(sql.Identifier(name)))


def _listing(conn, lid: str) -> dict:
    cur = conn.execute("select l.neighbourhood, l.neighbourhood_id, s.name as station, l.transit_walk_min, "
                       "l.provenance from listings l left join stations s on s.id = l.transit_station_id "
                       "where l.listing_id = %s", (lid,))
    return dict(zip([d.name for d in cur.description], cur.fetchone(), strict=True))


def test_listings_get_their_neighbourhood_and_nearest_station(conn):
    coverage = geo.load(conn, HOODS, STATIONS)
    assert (coverage["neighbourhoods"], coverage["stations"], coverage["located"]) == (3, 2, 3)
    assert coverage["neighbourhood"] == 2     # t:far is outside all three: it keeps none rather than a guess
    inside = _listing(conn, "t:in")
    assert (inside["neighbourhood"], inside["neighbourhood_id"], inside["station"]) == (
        "Fort York-Liberty Village", 1, "Exhibition")
    assert inside["transit_walk_min"] == 0.0 and inside["provenance"]["neighbourhood"]["source"] == "city"
    nextdoor = _listing(conn, "t:next")
    assert nextdoor["neighbourhood"] == "Niagara"
    # about 805 m east of the station: × 1.3 at 80 m a minute is about 13 minutes
    assert 12.5 < nextdoor["transit_walk_min"] < 13.5
    assert _listing(conn, "t:far")["neighbourhood"] is None
    assert _listing(conn, "t:nowhere")["station"] is None


def test_each_neighbourhood_gets_its_median_rent_by_bedrooms(conn):
    # Fort York-Liberty Village: three 1-beds (t:in at 2000, plus these two) and one 2-bed.
    for lid, cost, beds, lng in (("t:cheap", 1800, 1, -79.414), ("t:dear", 2600, 1, -79.416),
                                 ("t:two", 3000, 2, -79.413)):
        conn.execute("insert into listings (listing_id, source, source_id, url, first_seen, last_seen, "
                     "status, address, rent, monthly_cost, beds, geom) values (%s, 't', %s, 'u', now(), "
                     "now(), 'active', '1 Geo St', %s, %s, %s, "
                     "ST_SetSRID(ST_MakePoint(%s, 43.635), 4326)::geography)",
                     (lid, lid, cost, cost, beds, lng))
    conn.commit()
    coverage = geo.load(conn, HOODS, STATIONS, median_min=3)
    medians = dict(conn.execute("select id, median_rent from neighbourhoods").fetchall())
    assert medians == {1: {"1": 2000}, 2: {}, 3: {}}   # too few 2-beds, and Niagara has one listing
    ratios = dict(conn.execute("select listing_id, price_vs_median from listings").fetchall())
    assert ratios == {"t:in": 1.0, "t:cheap": pytest.approx(0.9), "t:dear": pytest.approx(1.3),
                      "t:two": None, "t:next": None, "t:far": None, "t:nowhere": None}
    assert (coverage["with_median"], coverage["price_vs_median"]) == (1, 3)
    assert _listing(conn, "t:cheap")["provenance"]["price_vs_median"]["source"] == "derived"
    # The 1-bed that sets the median leaves: the group is too small now, and nobody keeps a stale ratio.
    conn.execute("update listings set status = 'removed' where listing_id = 't:dear'")
    conn.commit()
    geo.load(conn, HOODS, STATIONS, median_min=3)
    assert conn.execute("select count(price_vs_median) from listings").fetchone()[0] == 0
    assert "price_vs_median" not in _listing(conn, "t:cheap")["provenance"]


def test_neighbourhoods_that_touch_are_adjacent(conn):
    geo.load(conn, HOODS, STATIONS)
    adjacent = dict(conn.execute("select id, adjacent from neighbourhoods order by id").fetchall())
    assert adjacent == {1: [2], 2: [1], 3: []}


def test_loading_twice_changes_nothing(conn):
    first = geo.load(conn, HOODS, STATIONS)
    assert geo.load(conn, HOODS, STATIONS) == first


def test_the_city_dump_and_the_station_file_read_as_expected():
    dump = json.dumps({"fields": [{"id": f} for f in ("_id", "AREA_SHORT_CODE", "AREA_NAME", "geometry")],
                       "records": [[1, "174", " South Eglinton-Davisville ", _square(-79.39, 43.69)]]})
    [hood] = geo.parse_neighbourhoods(dump)
    assert (hood["id"], hood["name"]) == (174, "South Eglinton-Davisville")
    assert json.loads(hood["geometry"])["type"] == "Polygon"
    names = {s["name"] for s in geo.stations()}
    assert {"Union", "Spadina", "Bloor-Yonge", "Ossington"} <= names and len(names) >= 60
