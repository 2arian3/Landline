"""Where each listing is (D-05): its City neighbourhood, the neighbourhoods next to it, and the nearest subway
station with the walk to it.

- Neighbourhoods: the City of Toronto's 158 boundaries (Open Government Licence – Toronto), fetched from the
  open-data portal's datastore dump, which its robots.txt allows. Two neighbourhoods are adjacent when their
  boundaries touch; the published shapes leave hairline gaps, so "touch" allows about 15 m.
- Stations: TTC subway stations from OpenStreetMap (pipeline/data/ttc_subway_stations.json, ODbL). The walk
  is the straight-line distance × 1.3 at 80 m a minute (docs/DATA.md).
- Median rents: each neighbourhood's median monthly cost by bedrooms (0, 1, 2, 3+) over the active listings
  in it, kept only where at least 5 share a neighbourhood and a bedroom count; then each listing's
  price_vs_median, its monthly cost over its group's median.

PostGIS does the geometry. Nothing here is a guess: a listing outside every neighbourhood keeps none, and one
whose group is too small for a median keeps no price_vs_median.

    python -m pipeline geo --load      # into $DATABASE_URL, then prints the join rates
"""
from __future__ import annotations

import json
from pathlib import Path

VERSION = "geo-v1"
HOODS_URL = ("https://ckan0.cf.opendata.inter.prod-toronto.ca/datastore/dump/"
             "5e6095fc-1bef-4776-887c-28d37f722c51?format=json")
STATIONS = Path(__file__).resolve().parent / "data" / "ttc_subway_stations.json"
TOUCH_M = 15          # boundaries this close count as touching
WALK_FACTOR, WALK_M_PER_MIN = 1.3, 80
MEDIAN_MIN = 5        # listings a neighbourhood needs at a bedroom count before its median means anything


def parse_neighbourhoods(dump: str) -> list[dict]:
    """The datastore dump's rows as {id, name, geometry}: id is the City's neighbourhood number."""
    data = json.loads(dump)
    cols = [f["id"] for f in data["fields"]]
    out = []
    for rec in data["records"]:
        row = dict(zip(cols, rec, strict=True))
        shape = row["geometry"]
        out.append({"id": int(row["AREA_SHORT_CODE"]), "name": row["AREA_NAME"].strip(),
                    "geometry": shape if isinstance(shape, str) else json.dumps(shape)})
    return out


def stations() -> list[dict]:
    return json.loads(STATIONS.read_text())["stations"]


LOAD_HOOD = """
insert into neighbourhoods (id, name, geom)
values (%(id)s, %(name)s, ST_Multi(ST_SetSRID(ST_GeomFromGeoJSON(%(geometry)s), 4326))::geography)
on conflict (id) do update set name = excluded.name, geom = excluded.geom
"""

ADJACENCY = """
update neighbourhoods n set adjacent = coalesce((
  select array_agg(m.id order by m.id) from neighbourhoods m
  where m.id <> n.id and ST_DWithin(n.geom, m.geom, %(touch)s)), '{}')
"""

LOAD_STATION = """
insert into stations (id, name, line, geom)
values (%(id)s, %(name)s, null, ST_SetSRID(ST_MakePoint(%(lng)s, %(lat)s), 4326)::geography)
on conflict (id) do update set name = excluded.name, geom = excluded.geom
"""

TAG = """
update listings l set neighbourhood_id = n.id, neighbourhood = n.name,
  provenance = l.provenance || jsonb_build_object('neighbourhood',
    jsonb_build_object('source', 'city', 'at', now(), 'version', %(version)s::text))
from neighbourhoods n
where l.geom is not null and ST_Covers(n.geom::geometry, l.geom::geometry)
"""

NEAREST = """
update listings l set transit_station_id = (select s.id from stations s order by s.geom <-> l.geom limit 1)
where l.geom is not null
"""

