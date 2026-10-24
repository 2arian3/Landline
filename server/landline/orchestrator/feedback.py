"""Feedback moves the ranking (O-04, docs/SCORING.md §3). A pure function of the session's reactions, so the
board can be rebuilt from the stored state at any time and a changed mind replaces the old reaction.

| said            | attribute | effect                                                       |
|-----------------|-----------|--------------------------------------------------------------|
| "too dark"      | light     | light +1, so darker units (a basement is ≤ 0.25) sink         |
| "too far"       | location  | location +1, and distance counts sooner (2.5 → 1.8 km)       |
| "too small"     | size      | size +1, and the size wanted is that unit's × 1.15           |
| "too expensive" | cost      | cost +1; the budget stays as said                            |
| "I need parking"| parking   | parking becomes a must-have                                  |
| "love this one" | —         | its two strongest features get +0.5                          |

A dislike or a reject with a reason does the same; a reject also takes the place off the board. Weights are
capped at 5 (scoring/weights.py). The Concierge passes the attribute; a tap on the page passes only the
reason, so the reason's words decide it.
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from landline.contracts import Preferences
from landline.db.catalogue import Catalogue
from landline.orchestrator.state import SessionState
from landline.scoring import FeatureMatrix, rank, weights_for

STEP, LOVE = 1.0, 0.5
TIGHTER_KM = 1.8
SIZE_UP = 1.15

# A reason in the renter's words, when no attribute came with it.
_REASONS = (
    ("light", re.compile(r"\b(dark|gloomy|dim|basement|no light|not bright|no sun)\b", re.I)),
    ("location", re.compile(r"\b(far|commute|distance|out of the way)\b", re.I)),
    ("size", re.compile(r"\b(small|cramped|tiny|tight|no space)\b", re.I)),
    ("cost", re.compile(r"\b(expensive|pricey|costly|too much|over budget)\b", re.I)),
    ("parking", re.compile(r"\bparking\b", re.I)),
)


@dataclass
class Adjustments:
    boosts: dict[str, float] = field(default_factory=dict)
    decay_km: float | None = None
    desired_sqft: int | None = None
    must_have: list[str] = field(default_factory=list)
    rejected: set[str] = field(default_factory=set)
    liked: list[str] = field(default_factory=list)


def attribute_of(attribute: str | None, reason: str | None) -> str | None:
    if attribute:
        return attribute
    for name, pattern in _REASONS:
        if reason and pattern.search(reason):
            return name
    return None


def adjustments(state: SessionState, catalogue: Catalogue) -> Adjustments:
    adj = Adjustments()
    boosts: dict[str, float] = defaultdict(float)
    for lid, said in state.reactions.items():
        if said.reaction == "like":
            adj.liked.append(lid)
            continue
        if said.reaction == "reject":
            adj.rejected.add(lid)
        attr = attribute_of(said.attribute, said.reason)
        if attr == "parking":
            adj.must_have.append("parking")
        elif attr:
            boosts[attr] += STEP
        if attr == "location":
            adj.decay_km = TIGHTER_KM
        if attr == "size" and (x := catalogue.by_id.get(lid)) and x.sqft:
            adj.desired_sqft = max(adj.desired_sqft or 0, round(x.sqft * SIZE_UP))
    adj.boosts = dict(boosts)
    return adj


def ranked(state: SessionState, catalogue: Catalogue, matrix: FeatureMatrix, status: dict,
           overrides: dict[str, dict[str, Any]] | None = None):
    """The Matcher's ranking with the feedback applied: the boosts, then +0.5 to each loved place's two
    strongest features, ranked again. `overrides` are what calls heard (orchestrator/overrides.py)."""
    adj = adjustments(state, catalogue)
    prefs: Preferences = state.preferences
    if adj.must_have:
        must = list(dict.fromkeys([*prefs.must_have, *adj.must_have]))
        prefs = prefs.model_copy(update={"must_have": must})
    extra = {} if adj.decay_km is None else {"decay_km": adj.decay_km}

    def run(boosts: dict[str, float]):
        return rank(matrix, prefs, weights=weights_for(prefs, boosts), previous=state.board, status=status,
                    rejected=adj.rejected, desired_sqft=adj.desired_sqft, overrides=overrides, **extra)

    ranking = run(adj.boosts)
    loved = [lid for lid in adj.liked if lid in ranking.scores]
    if not loved:
        return ranking
    boosts = defaultdict(float, adj.boosts)
    for lid in loved:
        top = sorted(ranking.explain(lid).contributions.items(), key=lambda kv: -kv[1])[:2]
        for f, _ in top:
            boosts[f] += LOVE
    return run(dict(boosts))
