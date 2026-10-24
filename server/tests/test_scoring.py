import random
import time
from datetime import UTC, date, datetime

import pytest

from landline.contracts import Anchor, Listing, Preferences, Provenance
from landline.scoring import FeatureMatrix, explain, rank, weights_for
from landline.scoring import utilities as u

NOW = datetime(2026, 10, 23, tzinfo=UTC)
KING_AND_BAY = Anchor(label="King and Bay", lat=43.6487, lng=-79.3817)


def _l(i: str, rent: int = 2000, **kw) -> Listing:
    """A one-bedroom at King and Bay unless told otherwise; monthly cost is the rent (no fees)."""
    fields = {"beds": 1.0, "sqft": 650, "lat": 43.6487, "lng": -79.3817, **kw}
    return Listing(listing_id=f"t:{i}", source="t", source_id=str(i), url=f"https://example.com/{i}",
                   first_seen=NOW, last_seen=NOW, address=f"{i} Test St", rent=rent, monthly_cost=rent,
                   **fields)


def _order(listings, prefs, **kw) -> list[str]:
    return rank(FeatureMatrix.build(listings), prefs, **kw).order


def _prefs(**kw) -> Preferences:
    return Preferences(**{"max_monthly": 3000, **kw})


# §1 Hard filters


def test_budget_filter_allows_ten_percent_over():
    order = _order([_l("in", 3300), _l("out", 3301)], _prefs())
    assert order == ["t:in"]


def test_beds_count_a_den_as_half():
    order = _order([_l("1+den", beds=1.0, dens=1), _l("1", beds=1.0)], _prefs(min_beds=1.5))
    assert order == ["t:1+den"]


def test_rooms_are_out_unless_asked_for():
    rows = [_l("room", unit_type="room"), _l("flat", unit_type="apartment")]
    assert _order(rows, _prefs()) == ["t:flat"]
    assert set(_order(rows, _prefs(must_have=["room"]))) == {"t:room", "t:flat"}


def test_dead_and_rejected_are_out():
    rows = [_l("a"), _l("b"), _l("c")]
    assert _order(rows, _prefs(), status={"t:a": "dead"}, rejected={"t:b"}) == ["t:c"]


def test_pets_exclude_only_with_a_call_or_city_record():
    said = _l("listing-says", pets="none", provenance={"pets": Provenance(source="llm", quote="No pets.")})
    city = _l("city-says", pets="none", provenance={"pets": Provenance(source="city")})
    heard = _l("call-will-say")
    order = _order([said, city, heard], _prefs(pets=["dog"]), overrides={"t:call-will-say": {"pets": "none"}})
    assert order == ["t:listing-says"]   # kept and ranked, the gap is a question for the Caller


def test_a_cats_only_place_still_takes_cats():
    city = _l("cats", pets="cats_only", provenance={"pets": Provenance(source="city")})
    assert _order([city], _prefs(pets=["cat"])) == ["t:cats"]
    assert _order([city], _prefs(pets=["dog"])) == []


def test_must_have_excludes_only_with_evidence():
    none_city = _l("no-parking", parking="none", provenance={"parking": Provenance(source="city")})
    unknown = _l("unknown")
    assert _order([none_city, unknown], _prefs(must_have=["parking"])) == ["t:unknown"]
    assert set(_order([none_city, unknown], _prefs())) == {"t:no-parking", "t:unknown"}


# §2 Utilities


def test_cost_utility_breakpoints():
    assert u.cost(2550, 3000) == 1.0
    assert u.cost(3000, 3000) == pytest.approx(0.4)
    assert u.cost(3300, 3000) == pytest.approx(0.0)
    assert 0.4 < u.cost(2800, 3000) < 1.0