WALK = """
update listings l
set transit_walk_min = round((ST_Distance(l.geom, s.geom) * %(factor)s / %(pace)s)::numeric, 1),
  provenance = l.provenance || jsonb_build_object('transit_walk_min',
    jsonb_build_object('source', 'derived', 'at', now(), 'version', %(version)s::text))
from stations s where s.id = l.transit_station_id
"""

# Bedrooms as the medians group them: "0" to "2", and "3" for three or more.
BEDS = "least(floor(l.beds), 3)::int::text"

MEDIANS = f"""
update neighbourhoods n set median_rent = coalesce((
  select jsonb_object_agg(g.beds, g.median) from (
    select {BEDS} as beds, round(percentile_cont(0.5) within group (order by l.monthly_cost)) as median
    from listings l
    where l.status = 'active' and l.neighbourhood_id = n.id and l.monthly_cost is not null
      and l.beds is not null
    group by 1 having count(*) >= %(min)s) g), '{{}}')
"""

VALUE = f"""
update listings l set price_vs_median = case when n.median_rent ? {BEDS} and l.monthly_cost is not null
    then round(l.monthly_cost / (n.median_rent ->> {BEDS})::numeric, 3) end,
  provenance = case when n.median_rent ? {BEDS} and l.monthly_cost is not null
    then l.provenance || jsonb_build_object('price_vs_median',
      jsonb_build_object('source', 'derived', 'at', now(), 'version', %(version)s::text))
    else l.provenance - 'price_vs_median' end
from neighbourhoods n where n.id = l.neighbourhood_id
"""

UNPLACED = """
update listings set price_vs_median = null, provenance = provenance - 'price_vs_median'
where neighbourhood_id is null and price_vs_median is not null
"""

COVERAGE = """
select count(*) as listings, count(geom) as located, count(neighbourhood_id) as neighbourhood,
  count(transit_walk_min) as station, count(*) filter (where transit_walk_min <= 15) as within_15_min,
  count(price_vs_median) as price_vs_median,
  (select count(*) from neighbourhoods) as neighbourhoods,
  (select count(*) from neighbourhoods where cardinality(adjacent) > 0) as with_neighbours,
  (select count(*) from neighbourhoods where median_rent <> '{}') as with_median,
  (select count(*) from stations) as stations
from listings where status = 'active'
"""


def load(conn, hoods: list[dict], station_rows: list[dict], median_min: int = MEDIAN_MIN) -> dict:
    """Load the reference data and tag every located listing, in one transaction. Returns the join rates."""
    with conn.transaction(), conn.cursor() as cur:
        cur.executemany(LOAD_HOOD, hoods)
        cur.execute(ADJACENCY, {"touch": TOUCH_M})
        cur.executemany(LOAD_STATION, station_rows)
        cur.execute(TAG, {"version": VERSION})
        cur.execute(NEAREST)
        cur.execute(WALK, {"factor": WALK_FACTOR, "pace": WALK_M_PER_MIN, "version": VERSION})
        cur.execute(MEDIANS, {"min": median_min})
        cur.execute(VALUE, {"version": VERSION})
        cur.execute(UNPLACED)
        cur.execute(COVERAGE)
        cols = [d.name for d in cur.description]
        return dict(zip(cols, cur.fetchone(), strict=True))


def report(c: dict) -> str:
    def pct(n: int) -> str:
        return f"{n} ({100 * n / c['located']:.0f}% of located)" if c["located"] else str(n)
    return "\n".join([
        f"geo: {c['neighbourhoods']} neighbourhoods ({c['with_neighbours']} with neighbours), "
        f"{c['stations']} stations",
        f"  listings: {c['listings']} active, {c['located']} located",
        f"  in a neighbourhood: {pct(c['neighbourhood'])}",
        f"  nearest station: {pct(c['station'])}, within a 15 min walk: {pct(c['within_15_min'])}",
        f"  median rent: {c['with_median']} neighbourhoods with at least {MEDIAN_MIN} listings at a bedroom "
        f"count; price vs median: {pct(c['price_vs_median'])}",
        "  City building records (RentSafeTO): not joined",
    ])
