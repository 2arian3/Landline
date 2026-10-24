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
from datetime import date, datetime, timedelta

from landline.agents.auditor import when
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
    "key_deposit": r"key|fob|钥匙", "pet_fee": r"pet (fee|deposit|rent)|宠物",   # before plain deposit
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
           name: str | None = None, today: date | None = None, question: str | None = None,
           parts: date | None = None) -> Value:
    """`value` and the structured hints are claims; the quote decides. Raises `Refused`. `today` is the
    call's day in Toronto (`call_day`), which a date like "tomorrow" counts from; the current day if None.
    `question` is the Caller's line just before the quote: a bare "Yes." or "No." answers it (#139).
    `parts` is a move-in day code already made (`from_parts`, #157), kept when the quote names no day."""
    said_no = _NO_FEES.search(quote) or answer(field, quote, question) == "no"
    if field == "fees" and not amounts(quote) and said_no:
        return Value(field, "none", "no other monthly fees")   # "No, nothing else." is an answer (#187)
    if field in MONEY_FIELDS:
        return _money(field, value, quote, amount, count, each, period, mandatory, name)
    if field in CATEGORIES:
        said = category(field, quote) or answer(field, quote, question)
        exact = _category_word(field, value)   # a category named outright ("allowed") is a claim to check
        if exact and said and exact != said:
            raise Refused(f"the quote says {said}, not {exact}")
        got = said or exact or category(field, value)   # the quote decides over a keyword in the value
        if got is None:
            raise Refused(f"can't tell {field} from the words")
        return Value(field, got, _DISPLAY.get((field, got), got.replace("_", " ")))
    if field == "utilities_included":
        if answer(field, quote, question) == "yes":   # "Yes, all of them." to "Are utilities included?"
            named = sorted(u for u, pat in UTILITIES.items() if re.search(pat, question or "", re.I))
            return (Value(field, ",".join(named), ", ".join(named) + " included") if named
                    else Value(field, "included", "utilities included"))
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
    if field == "available_from":   # the day comes from the quote, in code; a model's date is never kept
        today = today or call_day(None)
        day = free_from(quote, today) or parts
        if day is None:
            raise Refused("the quote names no day")
        return Value(field, "now" if day <= today else f"{day:%m-%d}", spoken_day(day, today))
    return Value(field, value.strip(), value.strip())   # light, outdoor_space, other: words, kept as said


def bare(quote: str) -> str | None:
    """"yes" or "no" when the words are only an answer ("Yes.", "Yep, they are", "Nope"), else None."""
    t = quote.strip().lower().replace("’", "'")
    if _BARE_NO.search(t):
        return "no"
    if _BARE_YES.search(t) and not _NEGATED.search(t):   # "Yeah, it's not." is no yes; dropped, not guessed
        return "yes"
    return None


def asks(field: str, question: str | None) -> bool:
    """The Caller's line asks about this field."""
    pattern = _ASKS.get(field)
    return bool(question and pattern and re.search(pattern, question, re.I))


def answer(field: str, quote: str, question: str | None) -> str | None:
    """The category a bare yes or no gives, as the answer to the Caller's question about this field; None
    when the words say more than yes or no, or the question was about something else (or there was none)."""
    said = bare(quote)
    if said is None or not asks(field, question):
        return None
    if field == "pets":
        return "allowed" if said == "yes" else "no_pets"
    if field == "parking":
        if said == "no":
            return "none"
        included = re.search(r"includ|come with|comes with|free", question or "", re.I)
        return "included" if included else "available"
    return said   # available, furnished, ac, elevator, utilities_included: yes or no


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
    if fname == "pet_fee" and period is None:   # a pet fee is paid once unless they say rent or a month
        per = "monthly" if re.search(r"pet rent|a month|monthly|per month", quote, re.I) else "one_time"
    must = mandatory if mandatory is not None else (
        False if _OPTIONAL.search(quote)
        else True if _MANDATORY.search(quote) or fname in ("deposit", "pet_fee") else None)
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


_NOW = re.compile(r"\b(now|immediately|right away|today|asap|any ?time|whenever|vacant|ready now)\b|"
                  r"现在|马上|随时")
