"""City reference data: neighbourhoods, TTC stations and RentSafeTO buildings.

D-05 loads them; the runtime and the enricher read them. Geometry goes in as
GeoJSON (neighbourhoods) or lat/lng, and points come back as `lat`/`lng`.
"""
from __future__ import annotations

import json
from typing import Any

from psycopg.types.json import Jsonb

from landline.db.base import Conn, Repo
from landline.db.rows import Building, Neighbourhood, Station

_POINT = "ST_SetSRID(ST_MakePoint(%(lng)s, %(lat)s), 4326)::geography"
_BUILDING = """building_id, rsn, address_norm, ST_Y(geom::geometry) as lat, ST_X(geom::geometry) as lng,
year_built, storeys, units, pets_allowed, pet_restrictions, ac_type, laundry, parking_type, visitor_parking,
locker, non_smoking, separate_hydro, elevators, barrier_free_entrance, mgmt_company,
eval_score::float as eval_score, eval_date, eval_detail"""
BUILDING_FIELDS = (
    "address_norm", "year_built", "storeys", "units", "pets_allowed", "pet_restrictions", "ac_type",
    "laundry", "parking_type", "visitor_parking", "locker", "non_smoking", "separate_hydro", "elevators",
    "barrier_free_entrance", "mgmt_company", "eval_score", "eval_date", "eval_detail",
)


class Neighbourhoods(Repo):
    async def upsert(self, id: int, name: str, geojson: dict[str, Any], *, adjacent: list[int] | None = None,
                     median_rent: dict[str, int] | None = None, conn: Conn = None) -> Neighbourhood:
        """`geojson` is a Polygon or MultiPolygon in WGS84, as the City publishes it."""
        row = await self._one(
            Neighbourhood,
            "insert into neighbourhoods (id, name, geom, adjacent, median_rent) "
            "values (%s, %s, ST_Multi(ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326))::geography, %s, %s) "
            "on conflict (id) do update set name = excluded.name, geom = excluded.geom, "
            "adjacent = excluded.adjacent, median_rent = excluded.median_rent "
            "returning id, name, adjacent, median_rent",
            (id, name, json.dumps(geojson), adjacent or [], Jsonb(median_rent or {})), conn,
        )
        assert row is not None
        return row

    async def get(self, id: int, conn: Conn = None) -> Neighbourhood | None:
        return await self._one(Neighbourhood, "select id, name, adjacent, median_rent from neighbourhoods "
                               "where id = %s", (id,), conn)

    async def containing(self, lat: float, lng: float, conn: Conn = None) -> Neighbourhood | None:
        """The neighbourhood a point falls in, if any."""
        return await self._one(Neighbourhood, "select id, name, adjacent, median_rent from neighbourhoods "
                               f"where ST_Covers(geom, {_POINT}) order by id limit 1",
                               {"lat": lat, "lng": lng}, conn)


class Stations(Repo):
    async def upsert(self, id: str, name: str, lat: float, lng: float, line: str | None = None,
                     conn: Conn = None) -> Station:
        row = await self._one(
            Station,
            f"insert into stations (id, name, line, geom) values (%(id)s, %(name)s, %(line)s, {_POINT}) "
            "on conflict (id) do update set name = excluded.name, line = excluded.line, geom = excluded.geom "
            "returning id, name, line, ST_Y(geom::geometry) as lat, ST_X(geom::geometry) as lng",
            {"id": id, "name": name, "line": line, "lat": lat, "lng": lng}, conn,
        )
        assert row is not None
        return row

    async def nearest(self, lat: float, lng: float, k: int = 1, conn: Conn = None) -> list[Station]:
        """The k closest stations, with straight-line distance in metres."""
        return await self._all(
            Station,
            "select id, name, line, ST_Y(geom::geometry) as lat, ST_X(geom::geometry) as lng, "
            f"ST_Distance(geom, {_POINT}) as distance_m from stations "
            f"order by geom <-> {_POINT}, id limit %(k)s",
            {"lat": lat, "lng": lng, "k": k}, conn,
        )


class Buildings(Repo):
    async def upsert(self, rsn: int, lat: float | None, lng: float | None, conn: Conn = None,
                     **fields: Any) -> Building:
        """One building by its RentSafeTO registration number. `fields` are columns from BUILDING_FIELDS."""
        unknown = set(fields) - set(BUILDING_FIELDS)
        if unknown:
            raise ValueError(f"not building columns: {sorted(unknown)}")
        if "eval_detail" in fields:
            fields["eval_detail"] = Jsonb(fields["eval_detail"] or {})
        names = list(fields)
        geom = _POINT if lat is not None and lng is not None else "null"
        sets = ", ".join(f"{n} = excluded.{n}" for n in ["geom", *names])
        row = await self._one(
            Building,
            f"insert into buildings (rsn, geom{''.join(', ' + n for n in names)}) "
            f"values (%(rsn)s, {geom}{''.join(f', %({n})s' for n in names)}) "
            f"on conflict (rsn) do update set {sets} returning {_BUILDING}",
            {"rsn": rsn, "lat": lat, "lng": lng, **fields}, conn,
        )
        assert row is not None
        return row

    async def get(self, building_id: int, conn: Conn = None) -> Building | None:
        return await self._one(Building, f"select {_BUILDING} from buildings where building_id = %s",
                               (building_id,), conn)

    async def nearest(self, lat: float, lng: float, within_m: float = 50,
                      conn: Conn = None) -> Building | None:
        """The closest registered building within `within_m` metres: how a listing finds its City record."""
        return await self._one(
            Building,
            f"select {_BUILDING}, ST_Distance(geom, {_POINT}) as distance_m from buildings "
            f"where ST_DWithin(geom, {_POINT}, %(within)s) order by geom <-> {_POINT}, building_id limit 1",
            {"lat": lat, "lng": lng, "within": within_m}, conn,
        )
