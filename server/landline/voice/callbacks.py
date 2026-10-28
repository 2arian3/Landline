"""A listing agent calling back (A-13b). A listing call that reached no one leaves a voicemail asking them to
call back (A-13a), and the number it dialled is stored on the call (calls.to_e164).

When a call comes in, code looks its From number up: a listing call to that number in the last WITHIN whose
latest try reached no one (voicemail, no answer or busy) makes it a callback, and the Caller for that listing
answers with the same plan (Dispatcher.callback). With more than one such listing, the agent is asked which,
from a list code gives, and code matches the key they press or the address they say: the model never picks
the listing or the session (hard rule 2). Anything else goes to the Concierge, as before, and so does every
call while the lookup fails or takes longer than LOOKUP_S: the renter's call never waits on it.
"""
from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from landline.contracts import as_e164
from landline.orchestrator import SessionRuntime

log = logging.getLogger(__name__)

MISSED = frozenset({"voicemail", "no_answer", "busy"})   # the calls that reached no one
WITHIN = timedelta(days=2)
LOOKUP_S = 2.0
MAX_CHOICES = 4   # listings read out when a number has several; the newest first
TRIES = 2         # times the agent is asked which listing before the call ends

OPENING = "Hi, this is Landline. I'm an AI assistant calling for a renter. Thanks for calling back."
AGAIN = "Sorry, I didn't catch that."
GIVE_UP = ("Sorry, I couldn't tell which place you're calling about. Please call again and press its number. "
           "Goodbye.")
# Said around an address, never part of telling one from another.
NOT_NAMES = frozenset({"st", "street", "ave", "avenue", "rd", "road", "blvd", "boulevard", "dr", "drive",
                       "cres", "crescent", "ln", "lane", "ct", "court", "pl", "place", "unit", "apt", "suite",
                       "east", "west", "north", "south", "the", "and", "for", "about"})


@dataclass(frozen=True)
class Missed:
    """A listing call that reached no one, which a call from that number is about."""
    call_id: str
    session_id: str
    listing_id: str
    address: str


async def find(runtime: SessionRuntime, phone: str | None, now: datetime) -> list[Missed]:
    """The missed listing calls a call from `phone` may be about, newest first; [] when it isn't a callback,
    and also when the lookup fails or is slow, which is logged: that call goes to the Concierge."""
    try:
        async with asyncio.timeout(LOOKUP_S):
            return await lookup(runtime, phone, now)
    except Exception:
        log.exception("callback lookup for %s failed; the call goes to the Concierge", _tail(phone))
        return []


async def lookup(runtime: SessionRuntime, phone: str | None, now: datetime) -> list[Missed]:
    """The lookup itself, which may raise. Only sessions still running here count: a callback's facts and
    booking go to the renter's session, and a session lost to a restart has nowhere to put them."""
    e164 = as_e164(phone) if phone else None
    if runtime.db is None or not e164:
        return []
    out: list[Missed] = []
    for row in await runtime.db.calls.latest_to(e164, now - WITHIN):
        if row.end_reason not in MISSED or not row.session_id or not row.listing_id:
            continue
        if not runtime.has(row.session_id):
            continue
        plan = runtime.state(row.session_id).plan
        listing = runtime.catalogue.by_id.get(row.listing_id)
        if plan is None or row.listing_id not in plan.listing_ids or listing is None:
            continue
        out.append(Missed(row.call_id, row.session_id, row.listing_id, listing.address or "the listing"))
    return out[:MAX_CHOICES]


def ask(choices: Sequence[Missed], again: bool = False) -> str:
    """What the agent hears when the number has several listings: the opening (with the disclosure, hard
    rule 6), then the addresses with their keys."""
    keys = ", ".join(f"{n} for {c.address}" for n, c in enumerate(choices, start=1))
    first = AGAIN if again else OPENING
    return f"{first} Which place are you calling about? Say the address, or press {keys}."


def pick(choices: Sequence[Missed], digits: str = "", speech: str = "") -> Missed | None:
    """The listing the agent chose: the key they pressed, else the address they said, matched in code on its
    house number and street name. None when it names none of them, or can't tell two apart."""
    key = digits.strip()
    if key.isdigit() and 1 <= int(key) <= len(choices):
        return choices[int(key) - 1]
    said = set(_words(speech))
    if not said:
        return None
    scored = [(_score(c.address, said), c) for c in choices]
    best = max(score for score, _ in scored)
    top = [c for score, c in scored if score == best]
    return top[0] if best > 0 and len(top) == 1 else None


def _score(address: str, said: set[str]) -> int:
    """How well what was said names this address: its house number counts double, then each street word."""
    words = [w for w in _words(address) if w not in NOT_NAMES]
    number = next((w for w in words if w.isdigit()), None)
    names = {w for w in words if not w.isdigit() and len(w) > 2}
    return (2 if number is not None and number in said else 0) + len(names & said)


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def _tail(number: str | None) -> str:
    return f"…{(number or '')[-4:]}"