_COUNT = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6}
_DAY_WORD = r"(\d{1,2})(?:st|nd|rd|th)?|(" + "|".join(when.ORDINALS) + ")"
_NTH = r"(\d{1,2})(?:st|nd|rd|th)|(" + "|".join(when.ORDINALS) + ")"   # "the 1st", never "the 2 bedroom"
_NOT_A_DAY = r"(?![\s-]*(?:floor|bed|room|unit|stor|level))"         # "the second floor"


def call_day(at: datetime | None) -> date:
    """The day a call happened, in Toronto: what "tomorrow" and "next week" count from."""
    return (at or datetime.now(when.TORONTO)).astimezone(when.TORONTO).date()


def free_from(text: str, today: date) -> date | None:
    """The day the words say a place is free from, counted from `today` (the call's day in Toronto), or
    None when they name no day. Only the words decide: a date a model offered is never taken."""
    t = text.lower().replace("’", "'")
    if "day after tomorrow" in t:
        return today + timedelta(days=2)
    if "tomorrow" in t or "明天" in text:
        return today + timedelta(days=1)
    if m := re.search(r"\bin (a|an|one|two|three|four|five|six|\d{1,2}) (day|week|month)s?\b", t):
        n = _COUNT.get(m.group(1)) or int(m.group(1))
        return (_months_on(today, n) if m.group(2) == "month"
                else today + timedelta(days=n * (7 if m.group(2) == "week" else 1)))
    if re.search(r"\bend of next month\b|下个?月底", t + " " + text):
        return _months_on(today.replace(day=1), 2) - timedelta(days=1)
    if m := re.search(r"(\d{1,2})月底", text):
        first = when._next_date(today.replace(day=1), int(m.group(1)), 1) or today
        return _months_on(first, 1) - timedelta(days=1)
    if re.search(r"\bend of (the|this) month\b|\bend of month\b|月底", t + " " + text):
        return _months_on(today.replace(day=1), 1) - timedelta(days=1)
    if re.search(r"\b(first|1st|beginning|start) of (the )?next month\b|\bnext month\b|下个?月",
                 t + " " + text):
        return _months_on(today.replace(day=1), 1)
    for i, name in enumerate(when.MONTHS):   # a named date beats its weekday: "Sunday, November first"
        month = rf"(?:{name}|{name[:3]}|sept)" if name == "september" else rf"(?:{name}|{name[:3]})"
        if not re.search(rf"\b{month}\b", t):
            continue
        said_may = rf"\bmay\.?\s+(?:the\s+)?(?:{_DAY_WORD})\b|\b(?:in|of|from|by|early|mid|late)\s+may\b"
        if name == "may" and not re.search(said_may, t):
            continue   # "it may be free" is not May
        if re.search(rf"\bend of {month}\b", t):
            first = when._next_date(today, i + 1, 1) or today
            return _months_on(first, 1) - timedelta(days=1)
        m = (re.search(rf"\b{month}\.?\s+(?:the\s+)?(?:{_DAY_WORD})\b", t)
             or re.search(rf"\b(?:the\s+)?(?:{_DAY_WORD})\s+of\s+{month}\b", t))
        day = (1 if m is None else int(m.group(1)) if m.group(1) else when.ORDINALS[m.group(2)])
        if m is None and re.search(rf"\bmid[\s-]*{month}\b", t):
            day = 15
        return when._next_date(today, i + 1, day)
    if m := re.search(r"(\d{1,2})月(\d{1,2})?[日号]?", text):
        return when._next_date(today, int(m.group(1)), int(m.group(2) or 1))
    if m := re.search(rf"\bthe\s+(?:{_NTH})\b{_NOT_A_DAY}", t):   # "from the first": the next such day
        d = int(m.group(1)) if m.group(1) else when.ORDINALS[m.group(2)]
        month, year = (today.month, today.year) if d >= today.day else when._next_month(today)
        try:
            return date(year, month, d)
        except ValueError:
            return None
    for i, name in enumerate(when.DAYS):
        if re.search(rf"\b{name}\b", t):
            return today + timedelta(days=(i - today.weekday()) % 7 or 7)   # the coming one
    if re.search(r"\bnext week\b|下(个)?(星期|周|礼拜)", t + " " + text):
        return today + timedelta(days=(0 - today.weekday()) % 7 or 7)       # that week's Monday
    if _NOW.search(t + " " + text):
        return today
    return None


