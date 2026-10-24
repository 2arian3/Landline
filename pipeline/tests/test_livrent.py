"""Parser tests on a synthetic page shaped like liv.rent's (no real listing content is committed)."""
import json
from datetime import UTC, datetime

from pipeline.sources import livrent

SEEN = datetime(2026, 10, 22, tzinfo=UTC)


def _page(objects: list[dict], ld_items: list[dict]) -> str:
    flight = "1:" + json.dumps({"items": objects}, separators=(",", ":"))   # the site sends compact JSON
    ld = {"@context": "https://schema.org", "@type": "CollectionPage",
          "mainEntity": {"@type": "ItemList", "itemListElement": ld_items}}
    return (
        f'<script type="application/ld+json">{json.dumps(ld)}</script>'
        f"<script>self.__next_f.push([1,{json.dumps(flight)}])</script>"
    )


BUILDING = {
    "__typename": "Building", "building_id": 7, "full_street_name": "10 Example Ave", "city": "Toronto",
    "zip": "M5V 1A1", "building_name": "Example Tower", "building_type": "highrise",
    "location": {"__typename": "Location", "lat": 43.65, "lon": -79.39},
    "building_amenities": [{"__typename": "BuildingAmenity", "name": "Gym"}],
    "listings": [
        {"__typename": "Listing", "listing_id": 1, "price": 2400, "price_frequency": "monthly",
         "bedrooms": 1, "bathrooms": 1, "half_bathrooms": 1, "size": 610, "unit_type_txt_id": "CONDO",
         "unit_type_scope_txt_id": "ENTIRE", "furnished": False, "state_machine": "published",
         "unit_files": [{"aws_s3_key": "b.jpg", "position": 1}, {"aws_s3_key": "a.jpg", "position": 0}],
         "search_deals": ["utilities_included"]},
        {"__typename": "Listing", "listing_id": 2, "price": 900, "price_frequency": "monthly",
         "bedrooms": 1, "bathrooms": 1, "unit_type_txt_id": "HOUSE", "unit_type_scope_txt_id": "PRIVATE_ROOM",
         "state_machine": "published"},
        {"__typename": "Listing", "listing_id": 3, "price": 150, "price_frequency": "daily",
         "bedrooms": 1, "state_machine": "published"},
    ],
}
LD = [{"@type": "ListItem", "item": {"url": "https://liv.rent/building/7/example-tower-toronto-on"}}]


def test_building_units_inherit_address_and_location():
    html = _page([BUILDING, BUILDING], LD)          # duplicates are dropped
    raws = list(livrent.raw_listings(html))
    assert [r["listing_id"] for r in raws] == [1, 2, 3]
    assert raws[0]["full_street_name"] == "10 Example Ave"
    assert raws[0]["location"]["lat"] == 43.65


def test_normalize_unit():
    html = _page([BUILDING], LD)
    urls = livrent.building_urls(html)
    first = livrent.to_listing(next(livrent.raw_listings(html)), SEEN, urls)
    assert first.listing_id == "livrent:1"
    assert first.url == "https://liv.rent/building/7/example-tower-toronto-on"
    assert (first.lat, first.lng, first.geocode_precision) == (43.65, -79.39, "street")
    assert first.baths == 1.5
    assert first.monthly_cost == first.rent == 2400
    assert first.unit_type == "condo"
    assert first.photos[0].endswith("/a.jpg")              # ordered by position
    assert first.utilities_included == ["utilities"]
    assert first.amenities == ["Gym"]
    assert first.provenance["rent"].source == "scraped"
    assert "pets" not in first.provenance                   # unknown stays unknown


def test_rooms_and_non_monthly_prices():
    html = _page([BUILDING], LD)
    raws = list(livrent.raw_listings(html))
    room = livrent.to_listing(raws[1], SEEN, {})
    assert room.unit_type == "room"
    assert livrent.to_listing(raws[2], SEEN, {}) is None    # daily price: not a rental we can rank


def _unit_at(lat: float, lon: float):
    building = {**BUILDING, "location": {"__typename": "Location", "lat": lat, "lon": lon}}
    return livrent.to_listing(next(livrent.raw_listings(_page([building], LD))), SEEN, {})


def test_dropped_minus_sign_is_flipped_when_it_lands_in_toronto():
    unit = _unit_at(43.7082251, 79.335082)                  # 701 Don Mills Rd as the site sends it
    assert (unit.lat, unit.lng, unit.geocode_precision) == (43.7082251, -79.335082, "street")
    assert unit.provenance["lng"].source == "derived"
    assert unit.provenance["lat"].source == "scraped"


def test_positive_longitude_outside_toronto_is_nulled():
    unit = _unit_at(43.65, 120.5)
    assert (unit.lat, unit.lng, unit.geocode_precision) == (None, None, "none")
    assert "lat" not in unit.provenance and "lng" not in unit.provenance


def test_toronto_point_leaves_good_and_missing_points_alone():
    assert livrent.toronto_point(43.65, -79.39) == (43.65, -79.39, False)
    assert livrent.toronto_point(None, None) == (None, None, False)
