"""listing_status (A-14): "What happened with Markham?", answered from what Landline's calls stored.

The model passes only the renter's words (`listing_ref`); code finds the listing and builds the reply from
stored outcomes, never the model (hard rule 3): whether the agent was reached, the facts heard with their
day, red flags, and the viewing with its status. Where it looks:

1. This session: the board (`refs.match`) and the calls it has made.
2. A phone caller's earlier sessions, found by the number the call came from (never by an argument): the
   listings in their past call plans, read from each session's stored snapshot the way /api/me/listings
   reads them (api/me.py `listings`). A browser or judge session has no number, so it answers from itself.

Nothing from earlier sessions reaches the call except this reply, and only when the renter asks (#144).
The reply is spoken: at most 25 words, the most important first (rented, a red flag, the viewing, then
the facts). When more was heard than fits, a phone caller is offered the rest by text, and `text_rest`
(their yes) texts all of it, with the agents' own words, to the number the call came from.

With O-09b's shared memory, what other calls heard about the same place, on fields this renter's calls
didn't cover, is added with its day and never who heard it. Reads there take seconds, so it's used only
when the read is ready within OTHERS_WAIT_S; a slower one carries on and is kept for the next ask.
"""
from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import TYPE_CHECKING, Any

from landline.agents.concierge import refs
from landline.api.admin import Place
from landline.api.me import HeardFact, MyListing, MyViewing, listings
from landline.contracts import SessionView, as_e164
from landline.contracts.tools import ListingStatus
from landline.orchestrator.memory import Known
from landline.orchestrator.narrator import MAX_WORDS, TORONTO, _fact, _when
from landline.orchestrator.state import GONE, SessionState

if TYPE_CHECKING:
    from landline.agents.concierge.tools import ToolContext

log = logging.getLogger(__name__)

HISTORY_WAIT_S = 2.0   # the caller's earlier sessions, from Postgres; slower, and this session answers alone
OTHERS_WAIT_S = 1.0    # what other calls heard (O-09b); slower, and the reply goes without it
NOT_FOUND = "I don't see that one in your calls."
OFFER = "Want the rest by text?"
NO_TEXT = "I can't text from this call."
TEXT_CHARS = 1500   # a text stays under Twilio's 1,600 characters
# "When's my viewing?" names no listing: the newest booked one.
_VIEWING = re.compile(r"\b(viewing|visit|showing|tour|booked|booking|appointment)\b", re.I)
# Words a ref carries that say nothing about which place ("what happened with", "the one with the").
_ASKING = {"what", "happened", "with", "about", "did", "they", "say", "said", "my", "for", "has", "had"}
VIEWING_STATUS = {"booked": "waiting for the agent to confirm", "confirmed": "confirmed",
                  "declined": "but the agent can't make it"}


@dataclass
class Status:
    """Everything stored about one listing for this renter, newest first, from one or more calls."""
    listing_id: str
    address: str
    calls: list[MyListing] = field(default_factory=list)   # newest first
    others: list[Known] = field(default_factory=list)      # other calls (O-09b), fields not heard here

    @property
    def latest(self) -> MyListing | None:
        return self.calls[0] if self.calls else None

    def facts(self) -> list[tuple[HeardFact, datetime]]:
        """One fact per field, the newest heard, each with the day its call was made."""
        seen: dict[str, tuple[HeardFact, datetime]] = {}
        for call in self.calls:
            for f in call.facts:
                seen.setdefault(f.field, (f, f.at or call.called_at))
        return list(seen.values())

    def viewing(self) -> MyViewing | None:
        return next((c.viewing for c in self.calls if c.viewing), None)

    def red_flags(self) -> list[str]:
        return next((c.red_flags for c in self.calls if c.ended == "talked"), [])


async def listing_status(args: ListingStatus, ctx: ToolContext, meta: dict) -> str:
    runtime, sid, now = ctx.runtime, ctx.session_id, meta["at"]
    state = runtime.state(sid)
    view = runtime.get(sid)
    place = _place_of(runtime.catalogue)
    history = _this_session(state, view, place, now) + await _earlier(runtime, state, place)
    found = resolve(args.listing_ref, view, history, place)
    if isinstance(found, str):
        return found
    found.others = await _others(runtime, found)
    if not args.text_rest:
        return spoken(found, now, can_text=ctx.text is not None)
    if ctx.text is None:
        return NO_TEXT
    ctx.text(texted(found, now))
    return f"Texted you everything Landline heard about {found.address}."


# ── Where the calls are ───────────────────────────────────────────────────────────────────────────────────


def _place_of(catalogue: Any) -> Callable[[str], Place]:
    def place(lid: str) -> Place:
        x = catalogue.by_id.get(lid)
        return Place(listing_id=lid, address=x.address if x else "that place",
                     unit_label=x.unit_label if x else None)
    return place