def test_other_utilities():
    assert u.transit(3) == 1.0 and u.transit(20) == 0.0 and u.transit(None) is None
    assert u.size(650, 1) == pytest.approx(0.5) and u.size(975, 1) == 1.0 and u.size(None, 1) is None
    assert u.size(500, 1, desired=1000) == 0.5
    assert u.high_floor(15) == 1.0 and u.high_floor(3) == pytest.approx(0.2)
    assert u.ground_floor(0, None, None) == 1.0 and u.ground_floor(8, True, None) == 1.0
    assert u.ground_floor(8, None, None) == 0.0 and u.ground_floor(None, None, None) is None
    assert u.parking("included") == 1.0 and u.parking("extra") == 0.6 and u.parking("none") == 0.0
    dog = u.species(["a small dog"])
    assert u.pets("any", dog) == 1.0 and u.pets("case_by_case", dog) == 0.6
    assert u.pets("small_only", dog) == 0.0 and u.pets("cats_only", u.species(["cat"])) == 1.0
    assert u.move_in(date(2026, 12, 10), date(2026, 12, 1)) == 1.0
    assert u.move_in(date(2027, 1, 30), date(2026, 12, 1)) == 0.0


def test_amenities_count_as_evidence_but_their_absence_does_not():
    m = FeatureMatrix.build([_l("a", amenities=["Insuite Laundry", "Elevator", "Balcony"]), _l("b")])
    assert (m.laundry, m.elevator, m.outdoor_space) == (["in_suite", None], [True, None], [True, None])


def test_an_impossible_size_is_unknown():
    assert FeatureMatrix.build([_l("tiny", sqft=2)]).sqft == [None]


# §3 Weights


def test_default_weights_need_a_signal():
    w = weights_for(Preferences())
    assert "cost" not in w and "location" not in w and "move_in" not in w   # no budget, place or date
    assert w["size"] == 1.5 and w["light"] == 1.0
    w = weights_for(_prefs(anchors=[KING_AND_BAY], move_in="December 1"))
    assert (w["cost"], w["location"], w["move_in"]) == (3.0, 3.0, 1.0)


def test_stated_priorities_get_four_three_two():
    w = weights_for(_prefs(priorities=["Parking", "bright", "size"]))
    assert (w["parking"], w["light"], w["size"]) == (4.0, 3.0, 2.0)
    assert w["cost"] == 3.0   # unstated features keep their defaults


def test_must_haves_and_pets_weigh_in_and_boosts_cap_at_five():
    w = weights_for(_prefs(must_have=["laundry_in_suite"], pets=["dog"]), boosts={"light": 9})
    assert w["laundry"] == 2.0 and w["pets"] == 2.0 and w["light"] == 5.0


# §4 Adjustments after calls


def test_verified_adds_seven_points():
    rows = [_l("a"), _l("b")]
    r = rank(FeatureMatrix.build(rows), _prefs(), status={"t:b": "verified"})
    assert r.order == ["t:b", "t:a"]
    assert r.scores["t:b"] - r.scores["t:a"] == pytest.approx(7.0)


def test_suspect_is_never_first_and_booked_is_pinned_on_top():
    rows = [_l("cheap", 1500), _l("mid", 2400), _l("dear", 3200)]
    order = _order(rows, _prefs(), status={"t:cheap": "suspect", "t:dear": "booked"})
    assert order == ["t:dear", "t:mid", "t:cheap"]
    assert _order(rows[:2], _prefs(), status={"t:cheap": "suspect"})[0] == "t:mid"


def test_a_fact_from_a_call_replaces_the_listing_value():
    rows = [_l("a", 2000), _l("b", 2100)]
    assert _order(rows, _prefs()) == ["t:a", "t:b"]
    assert _order(rows, _prefs(), overrides={"t:a": {"monthly_cost": 3200}}) == ["t:b", "t:a"]


# §5 Explanations


def test_reasons_are_the_strongest_known_features_in_plain_words():
    rows = [_l("a", 2180, sqft=900, amenities=["Insuite Laundry"])]
    prefs = _prefs(max_monthly=2800, anchors=[KING_AND_BAY], priorities=["laundry"])
    why = rank(FeatureMatrix.build(rows), prefs).explain("t:a")
    # Light and transit are unknown here: they score 0.5 and are never cited.
    assert why.reasons == ["in-suite laundry", "$620 under budget", "right by King and Bay"]
    assert why.watch_out is None
    assert set(why.contributions) == {"cost", "location", "size", "transit", "light", "laundry",
                                      "building_quality", "value"}


