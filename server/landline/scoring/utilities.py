"""Per-feature utilities, each 0–1 (docs/SCORING.md §2). None means the value is unknown."""
from __future__ import annotations

from datetime import date
from math import exp

# The size a renter expects for each bed count; a unit this size scores 0.5, one 1.5× as big scores 1.
TYPICAL_SQFT = {0: 450, 1: 650, 2: 900, 3: 1200, 4: 1500}


def clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return lo if x < lo else hi if x > hi else x


def cost(monthly_cost: int, budget: int) -> float:
    """1 up to 85% of the budget, 0.4 at the budget, 0 at the budget + 10% (the hard filter's edge)."""
    if monthly_cost <= 0.85 * budget:
        return 1.0
    if monthly_cost <= budget:
        return 1.0 - 0.6 * (monthly_cost - 0.85 * budget) / (0.15 * budget)
    return clamp(0.4 - 0.4 * (monthly_cost - budget) / (0.10 * budget))


def distance(d_km: float, decay_km: float) -> float:
    return exp(-d_km / decay_km)


def transit(walk_min: float | None) -> float | None:
    return None if walk_min is None else clamp(1 - (walk_min - 3) / 17)


def size(sqft: int | None, beds: float, desired: int | None = None) -> float | None:
    """0.5 at the typical size for the bed count, 1 at 1.5× it. After "too small" (O-04) `desired` takes the
    typical size's place on the same curve, so a bigger unit never falls behind a smaller one."""
    if sqft is None:
        return None
    typical = desired or TYPICAL_SQFT[min(int(beds), 4)]
    return clamp(0.5 + (sqft / typical - 1))


def high_floor(floor: int | None) -> float | None:
    return None if floor is None else clamp(floor / 15)


def ground_floor(floor: int | None, elevator: bool | None, step_free: bool | None) -> float | None:
    """For "ground floor" or "no stairs": 1 on floor 1 or below, or with an elevator or step-free access."""
    if step_free or elevator or (floor is not None and floor <= 1):
        return 1.0
    return None if floor is None else 0.0


def parking(value: str | None) -> float | None:
    return {"included": 1.0, "extra": 0.6, "none": 0.0}.get(value) if value else None


def species(renter_pets: list[str]) -> frozenset[str]:
    """`["a small dog"]` → {"dog"}. Anything that isn't a dog or a cat counts as "other"."""
    out = set()
    for pet in renter_pets:
        word = pet.lower()
        out.add("dog" if "dog" in word or "pupp" in word else "cat" if "cat" in word or "kitten" in word
                else "other")
    return frozenset(out)


def pets(value: str | None, renter: frozenset[str]) -> float | None:
    """Compatible 1, case by case 0.6, incompatible 0, unknown None."""
    if value is None:
        return None
    if value == "any":
        return 1.0
    if value == "case_by_case":
        return 0.6
    if value == "none":
        return 0.0
    allowed = {"cats_only": {"cat"}, "dogs_only": {"dog"}, "small_only": {"cat", "other"}}[value]
    return 1.0 if renter <= allowed else 0.0


def laundry(value: str | None) -> float | None:
    return {"in_suite": 1.0, "shared": 0.5, "none": 0.0}.get(value) if value else None


def present(value: bool | None) -> float | None:
    return None if value is None else (1.0 if value else 0.0)


def move_in(available_from: date | None, wanted: date) -> float | None:
    """1 if the place is free within 14 days of the move-in date (either way), falling to 0 at 60 days."""
    if available_from is None:
        return None
    off = abs((available_from - wanted).days)
    return 1.0 if off <= 14 else clamp(1 - (off - 14) / 46)
