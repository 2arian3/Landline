"""Is a quote really in the source text? The grounding check behind hard rule 3, as one pure function.

It knows nothing about calls or listings: the caller decides which texts count as the source (for a call,
only the landlord's turns; for a listing, its description). A quote matches a source when, after light
normalization (case, punctuation, curly quotes, "1,850" → "1850", and spoken amounts → the same digits, so
"one fifty", "one hundred fifty dollars" and "$150" are one claim), it appears in it exactly, or some run of
the source's tokens of about the same length is at least `threshold` similar (difflib ratio). Saying the
same amount again ("one fifty, one fifty, one hundred fifty") does not make it a different claim. Chinese,
Japanese and Korean characters count as one token each, so a Mandarin quote is checked character by
character. A quote is matched inside one source only, never across two, so words from two different turns
can't be stitched into something nobody said.

Two things a similarity ratio alone would let through are refused: a fuzzy match whose numbers differ from
the source's ("180 each" against "150 each" scores 0.9), and a match that the source negates just before it
("pets are allowed" inside "no pets are allowed").
"""
from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher

from landline.agents.auditor import numbers

FUZZY = 0.85
MIN_TOKENS = 2          # a one-word quote ("yes", "180") proves nothing about what it supports
FUZZY_MIN_TOKENS = 5    # shorter quotes must match exactly: one changed word is a different claim

NEGATORS = frozenset({"no", "not", "never", "don't", "doesn't", "isn't", "aren't", "won't", "can't", "cannot",
                      "without", "不", "没", "無", "无", "别"})
# "$150", "150 dollars" and "one hundred fifty dollars" are one amount. Only the word right after a number.
_CURRENCY = frozenset({"dollar", "dollars", "buck", "bucks", "cad"})
_CJK = r"\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af"   # kana, CJK ideographs, hangul
_TOKEN = re.compile(rf"[{_CJK}]|[^\s{_CJK}]+")


@dataclass(frozen=True)
class Match:
    source: int          # index into the sources
    score: float         # 1.0 for an exact match


def normalize(text: str) -> str:
    s = text.lower().replace("’", "'").replace("‘", "'").replace("$", " ")   # "$180" and "180" are one claim
    s = re.sub(r"(?<=\d),(?=\d{3})", "", s)               # 1,850 → 1850
    s = re.sub(rf"[^\w'\s{_CJK}]", " ", s)               # punctuation, CJK punctuation included
    s = re.sub(r"(?<=\w)'(?!\w)|(?<!\w)'", " ", s)         # quote marks, not apostrophes
    return re.sub(r"\s+", " ", s).strip()


def normalize_numbers(text: str) -> str:
    """Amounts as digits. The same amount written another way, or said again, is one claim: "one fifty,
    one fifty, one hundred fifty dollars" and "$150" both become "150". A different amount stays different.
    """
    out: list[str] = []
    for w in numbers.digitize(text).split(" "):
        if not w:
            continue
        if w in _CURRENCY and out and out[-1].isdigit():
            continue
        if out and w.isdigit() and out[-1] == w:
            continue
        out.append(w)
    return " ".join(out)


def tokens(text: str) -> list[str]:
    return _TOKEN.findall(normalize_numbers(normalize(text)))


def find(quote: str, sources: Sequence[str], threshold: float = FUZZY) -> Match | None:
    """The best source that contains `quote`, or None. Exact matches win over fuzzy ones."""
    q = tokens(quote)
    if len(q) < MIN_TOKENS:
        # One word inside a longer text proves nothing, but a whole turn of one word ("Yes.") is exactly what
        # was said. What it answers is for the caller to decide (values.settle's question, #139).
        whole = next((i for i, source in enumerate(sources) if q and tokens(source) == q), None)
        return Match(whole, 1.0) if whole is not None else None
    best: Match | None = None
    for i, source in enumerate(sources):
        s = tokens(source)
        if _contains(s, q):
            return Match(i, 1.0)
        score = _fuzzy(q, s) if len(q) >= FUZZY_MIN_TOKENS else 0.0
        if score >= threshold and (best is None or score > best.score):
            best = Match(i, score)
    return best


def verify(quote: str, sources: Sequence[str], threshold: float = FUZZY) -> bool:
    return find(quote, sources, threshold) is not None


def _contains(s: list[str], q: list[str]) -> bool:
    n = len(q)
    return any(s[i:i + n] == q and not _negated(s, i, q) for i in range(len(s) - n + 1))


def _fuzzy(q: list[str], s: list[str]) -> float:
    """Best similarity between the quote and a run of the source about as long (±1 token), among runs that
    hold every number the quote has and no negator the quote lacks, and that the source doesn't negate."""
    if not s:
        return 0.0
    joiner = "" if all(len(t) == 1 for t in q) else " "      # CJK quotes compare as a string of characters
    target = joiner.join(q)
    numbers = {t for t in q if _numeric(t)}
    n, best = len(q), 0.0
    for size in (n - 1, n, n + 1):
        if size < MIN_TOKENS:
            continue
        for i in range(max(1, len(s) - size + 1)):
            run = s[i:i + size]
            if (not numbers <= {t for t in run if _numeric(t)} or _negated(s, i, q)
                    or NEGATORS & set(run) - set(q)):
                continue
            sm = SequenceMatcher(None, target, joiner.join(run), autojunk=False)
            if sm.real_quick_ratio() < best or sm.quick_ratio() < best:
                continue
            best = max(best, sm.ratio())
    return best


def _negated(s: list[str], i: int, q: list[str]) -> bool:
    """The source negates the run starting at i (the token before it is a negator) and the quote doesn't."""
    return i > 0 and s[i - 1] in NEGATORS and q[0] not in NEGATORS


def _numeric(t: str) -> bool:
    return any(c.isdigit() for c in t)
