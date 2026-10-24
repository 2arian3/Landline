"""The ranking eval (E-03): the Matcher on renter briefs, on the frozen catalogue. Part of `make eval`. Pure
code: no key, no network, the same numbers every run.

    uv run python -m evals.run_ranking              # tune + holdout, writes the report
    uv run python -m evals.run_ranking --no-report  # tuning: print, leave the report alone

Personas (fixtures/ranking/, holdout in fixtures/holdout/ranking/) are renter briefs with acceptable
listings labelled from explicit criteria written in each file's `why` (budget and beds, then closest to the
place they named, cheapest or largest), not from the ranker. Each runs through the real session runtime,
the path a call takes: the preferences, the anchor the geocoder resolves, then feedback.

- **hit@3**: a labelled acceptable listing is in the board's top 3. Also precision@3.
- **Feedback turns to converge**: the renter has a want the brief didn't say ("too small"). Each turn they
  dislike the highest card not yet disliked, with that reason, through `FeedbackGiven`, until a listing from
  the case's labelled goal set is in the top 3. 0 if it already is; not converged after MAX_TURNS.
- **Monotonicity**: raising one weight never puts a listing that is worse on that feature above one that is
  strictly better on it, which was below; a brighter twin outranks a darker one when light matters, and
  never ranks below it otherwise.
- **Stability**: the same input gives the same order; one feedback turn only flips pairs of cards whose
  order the touched feature explains.
- **Latency**: `rank()` on the full matrix, warm (its columns cached) and cold, and on a matrix ten times
  the catalogue's size. Target < 5 ms warm.
"""

from __future__ import annotations

import argparse
import asyncio
import gc
import json
import statistics
import sys
import time
from datetime import UTC, date, datetime
from itertools import combinations
from typing import Any

from evals import report
from evals.run_extraction import _sha
from landline.config import ROOT
from landline.contracts import (
    Anchor,
    AnchorResolved,
    FeedbackGiven,
    Listing,
    Preferences,
    PreferencesUpdated,
    SessionStarted,
)
from landline.contracts.tools import AnchorRef, PreferenceDelta
from landline.db.catalogue import Catalogue, _from_fixture
from landline.orchestrator import SessionRuntime
from landline.orchestrator import feedback as feedback_rules
from landline.scoring import FeatureMatrix, rank, weights_for

FIXTURES = ROOT / "evals" / "fixtures"
CATALOGUE_PATH = FIXTURES / "listings.json"
SETS = {"tune": FIXTURES / "ranking", "holdout": FIXTURES / "holdout" / "ranking"}
SUMMARY = report.REPORTS / "ranking.json"
AT = datetime(2026, 10, 24, 15, 0, tzinfo=UTC)
TODAY = date(2026, 10, 24)
TOP = 3
MAX_TURNS = 5
LATENCY_BAR_MS = 5.0
HIT_BAR = 0.8
PAIRS = 20  # monotonicity and stability look at pairs among this many top cards


def load(name: str) -> list[dict[str, Any]]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(SETS[name].glob("*.json"))]


def catalogue() -> Catalogue:
    return Catalogue(_from_fixture(CATALOGUE_PATH), "eval")


# ── personas, through the session runtime ──────────────────────────────────


async def _session(rt: SessionRuntime, case: dict[str, Any]) -> str:
    sid = f"s_rank_{case['id']}"[:40]
    b = case["brief"]
    await rt.start(SessionStarted(session_id=sid, at=AT, idem_key=f"{sid}:start", channel="browser"))
    anchors = b.get("anchors") or []
    delta = PreferenceDelta(
        max_monthly=b.get("max_monthly"),
        min_beds=b.get("min_beds"),
        priorities=b.get("priorities") or None,
        pets=b.get("pets") or None,
        anchors=[AnchorRef(label=a["label"]) for a in anchors] or None,
    )
    await rt.emit(PreferencesUpdated(session_id=sid, at=AT, idem_key=f"{sid}:prefs", delta=delta))
    for a in anchors:  # the geocoder answering, off the hot path
        await rt.emit(
            AnchorResolved(
                session_id=sid,
                at=AT,
                idem_key=f"{sid}:anchor:{a['label']}",
                label=a["label"],
                lat=a["lat"],
                lng=a["lng"],
            )
        )
    return sid


def _top(rt: SessionRuntime, sid: str) -> list[str]:
    return [c.listing_id for c in rt.get(sid).board[:TOP]]