def _this_session(state: SessionState, view: SessionView, place: Callable[[str], Place],
                  now: datetime) -> list[MyListing]:
    """This session's settled calls, as rows shaped like the database's, through the same builder."""
    sid = state.session_id
    calls = [{"call_id": c.call_id, "session_id": sid, "listing_id": c.listing_id, "role": "listing",
              "state": c.state, "end_reason": c.outcome.end_reason, "answered_at": None,
              "created_at": c.started_at or now}
             for c in state.calls.values() if c.outcome is not None]
    snapshot = {"state": state.model_dump(mode="json"), "view": view.model_dump(mode="json")}
    return listings([{"session_id": sid, "snapshot": snapshot}], calls, place)


async def _earlier(runtime: Any, state: SessionState, place: Callable[[str], Place]) -> list[MyListing]:
    """A phone caller's earlier sessions, by the number this call came from. None for a browser or judge
    session, without a database, or when Postgres doesn't answer within HISTORY_WAIT_S."""
    db = runtime.db
    if state.channel != "phone" or not state.caller_phone or db is None:
        return []
    try:
        async with asyncio.timeout(HISTORY_WAIT_S):
            user = await db.users.by_phone(as_e164(state.caller_phone) or state.caller_phone)
            if user is None:
                return []
            sessions = [s for s in await db.admin.sessions(user.user_id)
                        if s["session_id"] != state.session_id]
            calls = await db.admin.calls([s["session_id"] for s in sessions]) if sessions else []
    except Exception:
        log.exception("listing_status: the earlier sessions of %s weren't read; this session answers alone",
                      state.session_id)
        return []
    return listings(sessions, calls, place)


async def _others(runtime: Any, found: Status) -> list[Known]:
    """What other calls heard about the place (O-09b), on fields this renter's calls didn't cover."""
    memory = getattr(runtime, "memory", None)
    if memory is None:
        return []
    read = memory.prefetch(found.listing_id)
    try:
        known = await asyncio.wait_for(asyncio.shield(read), OTHERS_WAIT_S)
    except Exception:   # still reading (kept for the next ask), or failed (its callback logged it)
        return []
    heard = {f.field for f, _ in found.facts()}
    return [k for k in known if k.field not in heard and k.field != "other"]


# ── Which listing ─────────────────────────────────────────────────────────────────────────────────────────


def resolve(ref: str, view: SessionView, history: list[MyListing],
            place: Callable[[str], Place]) -> Status | str:
    """The listing `ref` names, with every call about it: the board first, then the places in the renter's
    calls (address words, then words of what was heard there, like "balcony"), then "my viewing". A string
    when there's nothing to answer: which one, or not found."""
    by_listing: dict[str, list[MyListing]] = {}
    for call in sorted(history, key=lambda c: c.called_at, reverse=True):
        by_listing.setdefault(call.listing_id, []).append(call)

    def status(lid: str) -> Status:
        return Status(lid, _address(place(lid)), by_listing.get(lid, []))

    card = refs.match(ref, view.board, view.focus_listing_id).card
    if card is not None and card.listing_id in by_listing:
        return status(card.listing_id)
    words = {w for w in refs._words(ref) if w not in refs.FILLER and w not in _ASKING}
    # A place the renter's calls reached wins a tie with an uncalled card: "Markham" asks what happened.
    on_card = len(words & _address_words(place(card.listing_id))) if card is not None else 0
    for words_of in (lambda calls: _address_words(place(calls[0].listing_id)), _heard_words):
        scores = {lid: len(words & words_of(calls)) for lid, calls in by_listing.items()}
        best = max(scores.values(), default=0)
        hits = [lid for lid, s in scores.items() if s == best]
        if best < max(on_card, 1):
            continue
        if len(hits) == 1:
            return status(hits[0])
        return f"Which one? Your calls reached {_join([status(lid).address for lid in hits[:3]])}."
    if card is not None:
        return status(card.listing_id)
    if _VIEWING.search(ref):
        booked = [lid for lid, calls in by_listing.items() if any(c.viewing for c in calls)]
        if booked:
            return status(booked[0])
    return NOT_FOUND


def _address(p: Place) -> str:
    return f"{p.address}, {p.unit_label}" if p.unit_label else p.address


def _address_words(p: Place) -> set[str]:
    return set(refs._words(_address(p))) - refs.FILLER


def _heard_words(calls: list[MyListing]) -> set[str]:
    return {w for c in calls for f in c.facts for w in refs._words(f.value) if w not in refs.FILLER}


# ── What to say ───────────────────────────────────────────────────────────────────────────────────────────


def spoken(found: Status, now: datetime, *, can_text: bool) -> str:
    """At most MAX_WORDS: the address, then rented or unreached, a red flag, the viewing, and the facts by
    day, as many as fit. When something is left out and the caller can be texted, it ends with OFFER."""
    head, parts = f"{found.address}:", _sentences(found, now)
    whole = " ".join([head, *parts])
    if _count(whole) <= MAX_WORDS:
        return whole
    budget = MAX_WORDS - (_count(OFFER) if can_text else 0)
    said = [head]
    for part in parts:
        room = budget - _count(" ".join(said))
        if _count(part) <= room:
            said.append(part)
        elif cut := _fit_facts(part, room):
            said.append(cut)
    return " ".join([*said, OFFER] if can_text else said)


