"""The ranking eval (E-03, evals/run_ranking.py) runs with no key and no network. The three ranking problems
it found (too small, too expensive, too dark) were kept here as strict xfails until feedback fixed them."""

import json

from evals import run_ranking as R
from landline.contracts import FeedbackGiven
from landline.orchestrator import SessionRuntime


async def test_the_ranking_eval_runs_offline_and_holds_its_bars():
    results = await R.run(list(R.SETS), keep_report=True)
    for name in R.SETS:
        r = results[name]
        assert r["cases"] and r["hit_at_3"] >= R.HIT_BAR, name
        assert r["monotonicity"] == []
        assert {c["id"] for c in r["per_case"]} == {c["id"] for c in R.load(name)}
    checks = results["checks"]
    assert checks["light_twins"] == []
    assert checks["latency"]["p50_ms"] < R.LATENCY_BAR_MS


async def test_the_same_brief_gives_the_same_board_every_time():
    cat = R.catalogue()
    case = R.load("tune")[0]
    boards = []
    for _ in range(3):
        rt = SessionRuntime(cat)
        sid = await R._session(rt, case)
        boards.append([c.listing_id for c in rt.get(sid).board])
    assert boards[0] == boards[1] == boards[2]


def _case(name: str) -> dict:
    return json.loads((R.SETS["tune"] / f"{name}.json").read_text(encoding="utf-8"))


async def _after(case: dict, reason: str, attribute: str, cat=None) -> tuple[list[str], list[str]]:
    cat = cat or R.catalogue()
    rt = SessionRuntime(cat)
    sid = await R._session(rt, case)
    before = [c.listing_id for c in rt.get(sid).board]
    await rt.emit(
        FeedbackGiven(
            session_id=sid,
            at=R.AT,
            idem_key=f"{sid}:fb",
            listing_id=before[0],
            reaction="dislike",
            reason=reason,
            attribute=attribute,
        )
    )
    return before, [c.listing_id for c in rt.get(sid).board]


async def test_too_small_keeps_a_bigger_unit_above_smaller_ones():
    before, after = await _after(_case("union-commuter"), "too small", "size")
    assert before[:2] == ["livrent:134584", "livrent:139609"]  # 504 sq ft, then 700 sq ft
    big = after.index("livrent:139609")  # 393 King St W, 700 sq ft
    smaller = [after.index(x) for x in ("livrent:108451", "livrent:130282", "livrent:100747")]  # 502–607
    assert all(big < s for s in smaller)


async def test_too_expensive_brings_a_cheaper_place_into_the_top_three():
    # family-space: $3,800 budget; the goal is 2+ beds at <= $2,500 with >= 900 sq ft. Five turns of "too
    # expensive" never get one into the top 3, which stays at $2,596-$2,980 (all under 85% of the budget).
    result = await R.run_case(SessionRuntime(R.catalogue()), _case("family-space"))
    assert result["turns"] is not None


async def test_too_dark_takes_the_card_it_was_said_about_off_the_top():
    from landline.contracts import Listing
    from landline.db.catalogue import Catalogue

    rows = [
        Listing(
            listing_id=f"t:{i}",
            source="t",
            source_id=i,
            url=f"https://example.com/{i}",
            first_seen=R.AT,
            last_seen=R.AT,
            address=i,
            lat=43.66,
            lng=-79.39 + dx,
            rent=c,
            monthly_cost=c,
            beds=1,
            sqft=600,
            light_score=light,
        )
        for i, light, c, dx in R.DARK_ROWS
    ]
    case = {
        "id": "dark",
        "brief": {
            "max_monthly": 2500,
            "min_beds": 1,
            "priorities": ["location", "cost"],
            "anchors": [{"label": "work", "lat": 43.66, "lng": -79.39}],
        },
    }
    before, after = await _after(case, "too dark", "light", Catalogue(rows, "eval"))
    assert before[0] == "t:a-dark-close"  # light 0.15, the closest
    assert after[0] != "t:a-dark-close"
