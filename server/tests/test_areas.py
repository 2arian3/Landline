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


# Names the City doesn't use for one neighbourhood (#101)

DOWNTOWN = [Hood(170, "Yonge-Bay Corridor", 43.651, -79.383, (168,)),
            Hood(168, "Downtown Yonge East", 43.659, -79.378, (170,))]


def test_downtown_toronto_finds_the_downtown_neighbourhoods_and_puts_them_on_top():
    listings = [*LISTINGS, _listing(5, "Yonge-Bay Corridor", 43.651, -79.384),
                _listing(6, "Downtown Yonge East", 43.659, -79.377)]
    matrix = FeatureMatrix.build(listings, [*HOODS, *DOWNTOWN])
    assert [h.id for h in matrix.named(["downtown Toronto"])] == [170, 168]   # not Annex, not Niagara
    assert matrix.named(["downtown"]) == matrix.named(["downtown core"]) == matrix.named(["downtown Toronto"])
    # Every place costs the same, so without the area the order is by id; with it, downtown leads.
    assert rank(matrix, Preferences(max_monthly=2800)).order[:2] == ["t:1", "t:2"]
    assert rank(matrix, Preferences(max_monthly=2800, areas=["downtown Toronto"])).order[:2] == ["t:5", "t:6"]


def test_an_area_that_names_nothing_gives_location_no_weight():
    matrix = FeatureMatrix.build(LISTINGS, HOODS)
    said = rank(matrix, Preferences(max_monthly=2800, areas=["Narnia"]), keep_all=True)
    plain = rank(matrix, Preferences(max_monthly=2800), keep_all=True)
    assert "location" not in said.explain("t:1").contributions
    assert said.scores == plain.scores   # nothing flattened: the scores are the ones with no area at all