async def run_case(rt: SessionRuntime, case: dict[str, Any]) -> dict[str, Any]:
    sid = await _session(rt, case)
    top = _top(rt, sid)
    ok = set(case["acceptable"])
    hits = [lid for lid in top if lid in ok]
    fb, goal = case["feedback"], set(case["feedback"]["goal"])
    turns, disliked, path = 0, [], []
    while not goal & set(_top(rt, sid)) and turns < MAX_TURNS:
        board = [c.listing_id for c in rt.get(sid).board]
        target = next((lid for lid in board if lid not in disliked), None)
        if target is None:
            break
        disliked.append(target)
        turns += 1
        await rt.emit(
            FeedbackGiven(
                session_id=sid,
                at=AT,
                idem_key=f"{sid}:fb{turns}",
                listing_id=target,
                reaction="dislike",
                reason=fb["reason"],
                attribute=fb["attribute"],
            )
        )
        path.append(_top(rt, sid))
    converged = bool(goal & set(_top(rt, sid)))
    return {
        "id": case["id"],
        "top": top,
        "hit": bool(hits),
        "precision": len(hits) / TOP,
        "turns": turns if converged else None,
        "path": path,
        "reason": fb["reason"],
    }


# ── monotonicity and stability, on rank() itself ─────────────────────────────


def _utility(ranking, lid: str, feature: str, weights: dict[str, float]) -> float:
    return ranking.explain(lid).contributions[feature] / weights[feature]


def monotonic_weight(matrix: FeatureMatrix, case: dict[str, Any]) -> list[str]:
    """Raise each weighted feature by 1 in turn. Among the top cards, a pair where the one strictly better
    on that feature was above must keep it above. Returns the violations."""
    prefs = _prefs(case)
    base_w = weights_for(prefs, today=TODAY)
    before = rank(matrix, prefs, weights=base_w, today=TODAY)
    top = before.order[:PAIRS]
    bad = []
    for f in base_w:
        w = dict(base_w, **{f: base_w[f] + 1.0})
        after = rank(matrix, prefs, weights=w, today=TODAY)
        pos_b = {lid: i for i, lid in enumerate(before.order)}
        pos_a = {lid: i for i, lid in enumerate(after.order)}
        for a, b in combinations(top, 2):
            ua, ub = _utility(before, a, f, base_w), _utility(before, b, f, base_w)
            if abs(ua - ub) < 1e-9:
                continue
            better, worse = (a, b) if ua > ub else (b, a)
            if pos_b[better] < pos_b[worse] and pos_a[better] > pos_a[worse]:
                bad.append(f"{case['id']}: +1 {f} put {worse} above {better}, which is better on it")
    return bad


def light_twins() -> list[str]:
    """Two listings the same in every way but light: the brighter must win when light matters, and never
    lose otherwise."""
    base = dict(
        source="t",
        url="https://example.com/x",
        first_seen=AT,
        last_seen=AT,
        address="1 Twin St",
        lat=43.66,
        lng=-79.39,
        rent=2200,
        monthly_cost=2200,
        beds=1,
        sqft=600,
    )
    bright = Listing(listing_id="t:bright", source_id="b", light_score=0.85, **base)
    dark = Listing(listing_id="t:dark", source_id="d", light_score=0.2, **base)
    m = FeatureMatrix.build([dark, bright])  # the darker first, so id order can't hide a loss
    bad = []
    for label, priorities in (
        ("light first", ["light"]),
        ("light not mentioned", []),
        ("cost first", ["cost"]),
    ):
        prefs = _prefs({"brief": {"max_monthly": 2500, "min_beds": 1, "priorities": priorities}})
        r = rank(m, prefs, today=TODAY)
        s = r.scores
        if priorities == ["light"] and r.order[0] != "t:bright":
            bad.append(f"twins, {label}: the darker twin ranked first")
        if s["t:bright"] < s["t:dark"]:
            bad.append(f"twins, {label}: the brighter twin scored lower")
    return bad


# Light: id, light score, monthly cost, degrees of longitude east of the anchor. Same size and beds.
DARK_ROWS = (
    ("a-dark-close", 0.15, 2100, 0.0),
    ("b-dark-cheap", 0.20, 1900, 0.004),
    ("c-mid", 0.50, 2200, 0.006),
    ("d-bright", 0.80, 2300, 0.008),
    ("e-bright-far", 0.90, 2250, 0.02),
    ("f-basement", 0.10, 1800, 0.003),
)


