"""Numbers as people say them on the phone, read in code (hard rule 2: amounts are never a model's).

`amounts("two spots, one eighty each")` → [2, 180]. Handles digits ("$1,850", "2.5"), English words,
including the shortcuts people use for rent ("twenty-six fifty" = 2650, "one eighty" = 180, "eighteen
hundred" = 1800, "2k"), and Chinese numerals ("两千六" = 2600, "一百八" = 180).
"""
from __future__ import annotations

import re

UNITS = {"zero": 0, "oh": 0, "one": 1, "a": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
         "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
         "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19}
TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80,
        "ninety": 90}
SCALES = {"hundred": 100, "thousand": 1000, "grand": 1000, "k": 1000}
WORDS = UNITS.keys() | TENS.keys() | SCALES.keys()

ZH_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "兩": 2, "三": 3, "四": 4, "五": 5, "六": 6,
             "七": 7, "八": 8, "九": 9}
ZH_UNITS = {"十": 10, "百": 100, "千": 1000, "万": 10000, "萬": 10000}
_ZH = re.compile(f"[{''.join(ZH_DIGITS)}{''.join(ZH_UNITS)}]+")
_TOKEN = re.compile(r"\d[\d,]*(?:\.\d+)?k?|[a-z]+")


def amounts(text: str) -> list[int]:
    """Every whole number in `text`, in the order said. Decimals are rounded."""
    out: list[int] = []
    for m in _ZH.finditer(text):
        if (n := zh_number(m.group())) is not None:
            out.append(n)
    run: list[str] = []
    prev = ""
    for tok in _TOKEN.findall(_ZH.sub(" ", text.lower().replace("-", " "))):
        after_digits, prev = prev[:1].isdigit(), tok
        if after_digits and tok in ("thousand", "grand", "hundred") and not run:   # "2 grand", "1 thousand"
            out[-1] *= SCALES[tok]
        elif tok[0].isdigit():
            out += _flush(run)
            out.append(_digits(tok))
        elif tok == "a" and run:                 # "one eighty a month": the number ended
            out += _flush(run)
            run.append(tok)
        elif tok in WORDS and (run or tok not in ("k", "grand")):
            run.append(tok)
        elif tok == "and" and run:
            continue
        else:
            out += _flush(run)
    out += _flush(run)
    return out


def words_number(words: list[str]) -> int | None:
    """English number words → int. `a` counts only before a scale ("a hundred")."""
    if not words:
        return None
    vals = [UNITS.get(w, TENS.get(w)) for w in words]
    # Speech shortcuts with no scale word: "twenty six fifty" = 2650, "one eighty" = 180, "eighteen fifty".
    if all(w not in SCALES for w in words):
        groups = _pairs(words)
        if groups is None:
            return None
        if len(groups) == 2 and groups[0] < 100 and 10 <= groups[1] < 100:
            return groups[0] * 100 + groups[1]
        return groups[0] if len(groups) == 1 else None
    total, current = 0, 0
    for w, v in zip(words, vals, strict=True):
        if w in SCALES:
            scale = SCALES[w]
            if scale == 100:
                current = (current or 1) * 100
            else:
                total += (current or 1) * scale
                current = 0
        else:
            current += v
    return total + current


def zh_number(text: str) -> int | None:
    """Chinese numerals → int: 一百八十 = 180, 两千六 = 2600 (a trailing digit takes the next unit down)."""
    total, section, digit, last_unit = 0, 0, None, None
    for ch in text:
        if ch in ZH_DIGITS:
            if digit is not None:            # 二六五〇 style digit strings
                digit = digit * 10 + ZH_DIGITS[ch]
            else:
                digit = ZH_DIGITS[ch]
        elif ch in ZH_UNITS:
            unit = ZH_UNITS[ch]
            if unit == 10000:
                total += (section + (digit or 0)) * 10000
                section, digit = 0, None
            else:
                section += (1 if digit is None else digit) * unit
                digit = None
            last_unit = unit
        else:
            return None
    if digit is not None:
        if last_unit and last_unit >= 100 and digit < 10:
            digit *= last_unit // 10 if last_unit < 10000 else 1000   # 两千六 = 2600, 一百八 = 180
        section += digit
    n = total + section
    return n if (n or "零" in text or "〇" in text) else None


def _pairs(words: list[str]) -> list[int] | None:
    """Split scale-free number words into numbers under 100: twenty six fifty → [26, 50]."""
    groups: list[int] = []
    i = 0
    while i < len(words):
        w = words[i]
        if w == "a":
            return None
        if w in TENS:
            v = TENS[w]
            if i + 1 < len(words) and words[i + 1] in UNITS and 0 < UNITS[words[i + 1]] < 10:
                v += UNITS[words[i + 1]]
                i += 1
            groups.append(v)
        else:
            groups.append(UNITS[w])
        i += 1
    return groups


def _flush(run: list[str]) -> list[int]:
    if not run:
        return []
    words = list(run)
    run.clear()
    while words and words[0] == "a" and (len(words) == 1 or words[1] not in SCALES):
        words.pop(0)
    if not words:
        return []
    n = words_number(words)
    if n is not None:
        return [n]
    # Not one number: read each word on its own ("two spots one eighty" arrives as two runs anyway).
    return [v for w in words if (v := UNITS.get(w, TENS.get(w))) is not None and w != "a"]


def _digits(tok: str) -> int:
    k = tok.endswith("k")
    v = float(tok.rstrip("k").replace(",", ""))
    return round(v * 1000) if k else round(v)
