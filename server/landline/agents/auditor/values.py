"""A heard value in one fixed form per field, decided in code from the words.

The Caller's `record_fact` and the transcript extractor both hand over words: a field, a value and the
landlord's quote. This module turns them into something comparable with the listing and with the eval's
gold facts, and refuses a value its own quote contradicts ("pets: allowed" quoted from "no pets"). Money
always comes from the numbers in the quote (`numbers.amounts`), never from the model; a per-spot price and a
count stay apart, so "two spots at 180" is $180 a spot, never $2 or $182 (PORTING bug #12).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from landline.agents.auditor.numbers import amounts

MONEY_FIELDS = {"rent", "parking_cost", "fees"}
CATEGORIES = {
    "available": ("yes", "no"),
    "pets": ("allowed", "cats_only", "dogs_only", "small_only", "case_by_case", "no_pets"),
    "laundry": ("in_unit", "in_building", "none"),
    "parking": ("included", "available", "none"),
    "furnished": ("yes", "no"),
    "ac": ("yes", "no"),
    "elevator": ("yes", "no"),
    "step_free": ("yes", "no"),
}
UTILITIES = {"heat": r"heat|heating|暖气", "water": r"water|水费|水", "hydro": r"hydro|electric|电费|电",
             "internet": r"internet|wi-?fi|网", "gas": r"\bgas\b|燃气|煤气"}
FEE_NAMES = {   # how a fee is said → the name it's compared by
    "parking": r"parking|spot|space|garage|车位|停车", "locker": r"locker|storage|储物",
    "key_deposit": r"key|fob|钥匙", "pet_fee": r"pet fee|pet deposit|宠物",   # before plain deposit
    "deposit": r"deposit|first and last|last month|押金|定金",
    "utilities": r"utilit|hydro|electric|water|heat|水电", "internet": r"internet|wi-?fi|网",
    "amenity": r"amenity|gym|maintenance|condo fee|管理费",
    "cleaning": r"clean", "application": r"application|background|credit check|申请",
}
# A clause that puts a utility on the renter: "hydro is on you", "water and hydro are extra".
_NOT_INCLUDED = re.compile(r"extra|on you|not included|isn'?t included|aren'?t included|separate|"
                           r"you pay|tenant pays|on top|另付|自己付|不包", re.I)
_CLAUSE = re.compile(r"[,.;!?，。；]|\bbut\b|\bwhile\b")
_EACH = re.compile(r"\beach\b|\bper (spot|space|car|one|unit|locker)\b|\ba (spot|space|piece)\b|apiece|"
                   r"每个|每位|一个", re.I)
_COUNTED = re.compile(r"(\w+|\d+)\s+(parking )?(spots|spaces|lockers|cars|个车位|个)", re.I)
_ONE_TIME = re.compile(r"one[- ]time|deposit|up ?front|once|押金|一次性|application|key", re.I)
_OPTIONAL = re.compile(r"optional|if you (want|need)|extra|available for|you can (rent|get|add)|"
                       r"add[- ]on|on top|additional|可选|如果你需要|另外", re.I)
_MANDATORY = re.compile(r"mandatory|required|have to|must|included in|comes with|everyone pays|必须", re.I)


@dataclass(frozen=True)
class Value:
    field: str
    value: str                 # the fixed form: "yes", "cats_only", "180", "parking:180:monthly"
    display: str               # what the renter reads: "$180 a month per spot (2 spots)"
    amount: int | None = None  # per unit, CAD, from the quote's own numbers
    count: int = 1
    period: str | None = None  # monthly | one_time
    mandatory: bool | None = None
    name: str | None = None    # a fee's name


class Refused(Exception):
    """The value can't be read from its quote, or the quote says otherwise."""