async def too_dark() -> list[str]:
    """The 0:55 demo shot on a small catalogue where light varies (the frozen one has no light scores):
    "too dark" on the top card, a dark one, must take it off first and sink darker units under brighter
    ones. Every pair that swaps must be explained by light."""
    rows = [
        Listing(
            listing_id=f"t:{i}",
            source="t",
            source_id=i,
            url=f"https://example.com/{i}",
            first_seen=AT,
            last_seen=AT,
            address=i,
            lat=43.66,
            lng=-79.39 + dx,
            rent=c,
            monthly_cost=c,
            beds=1,
            sqft=600,
            light_score=light,
        )
        for i, light, c, dx in DARK_ROWS
    ]
    case = {
        "id": "too-dark",
        "brief": {
            "max_monthly": 2500,
            "min_beds": 1,
            "priorities": ["location", "cost"],
            "anchors": [{"label": "work", "lat": 43.66, "lng": -79.39}],
        },
        "feedback": {"reason": "too dark", "attribute": "light"},
    }
    return await one_turn_moves_what_it_should(Catalogue(rows, "eval"), case)


def same_input_same_order(matrix: FeatureMatrix, case: dict[str, Any]) -> list[str]:
    prefs = _prefs(case)
    first = rank(matrix, prefs, today=TODAY).order
    again = [rank(matrix, prefs, today=TODAY).order for _ in range(3)]
    fresh = rank(
        FeatureMatrix.build([matrix_listing for matrix_listing in _listings(matrix)]), prefs, today=TODAY
    ).order
    return [
        f"{case['id']}: the same brief gave a different order" for order in (*again, fresh) if order != first
    ]


async def one_turn_moves_what_it_should(cat: Catalogue, case: dict[str, Any]) -> list[str]:
    """After one feedback turn on the top card: it is no longer first, and every pair of top cards that
    swapped is explained by the touched feature (the one that rose is better on it)."""
    rt = SessionRuntime(cat)
    sid = await _session(rt, case)
    before = [c.listing_id for c in rt.get(sid).board]
    target = before[0]
    fb = case["feedback"]
    await rt.emit(
        FeedbackGiven(
            session_id=sid,
            at=AT,
            idem_key=f"{sid}:stab",
            listing_id=target,
            reaction="dislike",
            reason=fb["reason"],
            attribute=fb["attribute"],
        )
    )
    state = rt.state(sid)
    bad = []
    if rt.get(sid).board[0].listing_id == target:
        bad.append(f"{case['id']}: '{fb['reason']}' on the top card left it first")
    after_r = feedback_rules.ranked(state, cat, rt.matrix, state.statuses())
    adj = feedback_rules.adjustments(state, cat)
    feature = feedback_rules.attribute_of(fb["attribute"], fb["reason"])
    if feature is None or feature == "parking":
        return bad
    weights = weights_for(state.preferences, adj.boosts, today=TODAY)
    pos_a = {lid: i for i, lid in enumerate(after_r.order)}
    pos_b = {lid: i for i, lid in enumerate(before)}
    for a, b in combinations([lid for lid in before if lid in pos_a][:PAIRS], 2):
        if (pos_b[a] < pos_b[b]) == (pos_a[a] < pos_a[b]):
            continue
        risen, fallen = (b, a) if pos_b[a] < pos_b[b] else (a, b)
        if feature not in weights:
            bad.append(
                f"{case['id']}: '{fb['reason']}' swapped {fallen} and {risen} with {feature} unweighted"
            )
            continue
        ur, uf = _utility(after_r, risen, feature, weights), _utility(after_r, fallen, feature, weights)
        if ur <= uf:
            bad.append(
                f"{case['id']}: '{fb['reason']}' moved {risen} above {fallen}, which is no worse on {feature}"
            )
    return bad


# ── latency ─────────────────────────────────────────────────────────────────


