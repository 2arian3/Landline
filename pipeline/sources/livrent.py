"""liv.rent: Toronto rental listings.

Why this source: robots.txt allows the city listing pages; every page embeds
structured data (schema.org JSON-LD plus the page's own listing objects) with
coordinates, price, bedrooms, bathrooms, size and unit type, so we parse data
rather than scrape layout. No contact details are read or stored.

Terms note: liv.rent's terms forbid copying its content for commercial use or
redistribution without permission. We keep facts (address, price, size,
coordinates), always link back to the listing, keep volume low, and use photos
only by URL with attribution. See docs/DATA.md.
"""
from __future__ import annotations

import json
import re
from collections.abc import Iterator
from datetime import datetime

from landline.contracts.listing import Listing, Provenance, monthly_cost

SOURCE = "livrent"
BASE = "https://liv.rent"
CITY_PAGE = BASE + "/rental-listings/city/toronto?page={page}"
PHOTO = "https://cdn.liv.rent/800x/filters:quality(70)/{key}"
PARSER_VERSION = "livrent-v1"

_FLIGHT = re.compile(r"self\.__next_f\.push\(\[1,(\".*?\")\]\)", re.S)
_LD = re.compile(r'<script type="application/ld\+json"[^>]*>(.*?)</script>', re.S)
_START = re.compile(r'\{"__typename":"(?:FullListing|Building)"')
_BUILDING_KEYS = ("building_id", "street_name", "building_type", "full_street_name", "city", "state", "zip",
                  "building_name", "location", "building_amenities")

UNIT_TYPES = {
    "CONDO": "condo",
    "APARTMENT": "apartment",
    "HOUSE": "house",
    "TOWNHOUSE": "townhouse",
    "BASEMENT": "basement",
    "LANEWAY_HOUSE": "house",
    "COACH_HOUSE": "house",
}


def _flight_text(html: str) -> str:
    """Join the page's streamed data chunks back into one string."""
    out = []
    for chunk in _FLIGHT.findall(html):
        try:
            out.append(json.loads(chunk))
        except json.JSONDecodeError:
            continue
    return "".join(out)


def raw_listings(html: str) -> Iterator[dict]:
    """Every listing embedded in a city page, deduplicated by listing_id.

    The page carries two shapes: promoted `FullListing` objects (unit and
    building fields together) and `Building` objects whose `listings` hold the
    units. Building fields (address, location, amenities) are merged into each
    of their units.
    """
    text = _flight_text(html)
    decoder = json.JSONDecoder()
    seen: set[int] = set()
    for m in _START.finditer(text):
        try:
            obj, _ = decoder.raw_decode(text, m.start())
        except json.JSONDecodeError:
            continue
        if obj.get("__typename") == "Building":
            building = {k: obj.get(k) for k in _BUILDING_KEYS}
            units = [{**building, **unit} for unit in obj.get("listings") or []]
        else:
            units = [obj]
        for unit in units:
            lid = unit.get("listing_id")
            if lid is None or lid in seen:
                continue
            seen.add(lid)
            yield unit


def building_urls(html: str) -> dict[int, str]:
    """building_id -> canonical listing URL, from the page's JSON-LD item list."""
    urls: dict[int, str] = {}
    for block in _LD.findall(html):
        try:
            data = json.loads(block)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict) or data.get("@type") != "CollectionPage":
            continue
        for el in data.get("mainEntity", {}).get("itemListElement", []):
            url = (el.get("item") or {}).get("url", "")
            m = re.search(r"/building/(\d+)/", url)
            if m:
                urls[int(m.group(1))] = url
    return urls


def to_listing(raw: dict, seen_at: datetime, urls: dict[int, str]) -> Listing | None:
    """Normalize one liv.rent listing. Returns None if it can't be ranked (no monthly price)."""
    price = raw.get("price")
    if not price or raw.get("price_frequency") != "monthly" or raw.get("is_hidden"):
        return None
    if raw.get("state_machine") not in (None, "published"):
        return None

    loc = raw.get("location") or {}
    lat, lng = loc.get("lat"), loc.get("lon")
    baths = raw.get("bathrooms")
    if baths is not None:
        baths = float(baths) + 0.5 * float(raw.get("half_bathrooms") or 0)
        baths = baths or None   # "0 bathrooms" on a whole unit is a blank form field, not a fact
    scope = raw.get("unit_type_scope_txt_id")
    if scope and scope != "ENTIRE":
        unit_type = "room"
    else:
        unit_type = UNIT_TYPES.get(raw.get("unit_type_txt_id") or "", "other")
    files = sorted(raw.get("unit_files") or [], key=lambda f: f.get("position") or 0)
    photos = [PHOTO.format(key=f["aws_s3_key"]) for f in files if f.get("aws_s3_key")][:8]
    deals = raw.get("search_deals") or []
    amenities = [a if isinstance(a, str) else (a.get("name") or a.get("txt_id") or "")
                 for a in (raw.get("building_amenities") or [])]
    building_id = raw.get("building_id")

    rent = int(price)
    fields = {
        "address": raw.get("full_street_name") or raw.get("building_name") or "",
        "building_name": raw.get("building_name"),
        "postal_code": raw.get("zip"),
        "lat": lat,
        "lng": lng,
        "rent": rent,
        "beds": float(raw.get("bedrooms") or 0),
        "baths": baths,
        "sqft": int(raw["size"]) if raw.get("size") else None,
        "unit_type": unit_type,
        "building_type": raw.get("building_type"),
        "furnished": raw.get("furnished"),
        "utilities_included": ["utilities"] if "utilities_included" in deals else [],
        "amenities": [a for a in amenities if a],
        "photos": photos,
    }
    if not fields["address"]:
        return None
    prov = Provenance(source="scraped", at=seen_at, version=PARSER_VERSION)

    return Listing(
        listing_id=f"{SOURCE}:{raw['listing_id']}",
        source=SOURCE,
        source_id=str(raw["listing_id"]),
        url=urls.get(building_id, BASE + "/rental-listings/city/toronto"),
        first_seen=seen_at,
        last_seen=seen_at,
        city=raw.get("city") or "Toronto",
        geocode_precision="street" if lat is not None and lng is not None else "none",
        monthly_cost=monthly_cost(rent, []),
        provenance={k: prov for k, v in fields.items() if v not in (None, [], "")},
        **fields,
    )