def _months_on(day: date, n: int) -> date:
    """The same day `n` months on (the month's last day when it has fewer)."""
    month0 = day.month - 1 + n
    year, month = day.year + month0 // 12, month0 % 12 + 1
    for d in (day.day, 30, 29, 28):
        try:
            return date(year, month, d)
        except ValueError:
            continue
    return date(year, month, 28)


def from_parts(kind: str, today: date, *, weekday: str | None = None, weeks: int | None = None,
               month: int | None = None, day: int | None = None) -> date | None:
    """The day the Caller's parts name (`record_available_from`, #157), counted from `today`, the call's day
    in Toronto; None for `unknown` or parts that make no date. A past month or date is next year's."""
    if kind == "now":
        return today
    if kind == "tomorrow":
        return today + timedelta(days=1)
    if kind == "weekday" and weekday in when.DAYS:
        return today + timedelta(days=(when.DAYS.index(weekday) - today.weekday()) % 7 or 7)
    if kind == "next_week":
        return today + timedelta(days=(0 - today.weekday()) % 7 or 7)   # that week's Monday, as free_from
    if kind == "in_weeks" and weeks:
        return today + timedelta(weeks=weeks)
    if kind == "end_of_month":
        return _months_on(today.replace(day=1), 1) - timedelta(days=1)
    if kind in ("date", "month") and month:
        return when._next_date(today, month, day if kind == "date" and day else 1)
    return None


def spoken_day(day: date, today: date) -> str:
    """"now", or the day in words: "Sunday, October 25", with the year when it isn't the call's year."""
    if day <= today:
        return "now"
    words = f"{day:%A}, {day:%B} {day.day}"
    return words if day.year == today.year else f"{words}, {day.year}"


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
    "ac": (("no", r"no (ac|a/c|air)|(doesn't|does not|don't|do not) have (ac|a/c|air)|window unit you bring|"
                  r"没有空调"),
           ("yes", r"central air|\bac\b|a/c|air condition|空调")),
    "elevator": (("no", r"no elevator|walk[- ]up|stairs only|没有电梯"), ("yes", r"elevator|lift|电梯")),
    "step_free": (("no", r"stairs|steps up|not accessible"),
                  ("yes", r"step[- ]free|wheelchair|no stairs|ground level")),
}

# A bare answer (#139): the whole reply is a yes or a no, maybe with a filler ("Yep, they are."). "No problem"
# and "no worries" are a yes in effect, so they aren't read as no. Only for fields asked as yes-or-no.
_BARE_YES = re.compile(r"^(yes|yeah|yep|yup|sure|of course|absolutely|definitely|correct|they are|it is|"
                       r"it does|they do|we do)\b[^.!?]{0,20}[.!?]?$")
_BARE_NO = re.compile(r"^(no|nope|nah)\b(?!\s*(problem|worries|issue))[^.!?]{0,20}[.!?]?$|"
                      r"^(not really|it isn't|it's not|they aren't|they're not|it doesn't|we don't|"
                      r"they don't|it is not|they are not|it does not|they do not|we do not|of course not)\b"
                      r"(?![^.!?]*\b(problem|worries|issue)\b)[^.!?]{0,20}[.!?]?$")
_NEGATED = re.compile(r"\bnot\b|n't\b|\bnever\b")
_NO_FEES = re.compile(r"\bno (other |extra |additional |more )?(fees|charges|costs)\b|\bnothing else\b|"
                      r"\b(just|only) the rent\b|^\s*none\b", re.I)
_ASKS = {
    "available": r"available|still (on the market|open|free)|rented",
    "fees": r"\bfees?\b|\bcharges?\b|on top of the rent|anything else to pay",
    "pets": r"\bpets?\b|\bdogs?\b|\bcats?\b|animal",
    "parking": r"parking|\bspots?\b|garage",
    "furnished": r"furnish|furniture",
    "ac": r"\bac\b|a/c|air condition",
    "elevator": r"elevator|\blift\b",
    "utilities_included": r"utilit|hydro|heat|water|electric|internet|wi-?fi|\bgas\b",
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
