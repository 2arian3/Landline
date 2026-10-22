"""Phone numbers in E.164. Ported from Realest (server/calls.py `as_e164`)."""
from __future__ import annotations

import re

E164 = re.compile(r"^\+[1-9]\d{7,14}$")


def as_e164(raw: str | None) -> str:
    """Accept +14165550101, 4165550101, (416) 555-0101 or +4165550101 (NANP missing the 1).

    Returns "" when the input can't be read as a phone number.
    """
    if not raw:
        return ""
    raw = str(raw).strip()
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 11 and digits.startswith("1"):
        cand = "+" + digits
    elif len(digits) == 10 and digits[0] in "23456789":
        cand = "+1" + digits
    elif E164.match(raw):
        return raw
    else:
        return ""
    return cand if E164.match(cand) else ""
