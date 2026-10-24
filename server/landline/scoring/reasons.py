"""The words a card shows for a feature (docs/SCORING.md §5): plain facts, never a score or a %.

`phrase()` is only called for a value that is known, so every reason has something
behind it. It returns None when there is nothing honest to say.
"""
from __future__ import annotations

from typing import Any


def phrase(feature: str, value: float, row: dict[str, Any]) -> str | None:
    good = value >= 0.5
    say = _PHRASES.get(feature)
    return say(good, row) if say else None


def _cost(good: bool, r: dict[str, Any]) -> str | None:
    budget, cost = r["budget"], r["monthly_cost"]
    if budget is None:
        return None
    if cost < budget and not good:   # under budget but dear after "too expensive": no honest watch-out
        return None
    if cost <= budget:
        return f"${budget - cost:,} under budget" if budget > cost else "right at your budget"
    return f"${cost - budget:,} over budget"


def _location(good: bool, r: dict[str, Any]) -> str | None:
    if r.get("anchor_km") is not None:
        km = r["anchor_km"]
        return f"right by {r['anchor']}" if km < 0.1 else f"{km:.1f} km from {r['anchor']}"
    return f"in {r['neighbourhood']}" if good and r["neighbourhood"] else None


def to_station(minutes: float | None, station: str | None) -> str | None:
    """The nearest subway station, for the agent to say ("4 min walk to Wellesley station", "next to Union
    station"); None when the listing has none in the data, so nothing is guessed (#166)."""
    if not station or minutes is None:
        return None
    return f"next to {station} station" if minutes < 1 else f"{round(minutes)} min walk to {station} station"


def _transit(good: bool, r: dict[str, Any]) -> str:
    if r["transit_min"] < 1:   # rounding would say "0 min walk"
        return f"next to {r['station']} station" if r["station"] else "next to the subway"
    return f"{round(r['transit_min'])} min walk to {r['station'] or 'the subway'}"


def _size(good: bool, r: dict[str, Any]) -> str:
    return f"{r['sqft']:,} sq ft" if good else f"only {r['sqft']:,} sq ft"


def _light(good: bool, r: dict[str, Any]) -> str:
    return "bright (estimated)" if good else "dim (estimated)"


def _floor(good: bool, r: dict[str, Any]) -> str:
    floor = r["floor"]
    return "ground floor" if floor == 0 else "basement" if floor < 0 else f"floor {floor}"


def _ground_floor(good: bool, r: dict[str, Any]) -> str | None:
    if good:
        return "elevator" if r["elevator"] and (r["floor"] or 0) > 1 else "no stairs"
    return f"floor {r['floor']}, no elevator listed" if r["floor"] is not None else None


def _parking(good: bool, r: dict[str, Any]) -> str:
    if r["parking"] == "included":
        return "parking included"
    if r["parking"] == "extra":
        return f"parking ${r['parking_cost']:,} a month" if r["parking_cost"] else "parking available"
    return "no parking"


def _pets(good: bool, r: dict[str, Any]) -> str:
    only = next(iter(r["species"])) if len(r["species"]) == 1 else None
    kind = {"dog": "dogs", "cat": "cats"}.get(only, "pets")
    if r["pets"] == "case_by_case":
        return "pets case by case"
    return f"{kind} allowed" if good else f"no {kind}"


def _laundry(good: bool, r: dict[str, Any]) -> str:
    return "in-suite laundry" if good else "no laundry"


def _present(name: str, missing: str):
    return lambda good, r: name if good else missing


def _move_in(good: bool, r: dict[str, Any]) -> str:
    day = r["available_from"]
    return "available when you move" if good else f"available {day:%b} {day.day}"


_PHRASES = {
    "cost": _cost, "location": _location, "transit": _transit, "size": _size, "light": _light,
    "floor": _floor, "ground_floor": _ground_floor, "parking": _parking, "pets": _pets,
    "laundry": _laundry, "ac": _present("air conditioning", "no air conditioning"),
    "dishwasher": _present("dishwasher", "no dishwasher"),
    "outdoor_space": _present("balcony or patio", "no outdoor space"), "move_in": _move_in,
}