def settle(field: str, value: str, quote: str, *, amount: float | None = None, count: int | None = None,
           each: bool | None = None, period: str | None = None, mandatory: bool | None = None,
           name: str | None = None) -> Value:
    """`value` and the structured hints are claims; the quote decides. Raises `Refused`."""
    if field in MONEY_FIELDS:
        return _money(field, value, quote, amount, count, each, period, mandatory, name)
    if field in CATEGORIES:
        said = category(field, quote)
        exact = _category_word(field, value)   # a category named outright ("allowed") is a claim to check
        if exact and said and exact != said:
            raise Refused(f"the quote says {said}, not {exact}")
        got = said or exact or category(field, value)   # the quote decides over a keyword in the value
        if got is None:
            raise Refused(f"can't tell {field} from the words")
        return Value(field, got, _DISPLAY.get((field, got), got.replace("_", " ")))
    if field == "utilities_included":
        quoted = {u for clause in _CLAUSE.split(quote) if not _NOT_INCLUDED.search(clause)
                  for u, pat in UTILITIES.items() if re.search(pat, clause, re.I)}
        claimed = {u for u, pat in UTILITIES.items() if re.search(pat, value, re.I)} or quoted
        got = sorted(claimed & quoted)
        if not got:
            raise Refused("no utility named in the quote")
        return Value(field, ",".join(got), ", ".join(got) + " included")
    if field in ("lease_months", "sqft", "floor"):
        nums = amounts(quote)
        if field == "lease_months":
            years = re.search(r"(\w+)?\s*(year|年)", quote, re.I)
            if years:   # "one year lease, then month to month" is a one-year lease
                n = (amounts(years.group(1) or "") or [1])[0]
                return Value(field, str(12 * n), f"{12 * n} months")
            if re.search(r"month[- ]to[- ]month|按月", quote, re.I):
                return Value(field, "0", "month to month")
        if not nums:
            raise Refused("no number in the quote")
        n = nums[0] if amount is None else round(amount)
        if n not in nums:
            raise Refused(f"{n} isn't in the quote")
        unit = {"lease_months": " months", "sqft": " sq ft", "floor": ""}[field]
        return Value(field, str(n), f"{n:,}{unit}" if field != "floor" else f"floor {n}")
    if field == "available_from":
        return Value(field, _date_key(quote) or _date_key(value) or value.strip().lower(), value.strip())
    return Value(field, value.strip(), value.strip())   # light, outdoor_space, other: words, kept as said


def category(field: str, text: str) -> str | None:
    """The category the words say, by keyword, or None when they don't say."""
    t = text.lower().replace("’", "'")
    rules = _RULES.get(field, ())
    for cat, pattern in rules:
        if re.search(pattern, t, re.I):
            return cat
    return None


def fee_name(text: str) -> str:
    t = text.lower()
    for name, pattern in FEE_NAMES.items():
        if re.search(pattern, t, re.I):
            return name
    return "other"


def _money(field: str, value: str, quote: str, amount: float | None, count: int | None, each: bool | None,
           period: str | None, mandatory: bool | None, name: str | None) -> Value:
    nums = amounts(quote)
    if not nums:
        raise Refused("no amount in the quote")
    counted = _count(quote)
    if amount is not None:
        amt = round(amount)
        if amt not in nums:
            raise Refused(f"${amt} isn't in the quote")
    else:
        candidates = [n for n in nums if n >= 10 and n != counted] or [n for n in nums if n >= 10]
        if not candidates:
            raise Refused("no amount in the quote")
        hint = [n for n in amounts(value) if n in candidates]
        amt = hint[-1] if hint else max(candidates)
    n = count if count and count > 0 else (counted or 1)
    if n > 1 and n not in nums:
        raise Refused(f"{n} isn't in the quote")
    per_unit = each if each is not None else bool(_EACH.search(quote)) or n == 1
    if not per_unit and n > 1:
        amt, n = round(amt / n), n            # "two spots for 360": $180 a spot, divided in code
    if field == "rent":
        return Value(field, str(amt), f"${amt:,} a month", amount=amt, period="monthly", mandatory=True)
    if field == "parking_cost":
        extra = f" ({n} spots, ${amt * n:,} for all)" if n > 1 else ""
        return Value(field, str(amt), f"${amt:,} a month per spot{extra}", amount=amt, count=n,
                     period="monthly", name="parking")
    fname = fee_name(name or "") if name and fee_name(name) != "other" else fee_name(f"{value} {quote}")
    per = period if period in ("monthly", "one_time") else ("one_time" if _ONE_TIME.search(f"{value} {quote}")
                                                             else "monthly")
    must = mandatory if mandatory is not None else (
        False if _OPTIONAL.search(quote)
        else True if _MANDATORY.search(quote) or fname == "deposit" else None)
    when = "a month" if per == "monthly" else "one time"
    opt = "" if must is None else (", required" if must else ", optional")
    times = f" × {n}" if n > 1 else ""
    return Value("fees", f"{fname}:{amt}:{per}", f"{fname.replace('_', ' ')} ${amt:,}{times} {when}{opt}",
                 amount=amt, count=n, period=per, mandatory=must, name=fname)


def _count(quote: str) -> int | None:
    m = _COUNTED.search(quote)
    if not m:
        return None
    n = amounts(m.group(1))
    return n[0] if n and 1 < n[0] <= 5 else None


def _category_word(field: str, value: str) -> str | None:
    v = value.strip().lower().replace(" ", "_").replace("-", "_")
    return v if v in CATEGORIES.get(field, ()) else None


_MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]