def test_distance_is_a_reason_when_close_and_a_watch_out_when_far():
    near, far = _l("near", lat=43.6559), _l("far", lat=43.7027)     # 0.8 km and 6.0 km north
    r = rank(FeatureMatrix.build([near, far]), _prefs(anchors=[KING_AND_BAY]))
    assert "0.8 km from King and Bay" in r.explain("t:near").reasons
    assert r.explain("t:far").watch_out == "6.0 km from King and Bay"


def test_watch_out_names_the_worst_known_feature():
    why = explain(_l("a", 3200, sqft=400), _prefs())
    assert why.watch_out == "$200 over budget"
    assert why.rank == 1


# Monotonicity and stability (docs/SCORING.md §8)


def _crowd() -> list[Listing]:
    rng = random.Random(3)
    return [_l(f"c{i}", rng.randrange(1600, 3200), sqft=rng.randrange(450, 1100),
               lat=43.6487 + rng.uniform(-0.04, 0.04), lng=-79.3817 + rng.uniform(-0.05, 0.05),
               light_score=rng.uniform(0.2, 0.9), transit_walk_min=rng.uniform(2, 20)) for i in range(30)]


TWINS = {
    "cost": ({"rent": 1900}, {"rent": 2600}),
    "size": ({"sqft": 950}, {"sqft": 500}),
    "light": ({"light_score": 0.9}, {"light_score": 0.2}),
    "location": ({"lat": 43.6487}, {"lat": 43.6987}),
    "transit": ({"transit_walk_min": 4}, {"transit_walk_min": 18}),
}


@pytest.mark.parametrize("feature", TWINS)
def test_the_better_twin_wins_and_more_weight_never_sinks_it(feature):
    better, worse = TWINS[feature]
    base = {"rent": 2300, "sqft": 700, "light_score": 0.5, "transit_walk_min": 10}
    rows = [*_crowd(), _l("better", **{**base, **better}), _l("worse", **{**base, **worse})]
    matrix = FeatureMatrix.build(rows)
    prefs = _prefs(anchors=[KING_AND_BAY])
    order = rank(matrix, prefs).order
    assert order.index("t:better") < order.index("t:worse")
    for boost in (1, 2, 4):
        heavier = rank(matrix, prefs, weights=weights_for(prefs, boosts={feature: boost})).order
        assert heavier.index("t:better") <= order.index("t:better")
        order = heavier


def test_same_input_same_order_and_ties_keep_the_previous_order():
    rows = [*_crowd(), _l("x"), _l("y")]
    matrix = FeatureMatrix.build(rows)
    first = rank(matrix, _prefs()).order
    assert rank(matrix, _prefs()).order == first
    assert first.index("t:x") < first.index("t:y")              # exact tie: by listing id
    swapped = rank(matrix, _prefs(), previous=["t:y", "t:x"]).order
    assert swapped.index("t:y") < swapped.index("t:x")          # exact tie: previous rank wins


# §7 Performance


def _catalogue(n: int) -> list[Listing]:
    rng = random.Random(7)
    amenities = ["Elevator", "Insuite Laundry", "Parking", "Balcony", "Air Conditioning"]
    return [_l(f"p{i}", rng.randrange(1200, 4000), beds=float(rng.randrange(0, 4)),
               sqft=rng.choice([None, rng.randrange(300, 1500)]),
               lat=43.65 + rng.uniform(-0.1, 0.1), lng=-79.38 + rng.uniform(-0.15, 0.15),
               light_score=rng.choice([None, rng.random()]),
               transit_walk_min=rng.choice([None, rng.uniform(1, 25)]),
               amenities=rng.sample(amenities, rng.randrange(0, 4))) for i in range(n)]


def test_ranking_two_thousand_listings_takes_under_five_ms():
    matrix = FeatureMatrix.build(_catalogue(2000))
    prefs = _prefs(min_beds=1, anchors=[KING_AND_BAY], priorities=["location", "cost", "light"])
    rank(matrix, prefs)   # anchor distances and utility columns are computed once
    runs = []
    for _ in range(20):
        t = time.perf_counter()
        rank(matrix, prefs)
        runs.append(time.perf_counter() - t)
    print(f"ranking 2,000 listings: best of 20 = {min(runs) * 1000:.2f} ms")
    assert min(runs) < 0.005
