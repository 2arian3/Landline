"""Red flags: plain rules over the landlord's own words, so no model can talk one away or invent one.

Taken over from the Caller's stand-in (A-05 `outcome.py`): e-transfer, wire and Western Union, "once you
send", the owner away, keys by courier, money before a viewing. Added here: refusing a viewing, payment
that can't be traced (gift cards, crypto), and the same patterns in Mandarin. Each flag keeps the landlord
sentence that raised it, so the page can show the words behind it.

"First and last month" once a lease is signed is normal and legal in Ontario; only money asked for before
anyone has seen the unit, or through a channel nobody can trace, is a flag.
"""
from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

REASONS = {
    "deposit_before_viewing": "wants money before anyone sees the unit",
    "untraceable_payment": "asks for payment that can't be traced (wire, gift cards, crypto)",
    "owner_away": "the owner is away and can't show the unit in person",
    "no_viewing": "won't let anyone see the unit before paying or signing",
}

_I = re.IGNORECASE
MONEY = re.compile(r"e-?\s?transfer|interac|wire (it|the|me|money|transfer)|western union|moneygram|bitcoin|"
                   r"crypto|gift ?cards?|money order|(send|pay|transfer) (me |us )?(the |a |your )?"
                   r"(deposit|first month|money|payment)|deposit by|"
                   r"押金|定金|订金|转账|汇款|打款|付款|微信支付|支付宝", _I)
UNTRACEABLE = re.compile(r"western union|moneygram|bitcoin|crypto|gift ?cards?|"
                         r"wire (it|the|me|money|transfer)|"
                         r"比特币|礼品卡|西联", _I)
BEFORE = re.compile(r"before (the |a |any |you )?(viewing|view|see|seeing|showing|visit|come)|"
                    r"to (hold|reserve|secure) (it|the (unit|place|apartment|spot))|"
                    r"once (you|i get|i receive|we get|we receive) (send|pay|transfer|the|your|it)|"
                    r"as soon as (you )?(send|pay|transfer)|"
                    r"before (i|we) (can )?(show|give|send|mail|courier)|"
                    r"看房(之|以)?前|先(转账|付|交|打款|汇款)|收到(钱|款|押金)(之后|以后|后)", _I)
AWAY = re.compile(r"out of (the )?(country|town)|\babroad\b|overseas|"
                  r"not in (toronto|canada|the country|town)|"
                  r"(i'?m|we'?re|i am|we are|owner is|he'?s|she'?s) (currently )?(away|in (dubai|london|"
                  r"china|europe|the states))|away (for|on) (work|business)|"
                  r"(mail|courier|ship|send) (you )?the keys?|keys? (by|through|via) (mail|courier|post)|"
                  r"在国外|出国|不在(多伦多|加拿大|国内)|(快递|寄)(给你)?钥匙|钥匙(快递|寄)", _I)
NO_VIEWING = re.compile(r"(no|(do not|don'?t|can'?t|cannot|does not|doesn'?t) do|not doing) "
                        r"(in-?person )?(viewings?|showings?|tours?)|"
                        r"(can'?t|cannot|won'?t|unable to) (show (you )?(it|the (unit|place|apartment))|"
                        r"let (you|anyone) (see|in))(?! (on |this |until |till |before )?"
                        r"(today|tonight|tomorrow|"
                        r"mon|tue|wed|thu|fri|sat|sun|morning|afternoon|evening|"
                        r"this week|next week|until|till))|"
                        r"sight unseen|without (a viewing|seeing it|viewing)|"
                        r"(不能|没法|不可以|不方便)(看房|带你看)|不(提供|安排)看房", _I)
_SENTENCE = re.compile(r"(?<=[.!?。！？])\s*")


@dataclass(frozen=True)
class Flag:
    code: str
    reason: str
    quote: str           # the landlord sentence that raised it


def red_flags(said: Sequence[str]) -> list[Flag]:
    """The flags the landlord's turns raise, at most one of each, in a fixed order."""
    sentences = [s.strip() for turn in said for s in _SENTENCE.split(turn) if s.strip()]
    text = " ".join(sentences)
    found: list[Flag] = []

    def add(code: str, pattern: re.Pattern) -> None:
        quote = next((s for s in sentences if pattern.search(s)), "")
        found.append(Flag(code, REASONS[code], quote))

    # Money asked for when nobody can see the unit first is money before a viewing, however it's put.
    if MONEY.search(text) and (BEFORE.search(text) or AWAY.search(text) or NO_VIEWING.search(text)):
        add("deposit_before_viewing", _Both(MONEY, BEFORE) if _any(MONEY, BEFORE, sentences) else MONEY)
    if UNTRACEABLE.search(text):
        add("untraceable_payment", UNTRACEABLE)
    if AWAY.search(text):
        add("owner_away", AWAY)
    if NO_VIEWING.search(text):
        add("no_viewing", NO_VIEWING)
    return found


def _any(a: re.Pattern, b: re.Pattern, sentences: list[str]) -> bool:
    return any(a.search(s) and b.search(s) for s in sentences)


class _Both:
    """Matches a sentence that has both patterns: the best quote for money asked before a viewing."""

    def __init__(self, a: re.Pattern, b: re.Pattern):
        self.a, self.b = a, b

    def search(self, s: str) -> bool:
        return bool(self.a.search(s) and self.b.search(s))
