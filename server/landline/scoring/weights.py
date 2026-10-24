"""Feature weights from what the renter said (docs/SCORING.md §3).

A feature the renter gave no signal for weighs 0, so a constant can't pose as a
reason: no budget means no cost term, no resolved anchor or named area means no
location term, and parking, pets, laundry and the like count only once mentioned.
"""
from __future__ import annotations

import re
from datetime import date, datetime

from landline.contracts import Preferences

DEFAULTS = {
    "cost": 3.0, "location": 3.0, "size": 1.5, "transit": 1.0, "light": 1.0,
    "building_quality": 0.5, "value": 0.5, "move_in": 1.0,
}
PRIORITY_WEIGHTS = (4.0, 3.0, 2.0, 1.5, 1.0)   # the renter's stated order; later ones get the last value
MUST_HAVE_WEIGHT = 2.0                          # a must-have also ranks, at least this much
PET_WEIGHT = 2.0                                # the renter has pets
CAP = 5.0                                       # no weight goes above this, feedback included

# What a renter (or the Concierge) may call a feature. Keys are the feature names themselves.
SYNONYMS = {
    "cost": {"cost", "price", "budget", "rent", "cheap", "affordable"},
    "location": {"location", "area", "neighbourhood", "neighborhood", "commute", "close", "near"},
    "size": {"size", "space", "sqft", "square_feet", "big", "bigger", "spacious"},
    "transit": {"transit", "subway", "ttc", "metro", "train", "transport"},
    "light": {"light", "bright", "sunny", "sun", "natural_light", "sunlight"},
    "floor": {"floor", "high_floor", "view", "high"},
    "ground_floor": {"ground_floor", "low_floor", "no_stairs", "step_free", "accessible", "wheelchair"},
    "parking": {"parking", "car", "garage"},
    "pets": {"pets", "pet", "dog", "cat", "pet_friendly"},
    "laundry": {"laundry", "laundry_in_suite", "in_suite_laundry", "washer", "washer_dryer"},
    "ac": {"ac", "a/c", "air_conditioning", "aircon"},
    "dishwasher": {"dishwasher"},
    "outdoor_space": {"outdoor_space", "balcony", "terrace", "patio", "yard", "outdoor"},
    "building_quality": {"building_quality", "building", "quality", "well_kept"},
    "value": {"value", "deal", "bargain"},
    "move_in": {"move_in", "move_in_date", "availability", "date", "timing"},
}
_LOOKUP = {word: key for key, words in SYNONYMS.items() for word in words}
FEATURES = tuple(SYNONYMS)


def feature_key(word: str) -> str | None:
    """`"High floor"` → `"floor"`; None for a word that names no feature."""
    return _LOOKUP.get(re.sub(r"[\s\-]+", "_", word.strip().lower()))


def weights_for(prefs: Preferences, boosts: dict[str, float] | None = None,
                today: date | None = None) -> dict[str, float]:
    """Every feature's weight for these preferences. `boosts` are feedback changes (O-04), capped at 5."""
    w = {key: DEFAULTS.get(key, 0.0) for key in FEATURES}
    stated = []
    for word in prefs.priorities:
        key = feature_key(word)
        if key and key not in stated:
            stated.append(key)
    for i, key in enumerate(stated):
        w[key] = PRIORITY_WEIGHTS[min(i, len(PRIORITY_WEIGHTS) - 1)]
    for word in prefs.must_have:
        key = feature_key(word)
        if key:
            w[key] = max(w[key], MUST_HAVE_WEIGHT)
    if prefs.pets:
        w["pets"] = max(w["pets"], PET_WEIGHT)
    for key, extra in (boosts or {}).items():
        if key in w:
            w[key] = min(w[key] + extra, CAP)

    # No signal, no weight.
    if prefs.max_monthly is None:
        w["cost"] = 0.0
    if not any(a.lat is not None and a.lng is not None for a in prefs.anchors) and not prefs.areas:
        w["location"] = 0.0
    if move_in_date(prefs.move_in, today) is None:
        w["move_in"] = 0.0
    if not prefs.pets:
        w["pets"] = 0.0
    return {k: v for k, v in w.items() if v > 0}


def move_in_date(words: str | None, today: date | None = None) -> date | None:
    """The renter's move-in words as a date: ISO ("2026-12-01"), "December 1", "Dec 1", or "now"/"asap".
    Month and day mean the next such date. Anything else is None (unknown)."""
    if not words:
        return None
    today = today or date.today()
    text = words.strip().lower().rstrip(".")
    if text in {"now", "asap", "immediately", "today", "right away"}:
        return today
    try:
        return date.fromisoformat(text)
    except ValueError:
        pass
    text = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", text)
    for fmt in ("%B %d", "%b %d", "%d %B", "%d %b"):
        try:
            parsed = datetime.strptime(text, fmt).date()
        except ValueError:
            continue
        day = parsed.replace(year=today.year)
        return day if day >= today else day.replace(year=today.year + 1)
    return None