def _sentences(found: Status, now: datetime) -> list[str]:
    """The sentences about one place, most important first. A facts sentence is one day's facts, joined by
    "; " so it can be cut back fact by fact."""
    out: list[str] = []
    latest = found.latest
    facts = found.facts()
    if latest is None:
        out.append("Landline hasn't called it for you yet.")
    gone = next(((f, at) for f, at in facts if f.field == "available" and f.value.strip().lower() in GONE),
                None)
    if gone:
        out.append(f"{_cap(_on(gone[1], now))} the agent said it's already rented.")
    elif latest is not None and latest.ended != "talked" and not facts:
        how = "went to voicemail" if latest.ended == "voicemail" else "wasn't answered"
        out.append(f"{_cap(_on(latest.called_at, now))} the call {how}, so nothing was heard yet.")
    if flags := found.red_flags():
        out.append(f"Watch out: {_lower(flags[0]).rstrip('.')}.")
    if viewing := found.viewing():
        out.append(_viewing(viewing, now))
    by_day: dict[str, list[str]] = {}
    for f, at in facts:
        if f.field != "available":
            by_day.setdefault(_on(at, now), []).append(_fact(f.field, f.value.strip().rstrip(".")))
    out += [f"{_cap(day)} the agent said {'; '.join(said)}." for day, said in by_day.items()]
    by_day = {}
    for k in found.others:
        by_day.setdefault(_on_day(k.day, now), []).append(_fact(k.field, k.value.strip().rstrip(".")))
    out += [f"Another Landline call {day} heard {'; '.join(said)}." for day, said in by_day.items()]
    return out


def _viewing(v: MyViewing, now: datetime) -> str:
    when = _when(v.starts_at)
    if v.starts_at < now:
        return f"The viewing was {when}."
    return f"Viewing {when}, {VIEWING_STATUS.get(v.status, VIEWING_STATUS['booked'])}."


def _fit_facts(sentence: str, words: int) -> str | None:
    """A facts sentence cut back to its first facts so it fits in `words`; None when not even one does."""
    if "; " not in sentence:
        return None
    lead, _, rest = sentence.partition(" said ")
    items = rest.rstrip(".").split("; ")
    while items:
        cut = f"{lead} said {'; '.join(items)}."
        if _count(cut) <= words:
            return cut
        items.pop()
    return None


def texted(found: Status, now: datetime) -> str:
    """Everything, for the renter's phone: each fact with the agent's own words, red flags, the viewing,
    and what other calls heard (when, never who)."""
    lines = [f"Landline: what our calls heard about {found.address}."]
    latest = found.latest
    if latest is not None and latest.ended != "talked" and not found.facts():
        how = "went to voicemail" if latest.ended == "voicemail" else "wasn't answered"
        lines.append(f"{_cap(_on(latest.called_at, now))} the call {how}.")
    by_day: dict[str, list[str]] = {}
    for f, at in found.facts():
        by_day.setdefault(_on(at, now), []).append(f"{_fact(f.field, f.value.strip().rstrip('.'))} "
                                                   f"(“{f.quote.strip()}”)")
    lines += [f"{_cap(day)} the agent said: {'; '.join(said)}." for day, said in by_day.items()]
    lines += [f"Watch out: {flag.rstrip('.')}." for flag in found.red_flags()]
    if viewing := found.viewing():
        lines.append(_viewing(viewing, now))
    by_day = {}
    for k in found.others:
        by_day.setdefault(_on_day(k.day, now), []).append(f"{_fact(k.field, k.value)} (“{k.quote}”)")
    lines += [f"Another Landline call {day} heard: {'; '.join(said)}." for day, said in by_day.items()]
    kept = lines[:1]
    for line in lines[1:]:
        if len("\n".join([*kept, line])) > TEXT_CHARS:
            break
        kept.append(line)
    return "\n".join(kept)


def _on(at: datetime, now: datetime) -> str:
    return _on_day(at.astimezone(TORONTO).date(), now)


def _on_day(day: date, now: datetime) -> str:
    """When, from today in Toronto: "today", "yesterday", "on Tuesday" within the week, else "on Oct 21"."""
    days = (now.astimezone(TORONTO).date() - day).days
    if days == 0:
        return "today"
    if days == 1:
        return "yesterday"
    if 1 < days < 7:
        return f"on {day:%A}"
    return f"on {day:%b} {day.day}"


def _count(text: str) -> int:
    return len(text.split())


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:]


def _lower(s: str) -> str:
    return s[:1].lower() + s[1:]


def _join(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]
