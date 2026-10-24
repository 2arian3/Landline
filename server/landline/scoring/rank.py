"""The Matcher: hard filters, a weighted sum of utilities, then adjustments for calls (docs/SCORING.md).

`rank()` orders every listing that passes the filters. Only the cards on the board
need words, so reasons are built on demand by `Ranking.explain()`. The same inputs
always give the same order; ties keep the previous order, then go by listing id.

Speed: a utility column depends only on the catalogue and a few preference values
(the budget, the anchors), so each is computed once over the whole matrix and kept
there. A re-rank reads cached columns; only rows that calls overrode are recomputed.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from functools import cached_property
from math import inf
from typing import Any

from landline.contracts import CardStatus, Listing, Preferences
from landline.scoring import utilities as u
from landline.scoring.features import FeatureMatrix
from landline.scoring.reasons import phrase
from landline.scoring.weights import feature_key, move_in_date, weights_for

UNKNOWN = 0.5            # an unknown value scores neutral and is never a reason
CITED = 0.1              # a reason needs u ≥ 0.6, a watch-out u ≤ 0.4
VERIFIED_BONUS = 7.0     # on the 100-point scale
SUSPECT_PENALTY = 25.0
LOCATION_DECAY_KM = 2.5  # feedback "too far" tightens this to 1.8 (O-04)
NEXT_DOOR = 0.6          # a neighbourhood touching a named one (D-05)
AREA_DECAY_KM = 2.0      # beyond that, from the named neighbourhood's middle
BUDGET_SLACK = 1.10      # the budget filter, and where the cost utility reaches 0
CACHE_LIMIT = 256        # utility columns kept per matrix


@dataclass(frozen=True)
class Ranked:
    listing_id: str
    rank: int
    score: float
    contributions: dict[str, float]   # w × u per weighted feature, with unknown values at 0.5
    reasons: list[str]                # up to three, strongest first
    watch_out: str | None


class Ranking:
    """Every listing that passed the filters, best first."""

    def __init__(self, order: list[str], ids: list[str], score: list[float], ctx: _Context):
        self.order = order
        self._ids, self._score = ids, score
        self._ctx = ctx

    def __len__(self) -> int:
        return len(self.order)

    @cached_property
    def scores(self) -> dict[str, float]:
        return dict(zip(self._ids, self._score, strict=True))

    @cached_property
    def _rank(self) -> dict[str, int]:
        return {lid: i for i, lid in enumerate(self.order, start=1)}

    def top(self, k: int) -> list[Ranked]:
        return [self.explain(lid) for lid in self.order[:k]]

    def explain(self, listing_id: str) -> Ranked:
        ctx = self._ctx
        i = ctx.matrix.index[listing_id]
        values = {f: ctx.utilities(f, [i])[0] for f in ctx.weights}
        contributions = {f: w * (UNKNOWN if values[f] is None else values[f]) for f, w in ctx.weights.items()}
        known = {f: ctx.weights[f] * (v - UNKNOWN) for f, v in values.items() if v is not None}
        good = sorted((f for f in known if values[f] - UNKNOWN >= CITED), key=lambda f: -known[f])
        bad = sorted((f for f in known if values[f] - UNKNOWN <= -CITED), key=lambda f: known[f])
        reasons = [p for f in good if (p := phrase(f, values[f], ctx.row(f, i)))][:3]
        watch = next((p for f in bad if (p := phrase(f, values[f], ctx.row(f, i)))), None)
        return Ranked(listing_id, self._rank.get(listing_id, 0), self.scores[listing_id], contributions,
                      reasons, watch)


def rank(matrix: FeatureMatrix, prefs: Preferences, *, weights: dict[str, float] | None = None,
         previous: list[str] | tuple[str, ...] = (), rejected: set[str] | frozenset[str] = frozenset(),
         status: dict[str, CardStatus] | None = None, overrides: dict[str, dict[str, Any]] | None = None,
         desired_sqft: int | None = None, decay_km: float = LOCATION_DECAY_KM, today: date | None = None,
         keep_all: bool = False) -> Ranking:
    """Rank the catalogue for one renter.

    `previous` is the last board order (for ties), `rejected` the listings the renter turned down,
    `status` each card's call status, and `overrides` values calls found, by matrix column name
    (`{"livrent:1": {"monthly_cost": 2180}}`); they replace the listing's and count as call evidence.
    `desired_sqft` and `decay_km` are where feedback (O-04) tightens size and distance. `keep_all`
    skips the hard filters, to explain one card whatever its fit.
    """
    status = status or {}
    weights = weights if weights is not None else weights_for(prefs, today=today)
    ctx = _Context(matrix, prefs, weights, overrides or {}, desired_sqft, decay_km, today)
    rows = list(range(len(matrix))) if keep_all else ctx.passing(rejected, status)

    # Weighted sum over the whole matrix from cached columns, then the passing rows once. A column that
    # is the same for every listing (no data yet) adds a constant instead.
    total = sum(weights.values())
    acc = [0.0] * len(matrix)
    constant = 0.0
    for f, w in weights.items():
        _, scored, same = ctx._cached(f)
        if same is None:
            acc = [a + w * v for a, v in zip(acc, scored, strict=True)]
        else:
            constant += w * same
    for i in ctx.overridden:
        acc[i] = sum(w * ctx.scored(f, [i])[0] for f, w in weights.items()) - constant
    k = 100 / total if total else 0.0
    ids = [matrix.ids[i] for i in rows]
    score = [(acc[i] + constant) * k for i in rows] if total else [50.0] * len(rows)
    if status:
        for j, lid in enumerate(ids):
            st = status.get(lid)
            if st in ("verified", "booked"):
                score[j] += VERIFIED_BONUS
            elif st == "suspect":
                score[j] -= SUSPECT_PENALTY

    # Rows are in listing-id order; put the previous board first so a stable sort on score alone breaks
    # ties by previous rank, then by id.
    seq = list(range(len(ids)))
    if previous:
        pos = {lid: j for j, lid in enumerate(ids)}
        first = [pos[lid] for lid in dict.fromkeys(previous) if lid in pos]
        seen = set(first)
        seq = first + [j for j in seq if j not in seen]
    neg = [-s for s in score]
    order = [ids[j] for j in sorted(seq, key=neg.__getitem__)]
    if status:
        booked = [lid for lid in order if status.get(lid) == "booked"]
        if booked:
            order = booked + [lid for lid in order if status.get(lid) != "booked"]
        if order and status.get(order[0]) == "suspect":
            first = next((lid for lid in order if status.get(lid) != "suspect"), None)
            if first:
                order.remove(first)
                order.insert(0, first)
    return Ranking(order, ids, score, ctx)


def explain(listing: Listing, prefs: Preferences, weights: dict[str, float] | None = None) -> Ranked:
    """Reasons for one listing outside the catalogue (say, after a call added a fee). Rank is 1."""
    matrix = FeatureMatrix.build([listing])
    return rank(matrix, prefs, weights=weights, keep_all=True).explain(listing.listing_id)


class _Context:
    """One ranking's inputs, with the columns calls have overridden."""

    def __init__(self, matrix: FeatureMatrix, prefs: Preferences, weights: dict[str, float],
                 overrides: dict[str, dict[str, Any]], desired_sqft: int | None, decay_km: float,
                 today: date | None):
        self.matrix, self.prefs, self.weights = matrix, prefs, weights
        self.overrides = {lid: vals for lid, vals in overrides.items() if lid in matrix.index}
        self.overridden = {matrix.index[lid] for lid in self.overrides}
        self.desired_sqft, self.decay_km = desired_sqft, decay_km
        self.budget = prefs.max_monthly
        self.species = u.species(prefs.pets)
        self.wanted = move_in_date(prefs.move_in, today)
        resolved = [a for a in prefs.anchors if a.lat is not None and a.lng is not None]
        self.anchors = [(a.label, matrix.distances_km(a.lat, a.lng)) for a in resolved]
        self.anchor_key = tuple((round(a.lat, 5), round(a.lng, 5)) for a in resolved)
        self.areas = frozenset(a.strip().lower() for a in prefs.areas)
        self._cols: dict[str, list] = {}

    # Columns

    def col(self, name: str) -> list:
        """A matrix column with any call overrides applied."""
        if name not in self._cols:
            base = getattr(self.matrix, name)
            index = self.matrix.index
            patches = [(index[lid], vals[name]) for lid, vals in self.overrides.items() if name in vals]
            if patches:
                base = list(base)
                for i, value in patches:
                    base[i] = value
            self._cols[name] = base
        return self._cols[name]

    # Filters (docs/SCORING.md §1)

    def passing(self, rejected: set[str] | frozenset[str], status: dict[str, CardStatus]) -> list[int]:
        p, ids = self.prefs, self.matrix.ids
        cap = p.max_monthly * BUDGET_SLACK if p.max_monthly is not None else inf
        floor_beds = p.min_beds if p.min_beds is not None else -inf
        must = {feature_key(x) or x.strip().lower() for x in p.must_have}
        cost, beds, kind = self.col("monthly_cost"), self.col("beds"), self.col("unit_type")
        rooms_ok = "room" in must
        rows = [i for i in range(len(ids))
                if cost[i] <= cap and beds[i] >= floor_beds and (rooms_ok or kind[i] != "room")]

        drop = set(rejected) | {lid for lid, st in status.items() if st == "dead"}
        if drop:
            rows = [i for i in rows if ids[i] not in drop]
        # Excluding on pets or a must-have needs a call or the City record, so only rows with that
        # evidence are checked. The listing's own words only rank it; the gap becomes a question.
        refused: dict[str, Callable[[int], bool]] = {}
        if self.species:
            pets, renter = self.col("pets"), self.species
            refused["pets"] = lambda i: u.pets(pets[i], renter) == 0.0
        parking, laundry, ac = self.col("parking"), self.col("laundry"), self.col("ac")
        checks = {
            "parking": lambda i: parking[i] == "none",
            "laundry": lambda i: laundry[i] in ("shared", "none"),
            "ac": lambda i: ac[i] is False,
        }
        refused.update({key: checks[key] for key in must & checks.keys()})
        out = set()
        for key, refuses in refused.items():
            evidence = self.matrix.strong_rows.get(key, frozenset()) | {
                self.matrix.index[lid] for lid, vals in self.overrides.items() if key in vals}
            out |= {i for i in evidence if refuses(i)}
        return [i for i in rows if i not in out] if out else rows

    # Utilities (docs/SCORING.md §2)

    def utilities(self, f: str, rows: list[int]) -> list[float | None]:
        """Feature `f`'s utility for each row; None where the value is unknown."""
        full = self._cached(f)[0]
        out = [full[i] for i in rows]
        return self._patch(f, rows, out, scored=False)

    def scored(self, f: str, rows: list[int]) -> list[float]:
        """As `utilities`, with unknown values at the neutral 0.5."""
        full = self._cached(f)[1]
        out = [full[i] for i in rows]
        return self._patch(f, rows, out, scored=True)

    def _patch(self, f: str, rows: list[int], out: list, scored: bool) -> list:
        if self.overridden:
            for pos, i in enumerate(rows):
                if i in self.overridden:
                    v = self._compute(f, [i], self.col)[0]
                    out[pos] = UNKNOWN if scored and v is None else v
        return out

    def _cached(self, f: str) -> tuple[list[float | None], list[float], float | None]:
        """(utilities, utilities with unknown at 0.5, the value if every row has the same one)."""
        key = (f, *self._params(f))
        cache = self.matrix._utilities
        if key not in cache:
            if len(cache) >= CACHE_LIMIT:
                cache.clear()
            values = self._compute(f, range(len(self.matrix)), lambda name: getattr(self.matrix, name))
            scored = [UNKNOWN if v is None else v for v in values]
            distinct = set(scored)
            cache[key] = (values, scored, distinct.pop() if len(distinct) == 1 else None)
        return cache[key]

    def _params(self, f: str) -> tuple:
        """What besides the catalogue feature `f` depends on: the cache key."""
        return {
            "cost": (self.budget,),
            "location": (self.anchor_key, self.areas, self.decay_km),
            "size": (self.desired_sqft,),
            "pets": (self.species,),
            "move_in": (self.wanted,),
        }.get(f, ())

    def _compute(self, f: str, rows, col: Callable[[str], list]) -> list[float | None]:
        if f == "cost":
            c, b = col("monthly_cost"), self.budget
            return [u.cost(c[i], b) for i in rows] if b else [None] * len(rows)
        if f == "location":
            return self._location(rows)
        if f == "transit":
            t = col("transit_min")
            return [u.transit(t[i]) for i in rows]
        if f == "size":
            s, b, d = col("sqft"), col("beds"), self.desired_sqft
            return [u.size(s[i], b[i], d) for i in rows]
        if f == "light":
            light = col("light")
            return [light[i] for i in rows]
        if f == "floor":
            fl = col("floor")
            return [u.high_floor(fl[i]) for i in rows]
        if f == "ground_floor":
            fl, el, sf = col("floor"), col("elevator"), col("step_free")
            return [u.ground_floor(fl[i], el[i], sf[i]) for i in rows]
        if f == "parking":
            p = col("parking")
            return [u.parking(p[i]) for i in rows]
        if f == "pets":
            p, renter = col("pets"), self.species
            return [u.pets(p[i], renter) for i in rows]
        if f == "laundry":
            la = col("laundry")
            return [u.laundry(la[i]) for i in rows]
        if f in ("ac", "dishwasher", "outdoor_space"):
            c = col(f)
            return [u.present(c[i]) for i in rows]
        if f == "move_in" and self.wanted:
            a, wanted = col("available_from"), self.wanted
            return [u.move_in(a[i], wanted) for i in rows]
        return [None] * len(rows)   # building_quality and value have no data until the City join (D-05)

    def _location(self, rows) -> list[float | None]:
        """The mean of the known parts: each resolved anchor's exp(-d / decay), and for named areas 1 inside,
        0.6 in a neighbourhood next to one, and exp(-d / 2) from the nearest one's middle beyond that."""
        parts = [[None if d[i] is None else u.distance(d[i], self.decay_km) for i in rows]
                 for _, d in self.anchors]
        if self.areas:
            parts.append(self._areas(rows))
        if not parts:
            return [None] * len(rows)
        if len(parts) == 1:
            return parts[0]
        out: list[float | None] = []
        for values in zip(*parts, strict=True):
            known = [v for v in values if v is not None]
            out.append(sum(known) / len(known) if known else None)
        return out

    # Words (docs/SCORING.md §5)

    def _areas(self, rows) -> list[float | None]:
        hood = self.matrix.neighbourhood
        named = self.matrix.named(self.areas)
        if not named:   # no neighbourhood data, or a name the City doesn't use: only an exact match counts
            return [1.0 if hood[i] is not None and hood[i] in self.areas else None for i in rows]
        inside = {h.name.lower() for h in named}
        hoods = self.matrix.hoods
        nextdoor = {hoods[a].name.lower() for h in named for a in h.adjacent if a in hoods} - inside
        dists = [self.matrix.distances_km(h.lat, h.lng) for h in named]
        out: list[float | None] = []
        for i in rows:
            if hood[i] in inside:
                out.append(1.0)
            elif hood[i] in nextdoor:
                out.append(NEXT_DOOR)
            else:
                known = [d[i] for d in dists if d[i] is not None]
                out.append(min(NEXT_DOOR, u.distance(min(known), AREA_DECAY_KM)) if known else None)
        return out

    def row(self, f: str, i: int) -> dict[str, Any]:
        """What a phrase needs to describe feature `f` for listing `i`."""
        x = self.matrix.listings[i]
        out: dict[str, Any] = {
            "budget": self.budget, "monthly_cost": self.col("monthly_cost")[i], "sqft": self.col("sqft")[i],
            "beds": x.beds, "floor": self.col("floor")[i], "parking": self.col("parking")[i],
            "parking_cost": x.parking_cost, "pets": self.col("pets")[i], "species": self.species,
            "laundry": self.col("laundry")[i], "transit_min": self.col("transit_min")[i],
            "station": x.transit_station, "available_from": self.col("available_from")[i],
            "elevator": self.col("elevator")[i], "neighbourhood": x.neighbourhood,
        }
        if f == "location" and self.anchors:
            label, d = min(((lab, dist[i]) for lab, dist in self.anchors if dist[i] is not None),
                           key=lambda t: t[1], default=(None, None))
            out.update(anchor=label, anchor_km=d)
        return out
