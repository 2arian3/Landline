"""A viewing time as the landlord said it → a Toronto datetime, in code (PORTING bug #16).

`parse("Saturday at two", now)` → the coming Saturday, 14:00 Toronto time. Understands today, tomorrow,
weekdays ("this Saturday", "next Tuesday"), dates ("October 26th", "the 26th"), clock times ("2", "2:30",
"2 pm", "two thirty", "noon", "half past three") and the same in Mandarin ("星期六下午两点",
"明天上午十点半"). A bare hour from 1 to 7 means afternoon, since nobody books a 3 a.m. viewing. Returns
None when the day or the hour is missing, or the time has already passed: an unparsed time is never guessed.
The Auditor tries the Caller's `slots.parse` (A-05) first and falls back to this for dates and Mandarin.
"""
from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from landline.agents.auditor.numbers import TENS, UNITS, amounts, zh_number

TORONTO = ZoneInfo("America/Toronto")
DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
ZH_DAYS = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}
MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
          "november", "december"]
ORDINALS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6,
            "seventh": 7, "eighth": 8,
            "ninth": 9, "tenth": 10, "eleventh": 11, "twelfth": 12, "thirteenth": 13, "fourteenth": 14,
            "fifteenth": 15, "sixteenth": 16, "seventeenth": 17, "eighteenth": 18, "nineteenth": 19,
            "twentieth": 20, "thirtieth": 30}

_NUM = r"(\d{1,2}|" + "|".join(sorted(UNITS.keys() - {"a", "oh", "zero"}, key=lambda w: (-len(w), w))) + ")"
_MIN = r"(\d{2}|" + "|".join(TENS) + r"(?:[ -](?:one|two|three|four|five|six|seven|eight|nine))?|" \
       r"fifteen|o'?clock|oh five|thirty|forty-?five)"
_CLOCK = re.compile(rf"\b(?:at |around |for |by )?(?:(half past|quarter past|quarter to) )?{_NUM}"
                    rf"(?:[: ]{_MIN})?\s*(a\.?m\.?|p\.?m\.?|in the (?:morning|afternoon|evening))?(?![\w:])")
_NOON = re.compile(r"\b(noon|midday|lunchtime)\b")


def parse(text: str, now: datetime) -> datetime | None:
    local = now.astimezone(TORONTO)
    t = text.lower().replace("’", "'")
    day = _zh_day(text, local.date()) if re.search(r"[一-鿿]", text) else _day(t, local.date())
    clock = _zh_clock(text) if re.search(r"[一-鿿]", text) else _clock(t)
    if day is None or clock is None:
        return None
    at = datetime.combine(day, clock, TORONTO)
    if at <= local:
        if _named_weekday(t, text) and not re.search(r"\b(today|tonight)\b", t):
            at += timedelta(days=7)     # "Saturday at two" said on Saturday at three: next Saturday
        else:
            return None
    return at


def _named_weekday(t: str, raw: str) -> bool:
    return any(d in t for d in DAYS) or bool(re.search(r"(星期|周|礼拜)[一二三四五六日天]", raw))


def _day(t: str, today: date) -> date | None:
    if re.search(r"\b(today|tonight|this (afternoon|evening|morning))\b", t):
        return today
    if "day after tomorrow" in t:
        return today + timedelta(days=2)
    if "tomorrow" in t:
        return today + timedelta(days=1)
    for i, name in enumerate(DAYS):
        if re.search(rf"\b{name}s?\b|\b{name[:3]}\b", t):
            ahead = (i - today.weekday()) % 7
            if re.search(rf"\bnext (week )?{name}", t) and ahead < 7:
                ahead += 7 if ahead == 0 or re.search(r"\bnext week\b", t) else 0
            return today + timedelta(days=ahead)
    for i, name in enumerate(MONTHS):
        m = re.search(rf"\b{name[:3]}[a-z]*\.? (?:the )?(\d{{1,2}}|{'|'.join(ORDINALS)})", t)
        if m:
            d = int(m.group(1).rstrip("stndrh")) if m.group(1)[0].isdigit() else ORDINALS[m.group(1)]
            return _next_date(today, i + 1, d)
    m = re.search(r"\bthe (\d{1,2})(?:st|nd|rd|th)\b", t)
    if m:
        d = int(m.group(1))
        month, year = (today.month, today.year) if d >= today.day else _next_month(today)
        try:
            return date(year, month, d)
        except ValueError:
            return None
    return None