def _date_key(text: str) -> str | None:
    t = text.lower()
    if re.search(r"\b(now|immediately|right away|today|asap)\b|现在|马上|随时", t):
        return "now"
    for i, mon in enumerate(_MONTHS):
        m = re.search(rf"\b{mon}[a-z]*\.?\s*(?:the\s*)?(\d{{1,2}})?", t)
        if m:
            day = int(m.group(1)) if m.group(1) else 1
            return f"{i + 1:02d}-{day:02d}"
        m2 = re.search(r"(?:the\s*)?(\d{1,2})(?:st|nd|rd|th)?\s+of\s+" + mon, t)
        if m2:
            return f"{i + 1:02d}-{int(m2.group(1)):02d}"
    m = re.search(r"(\d{1,2})月(\d{1,2})?[日号]?", text)
    if m:
        return f"{int(m.group(1)):02d}-{int(m.group(2) or 1):02d}"
    return None


_RULES: dict[str, tuple[tuple[str, str], ...]] = {
    "available": (
        ("no", r"(already|just|been) (rented|taken|leased)|no longer|not available|isn't available|gone|"
               r"off the market|已经租|租出去了|没有了"),
        ("yes", r"still available|it's available|is available|yes,? (it|it's|it is)|still (open|free|up)|"
                r"available (now|from|starting)|还在|还有|可以租"),
    ),
    "pets": (
        ("no_pets", r"no pets|pets? (are |is )?not allowed|don'?t allow (any )?pets|not pet[- ]friendly|"
                    r"can'?t have (any )?pets|不(能|可以|允许)(养)?宠物|不让养"),
        ("cats_only", r"cats? only|only cats?|cats? (are |is )?(ok|okay|fine|allowed)[^.]*no dogs|"
                      r"no dogs|只(能|可以)?养猫"),
        ("dogs_only", r"dogs? only|only dogs?|no cats"),
        ("small_only", r"small (pets?|dogs?)|under \d+ (lb|pound|kg)|小型"),
        ("case_by_case", r"case[- ]by[- ]case|depends on the pet|ask the owner|看情况"),
        ("allowed", r"pets? (are |is )?(welcome|allowed|ok|okay|fine)|pet[- ]friendly|"
                    r"we allow (pets|dogs|cats)|"
                    r"(dogs?|cats?) (are |is )?(welcome|allowed|ok|okay|fine)|可以养宠物|允许养"),
    ),
    "laundry": (
        ("in_unit", r"in[- ]suite|in[- ]unit|ensuite|own (washer|laundry)|washer and dryer in|室内洗衣|"
                    r"(房间|单元|套)内|家里有洗衣机"),
        ("in_building", r"shared laundry|laundry room|coin|in the (building|basement)|\bshared\b|basement|"
                        r"楼里|公用|共用|地下室|洗衣房"),
        ("none", r"no laundry|laundromat|没有洗衣"),
    ),
    "parking": (
        ("none", r"no parking|there'?s no (spot|space)|street parking only|(only|just) street parking|"
                 r"只有街边|没有(专属|固定)?的?停?车位|没有停车"),
        ("included", r"parking (is )?included|comes with (a )?(parking|spot)|free parking|包(括)?车位"),
        ("available", r"parking|spot|space|车位"),
    ),
    "furnished": (("no", r"unfurnished|not furnished|无家具|不带家具"),
                  ("yes", r"furnished|comes with furniture|带家具")),
    "ac": (("no", r"no (ac|a/c|air)|window unit you bring|没有空调"),
           ("yes", r"central air|\bac\b|a/c|air condition|空调")),
    "elevator": (("no", r"no elevator|walk[- ]up|stairs only|没有电梯"), ("yes", r"elevator|lift|电梯")),
    "step_free": (("no", r"stairs|steps up|not accessible"),
                  ("yes", r"step[- ]free|wheelchair|no stairs|ground level")),
}

_DISPLAY = {   # "not available" is one of the words card status reads as gone (orchestrator/state.py GONE)
    ("available", "yes"): "still available", ("available", "no"): "not available",
    ("pets", "no_pets"): "no pets", ("pets", "cats_only"): "cats only", ("pets", "dogs_only"): "dogs only",
    ("pets", "small_only"): "small pets only", ("pets", "case_by_case"): "pets case by case",
    ("pets", "allowed"): "pets allowed", ("laundry", "in_unit"): "laundry in the unit",
    ("laundry", "in_building"): "laundry in the building", ("laundry", "none"): "no laundry",
    ("parking", "included"): "parking included", ("parking", "available"): "parking available",
    ("parking", "none"): "no parking", ("furnished", "yes"): "furnished", ("furnished", "no"): "unfurnished",
    ("ac", "yes"): "air conditioning", ("ac", "no"): "no air conditioning",
}
