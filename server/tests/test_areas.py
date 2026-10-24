"""Named areas in the Matcher (D-05): a neighbourhood found by its words scores 1 inside, 0.6 next door, and
exp(-d / 2) from its middle beyond that (docs/SCORING.md, Location)."""
from datetime import UTC, datetime

import pytest

from landline.contracts import Listing, Preferences
from landline.scoring import FeatureMatrix, rank
from landline.scoring.features import Hood

NOW = datetime(2026, 10, 24, tzinfo=UTC)
HOODS = [Hood(1, "Fort York-Liberty Village", 43.637, -79.417, (2,)),
         Hood(2, "Niagara", 43.639, -79.407, (1,)),
         Hood(3, "Annex", 43.670, -79.405, ()),
         Hood(4, "Rouge", 43.805, -79.150, ())]


def _listing(n: int, hood: str | None, lat: float, lng: float) -> Listing:
    return Listing(listing_id=f"t:{n}", source="t", source_id=str(n), url="https://example.com",
                   first_seen=NOW, last_seen=NOW, address=f"{n} Area St", lat=lat, lng=lng, rent=2000,
                   monthly_cost=2000, beds=1, neighbourhood=hood)


LISTINGS = [_listing(1, "Fort York-Liberty Village", 43.637, -79.418),
            _listing(2, "Niagara", 43.640, -79.406),
            _listing(3, "Annex", 43.670, -79.404),
            _listing(4, "Rouge", 43.805, -79.151)]


def _location(areas: list[str], hoods=HOODS) -> dict[str, float | None]:
    matrix = FeatureMatrix.build(LISTINGS, hoods)
    ranking = rank(matrix, Preferences(areas=areas), keep_all=True)
    return {lid: round(ranking.explain(lid).contributions["location"] / 3.0, 3) for lid in matrix.ids}


@pytest.mark.parametrize(("said", "found"), [
    ("Liberty Village", ["Fort York-Liberty Village"]),
    ("the Annex", ["Annex"]),
    ("annex area", ["Annex"]),
    ("Leslieville", []),   # not a City name: the place lookup (D-04) is for those
])
def test_an_area_is_found_by_its_words(said, found):
    assert [h.name for h in FeatureMatrix.build(LISTINGS, HOODS).named([said])] == found


def test_inside_beats_next_door_beats_further_away():
    loc = _location(["Liberty Village"])
    assert loc["t:1"] == 1.0 and loc["t:2"] == 0.6
    assert 0 <= loc["t:4"] < loc["t:3"] < 0.6       # further from the named one's middle, lower (Rouge: ~0)
    order = rank(FeatureMatrix.build(LISTINGS, HOODS), Preferences(areas=["Liberty Village"])).order
    assert order[:2] == ["t:1", "t:2"] and order[-1] == "t:4"


def test_without_neighbourhood_data_only_an_exact_name_counts():
    loc = _location(["annex"], hoods=[])
    assert loc["t:3"] == 1.0 and loc["t:1"] == 0.5    # unknown counts 0.5: no evidence either way


def test_two_named_areas_both_count_as_inside():
    loc = _location(["Annex", "Niagara"])
    assert loc["t:3"] == loc["t:2"] == 1.0 and loc["t:1"] == 0.6   # Liberty Village touches Niagara
