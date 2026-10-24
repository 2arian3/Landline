"""What calls heard, as the ranker's overrides (docs/SCORING.md §4): a heard value replaces the listing's and
counts as call evidence for the hard filters, so a cats-only place drops for a dog owner.

Each fact is read again from its own quote with the Auditor's rules (agents/auditor/values.py): the outcome
keeps the words the renter reads, and the quote decides the value. Money is the quote's own numbers, and
`monthly_cost` is computed here, never taken from a model (hard rule 2): rent plus the mandatory monthly
fees (docs/DATA.md), where a heard parking price counts when its quote makes it required ("it comes with
the unit") or the renter must have parking.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any

from landline.agents.auditor.values import Refused, Value, call_day, fee_name, free_from, settle
from landline.contracts import Fact, Listing
from landline.db.catalogue import Catalogue
from landline.orchestrator.state import SessionState
from landline.scoring.weights import feature_key

# The Auditor's categories → the matrix's (scoring/features.py). Pets not named here keep their name.
PETS = {"allowed": "any", "no_pets": "none"}
LAUNDRY = {"in_unit": "in_suite", "in_building": "shared", "none": "none"}
PARKING = {"included": "included", "available": "extra", "none": "none"}


def from_calls(state: SessionState, catalogue: Catalogue,
               today: date | None = None) -> dict[str, dict[str, Any]]:
    """`{listing_id: {column: value}}` from every outcome in the session, for `rank(overrides=...)`."""
    today = today or date.today()
    must = state.preferences.must_have
    needs_parking = any((feature_key(m) or m.strip().lower()) == "parking" for m in must)
    out: dict[str, dict[str, Any]] = {}
    for lid, outcome in state.outcomes.items():
        listing = catalogue.by_id.get(lid)
        if listing is None:
            continue
        heard, quotes, fees = _settled(outcome.facts)
        cols: dict[str, Any] = {}
        if v := heard.get("pets"):
            cols["pets"] = PETS.get(v.value, v.value)
        if v := heard.get("laundry"):
            cols["laundry"] = LAUNDRY[v.value]
        if v := heard.get("parking"):
            cols["parking"] = PARKING[v.value]
        if (v := heard.get("available_from")) and (day := _day(v.value, _said_on(outcome.facts, today))):
            cols["available_from"] = day
        cost = _monthly_cost(listing, heard, quotes, fees, needs_parking)
        if cost is not None and cost != listing.monthly_cost:
            cols["monthly_cost"] = cost
        if cols:
            out[lid] = cols
    return out


def _settled(facts: list[Fact]) -> tuple[dict[str, Value], dict[str, str], list[Value]]:
    """The facts' values by field (the last one heard wins) with their quotes, and every fee. A fact its
    quote no longer supports is skipped."""
    heard: dict[str, Value] = {}
    quotes: dict[str, str] = {}
    fees: list[Value] = []
    for f in facts:
        try:
            if f.field == "fees":
                fees.append(settle("fees", f.value, f.quote, **_fee_hints(f.value)))
            else:
                on = call_day(f.at)   # the Auditor wrote the move-in day in words, from code (#157)
                parts = free_from(f.value, on) if f.field == "available_from" else None
                heard[f.field], quotes[f.field] = settle(f.field, f.value, f.quote,
                                                         today=on if f.at else None, parts=parts), f.quote
        except Refused:
            continue
    return heard, quotes, fees


def _fee_hints(display: str) -> dict[str, Any]:
    """What the Auditor's own display of a fee says ("amenity $40 a month, required"): period, whether it's
    required and the count, so reading it again keeps what the Auditor decided."""
    hints: dict[str, Any] = {"period": "one_time" if "one time" in display else "monthly"}
    if display.endswith(", required"):
        hints["mandatory"] = True
    elif display.endswith(", optional"):
        hints["mandatory"] = False
    if m := re.search(r"× (\d+)", display):
        hints["count"] = int(m.group(1))
    return hints


def _monthly_cost(listing: Listing, heard: dict[str, Value], quotes: dict[str, str], fees: list[Value],
                  needs_parking: bool) -> int | None:
    """None when the call heard nothing that moves the cost."""
    parking = heard.get("parking_cost")
    if "rent" not in heard and not fees and parking is None:
        return None
    rent = heard["rent"].amount if "rent" in heard else listing.rent
    monthly = {fee_name(f.name): f.amount for f in listing.fees if f.mandatory and f.period == "monthly"}
    for v in fees:
        if v.period != "monthly":
            continue
        if v.mandatory:
            monthly[v.name or "other"] = (v.amount or 0) * v.count
        elif v.mandatory is False:
            monthly.pop(v.name or "other", None)
    if parking is not None and parking.amount and "parking" not in monthly:
        try:   # the Auditor's fee rules decide from the quote whether the spot is required
            required = settle("fees", parking.display, quotes["parking_cost"], name="parking",
                              period="monthly").mandatory
        except Refused:
            required = None
        if required or needs_parking:
            monthly["parking"] = parking.amount   # one spot
    return (rent or 0) + sum(monthly.values())


def _said_on(facts: list[Fact], today: date) -> date:
    """The day the move-in date was heard, which its key counts from, so "tomorrow" keeps meaning the day
    after that call (the same day the Narrator names); today when the fact has no time."""
    at = next((f.at for f in reversed(facts) if f.field == "available_from" and f.at), None)
    return call_day(at) if at else today


def _day(key: str, today: date) -> date | None:
    """The Auditor's date key ("now", "11-01") as the next such day from `today`, the call's day."""
    if key == "now":
        return today
    m = re.fullmatch(r"(\d{2})-(\d{2})", key)
    if not m:
        return None
    try:
        day = date(today.year, int(m.group(1)), int(m.group(2)))
        return day if day >= today else date(today.year + 1, day.month, day.day)
    except ValueError:
        return None