def _clock(t: str) -> time | None:
    if _NOON.search(t):
        return time(12, 0)
    for m in _CLOCK.finditer(t):
        prefix, hour_s, minute_s, half = m.group(1), m.group(2), m.group(3), m.group(4)
        hour = int(hour_s) if hour_s.isdigit() else UNITS.get(hour_s)
        if hour is None or not 1 <= hour <= 12:
            continue
        if not (minute_s or half or prefix or m.group(0).strip().startswith(("at", "around", "for", "by"))):
            continue                    # "two spots" is not a time
        minute = _minute(minute_s)
        if minute is None:
            continue
        if prefix == "half past":
            minute = 30
        elif prefix == "quarter past":
            minute = 15
        elif prefix == "quarter to":
            hour, minute = hour - 1 or 12, 45
        pm = bool(half and (half.startswith("p") or "afternoon" in half or "evening" in half))
        am = bool(half and (half.startswith("a") or "morning" in half))
        if pm and hour < 12:
            hour += 12
        elif am and hour == 12:
            hour = 0
        elif not am and not pm and 1 <= hour <= 7:
            hour += 12
        return time(hour, minute)
    return None


def _minute(s: str | None) -> int | None:
    if not s or s.startswith("o"):
        return 0
    if s.isdigit():
        return int(s) if int(s) < 60 else None
    n = amounts(s.replace("-", " "))
    return n[0] if n and n[0] < 60 else None


def _zh_day(raw: str, today: date) -> date | None:
    if "今天" in raw or "今晚" in raw:
        return today
    if "后天" in raw:
        return today + timedelta(days=2)
    if "明天" in raw:
        return today + timedelta(days=1)
    m = re.search(r"(下)?(?:个)?(星期|周|礼拜)([一二三四五六日天])", raw)
    if m:
        if m.group(1):                  # 下周三: Wednesday of the week after this one
            return today + timedelta(days=7 - today.weekday() + ZH_DAYS[m.group(3)])
        return today + timedelta(days=(ZH_DAYS[m.group(3)] - today.weekday()) % 7)
    m = re.search(r"(\d{1,2}|[一二三四五六七八九十]+)月(\d{1,2}|[一二三四五六七八九十]+)[日号]", raw)
    if m:
        month, d = (int(x) if x.isdigit() else zh_number(x) for x in m.groups())
        if month and d:
            return _next_date(today, month, d)
    return None


def _zh_clock(raw: str) -> time | None:
    m = re.search(r"(上午|早上|中午|下午|晚上)?\s*(\d{1,2}|[一二两三四五六七八九十]+)\s*"
                  r"[点點](半|(\d{1,2}|[一二三四五六七八九十]+)分?)?", raw)
    if not m:
        return None
    part, h, rest = m.group(1), m.group(2), m.group(3)
    hour = int(h) if h.isdigit() else zh_number(h)
    if hour is None or not 0 <= hour <= 12:
        return None
    minute = 30 if rest == "半" else (int(m.group(4)) if m.group(4) and m.group(4).isdigit()
                                       else (zh_number(m.group(4)) if m.group(4) else 0))
    if minute is None or minute >= 60:
        return None
    if part in ("下午", "晚上") and hour < 12:
        hour += 12
    elif part is None and 1 <= hour <= 7:
        hour += 12
    return time(hour, minute)


def _next_date(today: date, month: int, d: int) -> date | None:
    try:
        candidate = date(today.year, month, d)
        return candidate if candidate >= today else date(today.year + 1, month, d)
    except ValueError:
        return None


def _next_month(today: date) -> tuple[int, int]:
    return (1, today.year + 1) if today.month == 12 else (today.month + 1, today.year)