def latency(cat: Catalogue, case: dict[str, Any], repeats: int = 200) -> dict[str, Any]:
    prefs = _prefs(case)

    def timed(matrix: FeatureMatrix, n: int) -> list[float]:
        out = []
        gc.collect()
        gc.disable()  # a collection mid-loop is the machine, not the ranker
        try:
            for _ in range(n):
                t = time.perf_counter()
                rank(matrix, prefs, today=TODAY)
                out.append((time.perf_counter() - t) * 1000)
        finally:
            gc.enable()
        return out

    m = FeatureMatrix.build(cat.listings)
    cold = timed(m, 1)[0]
    warm = timed(m, repeats)
    big = FeatureMatrix.build(
        [x.model_copy(update={"listing_id": f"{x.listing_id}#{k}"}) for k in range(10) for x in cat.listings]
    )
    timed(big, 1)
    warm_big = timed(big, max(50, repeats // 4))
    return {
        "listings": len(cat.listings),
        "cold_ms": round(cold, 2),
        "p50_ms": _pct(warm, 50),
        "p95_ms": _pct(warm, 95),
        "big_listings": len(big),
        "big_p50_ms": _pct(warm_big, 50),
        "big_p95_ms": _pct(warm_big, 95),
    }


def _pct(xs: list[float], p: int) -> float:
    return round(statistics.quantiles(xs, n=100)[p - 1], 3) if len(xs) > 1 else round(xs[0], 3)


# ── the run ─────────────────────────────────────────────────────────────────


def _prefs(case: dict[str, Any]):
    b = case["brief"]
    return Preferences(
        max_monthly=b.get("max_monthly"),
        min_beds=b.get("min_beds"),
        priorities=b.get("priorities") or [],
        pets=b.get("pets") or [],
        anchors=[Anchor(**a) for a in b.get("anchors") or []],
    )


def _listings(matrix: FeatureMatrix) -> list[Listing]:
    return list(matrix.listings)


async def run_set(cat: Catalogue, cases: list[dict[str, Any]]) -> dict[str, Any]:
    rt = SessionRuntime(cat)
    results = [await run_case(rt, c) for c in cases]
    matrix = FeatureMatrix.build(cat.listings)
    mono = [v for c in cases for v in monotonic_weight(matrix, c)]
    stable = [v for c in cases for v in same_input_same_order(matrix, c)]
    moved = [v for c in cases for v in await one_turn_moves_what_it_should(cat, c)]
    turns = [r["turns"] for r in results if r["turns"] is not None]
    n = len(results)
    return {
        "cases": n,
        "hit_at_3": round(sum(r["hit"] for r in results) / n, 3) if n else None,
        "precision_at_3": round(sum(r["precision"] for r in results) / n, 3) if n else None,
        "converged": len(turns),
        "turns_mean": round(statistics.mean(turns), 2) if turns else None,
        "turns_max": max(turns) if turns else None,
        "monotonicity": mono,
        "stability": stable + moved,
        "per_case": results,
    }


async def run(sets: list[str], keep_report: bool = False) -> dict[str, Any]:
    cat = catalogue()
    out: dict[str, Any] = {name: await run_set(cat, load(name)) for name in sets}
    first = (load(sets[0]) or [{"brief": {"max_monthly": 2500, "min_beds": 1}}])[0]
    checks = {"light_twins": light_twins(), "too_dark": await too_dark(), "latency": latency(cat, first)}
    meta = {
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
        "sha": _sha(),
        "catalogue": CATALOGUE_PATH.name,
    }
    if not keep_report:
        data = json.loads(SUMMARY.read_text(encoding="utf-8")) if SUMMARY.exists() else {}
        for name, r in out.items():
            data[name] = {"current": {**meta, **r}, "previous": (data.get(name) or {}).get("current")}
        data["checks"] = {
            "current": {**meta, **checks},
            "previous": (data.get("checks") or {}).get("current"),
        }
        SUMMARY.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        report.write()
    return {"meta": meta, "checks": checks, **out}


def show(results: dict[str, Any]) -> None:
    for name in SETS:
        r = results.get(name)
        if not r:
            continue
        print(
            f"{name}: hit@3 {r['hit_at_3']}  precision@3 {r['precision_at_3']}  converged {r['converged']}/"
            f"{r['cases']}  turns mean {r['turns_mean']} max {r['turns_max']}"
        )
        for c in r["per_case"]:
            print(
                f"  {c['id']:18} hit {'yes' if c['hit'] else 'NO '}  p@3 {c['precision']:.2f}  "
                f"turns {c['turns'] if c['turns'] is not None else 'not converged'} ({c['reason']})"
            )
        for v in r["monotonicity"] + r["stability"]:
            print(f"  FAIL {v}")
    c = results["checks"]
    for v in c["light_twins"] + c["too_dark"]:
        print(f"FAIL {v}")
    lat = c["latency"]
    print(
        f"latency: {lat['listings']} listings p50 {lat['p50_ms']} ms p95 {lat['p95_ms']} ms (cold "
        f"{lat['cold_ms']} ms); {lat['big_listings']} listings p50 {lat['big_p50_ms']} ms "
        f"p95 {lat['big_p95_ms']} ms"
    )


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", choices=[*SETS, "all"], default="all")
    ap.add_argument("--no-report", action="store_true")
    a = ap.parse_args()
    sets = list(SETS) if a.set == "all" else [a.set]
    results = asyncio.run(run(sets, keep_report=a.no_report))
    show(results)
    return 0


if __name__ == "__main__":
    sys.exit(main())
