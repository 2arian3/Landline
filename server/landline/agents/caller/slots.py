"""A viewing time as the landlord said it ("Saturday at two") → a Toronto datetime, in code (hard rule 2).

The Caller passes the landlord's words to `book_viewing`; nothing about the date comes from the model. A
day (a weekday, today or tomorrow) and a time (digits or words, with or without am/pm) are both needed. A
time with no am/pm reads as the one inside the renter's windows when exactly one is; otherwise 8–11 is
morning and 12–7 is afternoon or evening, the way people talk about viewings. A weekday that is today and
already past reads as next week, unless `keep_past_today`: `book_viewing` keeps it today, so it can say the
time has passed and offer what's left (`open_times`) rather than call it outside the renter's windows.
"""
from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from landline.contracts import Window

TORONTO = ZoneInfo("America/Toronto")
WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
SHORT = {d[:3]: d for d in WEEKDAYS} | {"tues": "tuesday", "thur": "thursday", "thurs": "thursday"}
HOURS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
         "ten": 10, "eleven": 11, "twelve": 12, "noon": 12, "midday": 12}
MINUTES = {"fifteen": 15, "thirty": 30, "forty-five": 45, "forty five": 45, "half": 30, "quarter": 15}
EVENING = re.compile(r"\b(pm|p\.m\.|afternoon|evening|tonight|at night)\b")
MORNING = re.compile(r"\b(am|a\.m\.|morning)\b")


def parse(text: str, now: datetime, windows: list[Window] = (),
          keep_past_today: bool = False) -> datetime | None:
    """The next such slot in Toronto time (with `keep_past_today`, today's even when it has passed), or None
    if the words don't name both a day and a time."""
    words = text.lower().replace("’", "'")
    day = _day(words, now.astimezone(TORONTO))
    clock = _clock(words)
    if day is None or clock is None:
        return None
    hour, minute, half = clock
    if half == "24h":
        readings = [_at(day, hour, minute)]
    elif half is not None:
        if half == "pm" and hour < 12:
            hour += 12
        elif half == "am" and hour == 12:
            hour = 0
        readings = [_at(day, hour, minute)]
    else:
        readings = [_at(day, hour % 12, minute), _at(day, hour % 12 + 12, minute)]
    named_weekday = not re.search(r"\b(today|tonight|tomorrow|this (afternoon|evening|morning))\b", words)
    if named_weekday and not keep_past_today:   # "Saturday at 11" on a Saturday afternoon: next Saturday
        readings = [r + timedelta(days=7) if r <= now else r for r in readings]
    inside = [r for r in readings if within(r, windows)]
    if len(inside) == 1 or len(readings) == 1:
        return inside[0] if inside else readings[0]
    return readings[1] if hour == 12 or 1 <= hour <= 7 else readings[0]


def within(at: datetime, windows: list[Window]) -> bool:
    return any(w.start <= at < w.end for w in windows)


def open_times(windows: list[Window], now: datetime, most: int = 2) -> str | None:
    """What can still be booked, in words: "between 12 pm and 2 pm today, or Sunday between 1 pm and 5 pm".
    Each window from now (rounded up to the half hour) to its end; None when every window has passed."""
    soonest = now + timedelta(minutes=-now.minute % 30, seconds=-now.second, microseconds=-now.microsecond)
    today = now.astimezone(TORONTO).date()
    said = []
    for w in sorted(windows, key=lambda w: w.start):
        start = max(w.start, soonest)
        if start >= w.end:
            continue
        local = start.astimezone(TORONTO)
        span = f"between {_clock_words(start)} and {_clock_words(w.end)}"
        day = "today" if local.date() == today else WEEKDAYS[local.weekday()].capitalize()
        said.append(f"{span} today" if day == "today" else f"{day} {span}")
    return ", or ".join(said[:most]) or None


def spoken(at: datetime) -> str:
    """'Saturday at 2 pm', for replies and results."""
    local = at.astimezone(TORONTO)
    return f"{WEEKDAYS[local.weekday()].capitalize()} at {_clock_words(at)}"


def _clock_words(at: datetime) -> str:
    local = at.astimezone(TORONTO)
    h12, half = local.hour % 12 or 12, "am" if local.hour < 12 else "pm"
    return f"{h12} {half}" if local.minute == 0 else f"{h12}:{local.minute:02d} {half}"


def _day(words: str, local: datetime) -> date | None:
    if re.search(r"\btoday\b|\btonight\b|\bthis (afternoon|evening|morning)\b", words):
        return local.date()
    if re.search(r"\btomorrow\b", words):
        return local.date() + timedelta(days=1)
    for token in re.findall(r"[a-z]+", words):
        name = token if token in WEEKDAYS else SHORT.get(token)
        if name is None and token.endswith("s") and token[:-1] in WEEKDAYS:
            name = token[:-1]   # "Saturdays"
        if name is not None:
            ahead = (WEEKDAYS.index(name) - local.weekday()) % 7
            if "next " + token in words and ahead == 0:
                ahead = 7
            return local.date() + timedelta(days=ahead)
    return None


def _clock(words: str) -> tuple[int, int, str | None] | None:
    """(hour, minute, half): half is am, pm, 24h (an hour past 12) or None when the words don't say."""
    half = "pm" if EVENING.search(words) else "am" if MORNING.search(words) else None
    for m in re.finditer(r"\b(\d{1,2})(?:[:.](\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)?(?![\d/]|st\b|nd\b|rd\b|th\b)",
                         words):
        hour, minute = int(m.group(1)), int(m.group(2) or 0)
        if not (0 <= hour <= 23 and minute < 60):
            continue
        if m.group(3):
            return hour, minute, "pm" if m.group(3).startswith("p") else "am"
        if hour > 12 or hour == 0:
            return hour, minute, "24h"
        return hour, minute, half
    if re.search(r"\bnoon\b|\bmidday\b", words):
        return 12, 0, "pm"
    for name, hour in HOURS.items():
        if re.search(rf"\b{name}\b", words):
            minute = 0
            for mname, mins in MINUTES.items():
                if re.search(rf"\b{name}[- ]{mname}\b", words):
                    minute = mins
            if m := re.search(rf"\bhalf past {name}\b", words):
                minute = 30
            return hour, minute, half
    return None


def _at(day: date, hour: int, minute: int) -> datetime:
    return datetime.combine(day, time(hour % 24, minute), TORONTO)
