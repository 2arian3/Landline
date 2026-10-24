"""What one listing call found, in code: grounded quotes, red flags, and the call's CallOutcome.

Until the Auditor (A-07) reads transcripts, the outcome is built from what the Caller recorded live: each
fact keeps its quote only if the landlord really said it (hard rule 3), and red flags come from plain rules
over the landlord's own words, never from the model. `quoted` is the grounding check; it stands in for the
shared `grounding.py` (D-06) and uses the same bar: the quote appears in what was said, exactly or at a
fuzzy ratio of at least 0.85.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher

from landline.contracts import CallOutcome, EndReason, Fact, Viewing

FUZZY = 0.85

# A red flag is a rule over what the landlord said, so the Caller can't talk one away or invent one.
MONEY = re.compile(r"e-?transfer|wire (it|the|me|money)|western union|moneygram|bitcoin|crypto|gift card|"
                   r"money order|(send|pay|transfer) (me |us )?(the |a )?(deposit|first month)|deposit by")
# "First and last month" is a normal, legal deposit once a lease is signed; only money before anyone has
# seen the unit is a flag.
BEFORE = re.compile(r"before (the |a |any |you )?(viewing|view|see|seeing|showing|visit)|"
                    r"to (hold|reserve|secure)|up ?front|in advance|once you (send|pay|transfer)|"
                    r"as soon as you (send|pay|transfer)|before (i|we) (can )?(show|give|send|mail|courier)")
AWAY = re.compile(r"out of (the )?(country|town)|\babroad\b|overseas|"
                  r"not in (toronto|canada|the country|town)|"
                  r"(can'?t|cannot|won'?t be able to) (show|meet)|away (for|on) (work|business)|"
                  r"(mail|courier|send) (you )?the keys?|keys? by (mail|courier)")
RED_FLAGS = {
    "deposit_before_viewing": "wants money before anyone sees the unit",
    "owner_away": "the owner is away and can't show the unit in person",
}


def quoted(quote: str, said: list[str]) -> bool:
    """Whether `quote` is something the landlord said: their words, not the Caller's."""
    q = _norm(quote)
    if len(q) < 2:
        return False
    text = " ".join(_norm(s) for s in said)
    if q in text:
        return True
    words, qwords = text.split(), q.split()
    n = len(qwords)
    if not words or n == 0:
        return False
    best = 0.0
    for i in range(max(1, len(words) - n + 1)):
        for size in (n - 1, n, n + 1):
            if size <= 0:
                continue
            window = " ".join(words[i:i + size])
            best = max(best, SequenceMatcher(None, q, window).ratio())
            if best >= FUZZY:
                return True
    return False


def red_flags(said: list[str]) -> list[str]:
    """Which red-flag rules the landlord's own words match, as the reasons the renter will read."""
    text = " ".join(s.lower() for s in said)
    found = []
    if MONEY.search(text) and BEFORE.search(text):
        found.append(RED_FLAGS["deposit_before_viewing"])
    if AWAY.search(text):
        found.append(RED_FLAGS["owner_away"])
    return found


def outcome(call_id: str, listing_id: str, end_reason: EndReason, facts: list[Fact], said: list[str],
            questions: list[str], viewing: Viewing | None) -> CallOutcome:
    """The call's outcome: the facts the landlord's words support, the red flags they match, the questions
    to bring back, and the viewing code booked."""
    grounded = [f for f in facts if quoted(f.quote, said)]
    return CallOutcome(call_id=call_id, listing_id=listing_id, end_reason=end_reason, facts=_latest(grounded),
                       red_flags=red_flags(said), open_questions=list(dict.fromkeys(questions)),
                       viewing=viewing)


def _latest(facts: list[Fact]) -> list[Fact]:
    """One fact per field, the last said (a corrected read-back replaces the first answer); `other` keeps
    every answer, since each is a different question."""
    keep: dict[str, Fact] = {}
    others: list[Fact] = []
    for f in facts:
        if f.field == "other":
            if not any(o.value == f.value for o in others):
                others.append(f)
        else:
            keep[f.field] = f
    return [*keep.values(), *others]


def _norm(s: str) -> str:
    s = s.lower().replace("’", "'").replace("$", " $ ")
    s = re.sub(r"(?<=\d),(?=\d{3})", "", s)          # 1,850 → 1850
    s = re.sub(r"[^\w$'\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()
