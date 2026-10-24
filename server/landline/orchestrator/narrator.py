"""The Narrator (O-06): every sentence Landline says or texts that code writes. Templates, no model.

    line(before, after, events, state, catalogue)  the short line for `agent_says`: what the page shows
                                                    and what a Concierge tool reply speaks (≤ 25 words)
    results(state, view, catalogue)                 the results after the calls: the SMS, the page text,
                                                    and the next call's opening

Every number comes from the board or the call outcome, never from a model. Templates are English for now;
the Concierge speaks a tool reply in the renter's own language.
"""
from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from zoneinfo import ZoneInfo

from landline.contracts import Card, Event, PreferencesUpdated, SessionView
from landline.db.catalogue import Catalogue
from landline.orchestrator.state import SessionState
from landline.scoring.weights import feature_key

TORONTO = ZoneInfo("America/Toronto")
MAX_WORDS = 25          # a tool reply is spoken; docs/CONTRACTS.md caps it
MAX_FACTS = 2           # facts read out per place in the results
WIDEN = "Nothing fits now. Want to widen the budget or the area?"
ORDINALS = ("first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth", "tenth")
BEDS = {0: "studios are fine", 1: "one bedroom or more", 2: "two bedrooms or more",
        3: "three bedrooms or more"}
RERANKS = {"PreferencesUpdated", "FeedbackGiven", "AnchorResolved"}   # events whose re-rank is worth saying
# How a fact's field reads out loud: "parking: $180 a month".
FIELDS = {
    "available": "still available", "available_from": "free from", "rent": "rent", "fees": "fees",
    "parking": "parking", "parking_cost": "parking", "pets": "pets", "laundry": "laundry", "ac": "AC",
    "utilities_included": "utilities", "furnished": "furnished", "lease_months": "lease",
    "sqft": "size", "floor": "floor", "light": "light", "elevator": "elevator", "step_free": "step-free",
    "outdoor_space": "outdoor space", "other": "also",
}


# The brief


def line(before: SessionView | None, after: SessionView, events: Sequence[Event], state: SessionState,
         catalogue: Catalogue) -> str | None:
    """The line for this commit, or None to keep the last one (a focus change says nothing new)."""
    kinds = {e.type for e in events}
    if before is None:
        if state.returning:   # the page's line for a caller this number has seen before (O-05)
            hello = f"Welcome back, {state.user_name}." if state.user_name else "Welcome back."
            return f"{hello} {state.previous_results or 'Here is your search from last time.'}"
        return "Tell me what you're looking for, and the map will follow."
    if "CallsCompleted" in kinds:
        n = len(after.calls)
        return f"All {n} call{'s' if n != 1 else ''} are done. Here's what I found." if n else None
    if after.phase == "closed" and before.phase == "open":
        return "Bye for now. Call again any time."
    if "CallsScheduled" in kinds or (after.phase == "calling" and before.phase == "open"):
        n = len(state.request.listing_ids) if state.request else len(after.plan)
        return f"Calling {n} place{'s' if n != 1 else ''} now. I'll text you what they say."
    if outcomes := [e for e in events if e.type == "OutcomeReady"]:
        lid = outcomes[-1].outcome.listing_id
        return _fit(_place(state, lid, after, catalogue), _place(state, lid, after, catalogue)[:1])
    if "CallsRequested" in kinds and state.request:
        ids = state.request.listing_ids
        names = [_address(catalogue, lid) for lid in ids]
        return _fit([f"I'll call {_join(names)} after you hang up."],
                    [f"I'll call these {len(ids)} places after you hang up."])
    ack = acknowledge([e for e in events if isinstance(e, PreferencesUpdated)])
    said = ([ack] if ack else []) + (_diff(before, after) if kinds & RERANKS else [])
    return _fit(said, ["Got it."]) if said else None


def acknowledge(updates: Sequence[PreferencesUpdated]) -> str | None:
    """"Got it: up to $2,600, light first." The first two things the renter changed, in a fixed order. A
    priority no feature ranks by isn't acknowledged; the line says so instead, and since it is also the
    Concierge's tool reply, the Concierge can ask what the renter meant."""
    parts: list[str] = []
    unranked: list[str] = []
    for u in updates:
        d = u.delta
        if d.max_monthly is not None:
            parts.append(f"up to {_money(d.max_monthly)}")
        if d.min_beds is not None:
            parts.append(BEDS.get(int(d.min_beds), f"{d.min_beds:g} bedrooms or more"))
        if d.areas:
            parts.append(f"around {_join(_cap(d.areas))}")
        if d.anchors:
            parts.append(f"near {_join(_cap([a.label for a in d.anchors]))}")
        if d.pets:
            parts.append(f"with your {_join(_cap(d.pets))}")
        if ranked := [p for p in d.priorities or () if feature_key(p)]:
            parts.append(f"{ranked[0].strip().lower()} first")
        unranked += [p.strip().lower() for p in d.priorities or () if not feature_key(p) and p.strip()]
        if d.must_have:
            parts.append(f"must have {_join(_cap(d.must_have))}")
    said = [f"Got it: {', '.join(parts[:2])}."] if parts else []
    if unranked:
        said.append(f"I can't rank by {_join(_cap(unranked))}.")
    return " ".join(said) or None


def rank_diff(before: SessionView, after: SessionView) -> str:
    """What the re-rank did, in the words a renter would use. One template per kind of change:
    empty, first board, new top (the old one dropped or left), top holds with a climber or newcomer,
    and unchanged. The count leads only when the filters changed how many places fit."""
    return " ".join(_diff(before, after))


def _diff(before: SessionView, after: SessionView) -> list[str]:
    if not after.board:
        return [WIDEN]
    top = after.board[0]
    if not before.board:
        return [f"{len(after.matches):,} places fit.", f"{_name(top, after.board)} is on top."]
    count = [f"{len(after.matches):,} places fit."] if len(after.matches) != len(before.matches) else []
    old = [c.listing_id for c in before.board]
    new = [c.listing_id for c in after.board]
    if old[0] != new[0]:
        prev = before.board[0]
        where = (f"{_name(prev, after.board)} drops to {_ordinal(new.index(prev.listing_id) + 1)}."
                 if prev.listing_id in new else f"{_name(prev, before.board)} is off the list.")
        return [*count, f"{_name(top, after.board)} moves to the top.", where]
    if old == new:
        return count or ["The list stays the same."]
    climbs = [(old.index(lid) - i if lid in old else len(old), i, c)
              for i, (lid, c) in enumerate(zip(new, after.board, strict=True)) if i]
    gain, i, card = max(climbs, key=lambda x: x[0])
    said = [*count, f"{_name(top, after.board)} stays on top."]
    if gain > 0:
        verb = "joins at" if card.listing_id not in old else "moves up to"
        said.append(f"{_name(card, after.board)} {verb} {_ordinal(i + 1)}.")
    return said


# The results


def results(state: SessionState, view: SessionView, catalogue: Catalogue) -> str:
    """The results of every call in the plan: bookings first, then the renter's order."""
    planned = state.plan.listing_ids if state.plan else [c.listing_id for c in state.calls.values()]
    order = sorted(dict.fromkeys(planned), key=lambda lid: lid not in state.viewings)
    return " ".join(s for lid in order for s in _place(state, lid, view, catalogue))


def _place(state: SessionState, lid: str, view: SessionView, catalogue: Catalogue) -> list[str]:
    """What one call found, as sentences: a booking, a red flag, rented, nobody answered, or the facts,
    then any conflict with the listing, where it moved on the board, and the landlord's questions."""
    addr = _address(catalogue, lid)
    out = state.outcomes.get(lid)
    status = state.status(lid)
    said: list[str] = []
    if status == "booked":
        said.append(f"I booked {addr} for {_when(state.viewings[lid].starts_at)}.")
    elif status == "suspect" and out and out.red_flags:
        said.append(f"{addr}: {_lower(out.red_flags[0]).rstrip('.')}, so I'd skip it.")
    elif status == "dead":
        said.append(f"{addr} is already rented.")
    elif out is None:
        said.append(f"I couldn't reach {addr} in time.")
    elif out.end_reason == "blocked":   # never dialled: no caller, or no allowlisted number
        said.append(f"I couldn't place the call to {addr}.")
    elif status == "no_answer":
        said.append(f"I left a message for {addr}'s agent." if out.end_reason == "voicemail"
                    else f"Nobody picked up at {addr}.")
    elif out.facts:
        facts = [f"{FIELDS.get(f.field, f.field.replace('_', ' '))}: {f.value.rstrip('.')}"
                 for f in out.facts if f.field != "available"][:MAX_FACTS]
        said.append(f"At {addr}, {'; '.join(facts)}." if facts else f"{addr} is still available.")
    if out and out.conflicts:
        c = out.conflicts[0]
        source = "the City record" if c.against == "city" else "the listing"
        said.append(f"{source[:1].upper()}{source[1:]} says {FIELDS.get(c.field, c.field)} {c.listed}, "
                    f"but the agent said {c.heard}.")
    if status in ("verified", "booked") and (moved := _moved(state, lid, view)):
        said.append(moved)
    asked = [*state.questions.get(lid, []), *(out.open_questions if out else [])]
    said += [f"Its agent asked, “{q.rstrip('?')}?” I said I'd check with you." for q in dict.fromkeys(asked)]
    return said


def _moved(state: SessionState, lid: str, view: SessionView) -> str | None:
    """Where a called place went on the board since the renter hung up."""
    was = state.ranks_at_hangup.get(lid)
    now = next((c.rank for c in view.board if c.listing_id == lid), None)
    if was is None or now is None or was == now:
        return None
    return f"It {'moves up' if now < was else 'drops'} to {_ordinal(now)}."


# Words


def _fit(sentences: Sequence[str], fallback: Sequence[str]) -> str:
    """Drop whole sentences from the end until the line is short enough to speak; if the first one alone
    is too long, say the fallback instead."""
    kept = list(sentences)
    while len(kept) > 1 and _words(kept) > MAX_WORDS:
        kept.pop()
    return " ".join(kept if _words(kept) <= MAX_WORDS else fallback)


def _words(sentences: Sequence[str]) -> int:
    return sum(len(s.split()) for s in sentences)


def _cap(items: Sequence[str], n: int = 2) -> list[str]:
    """At most `n` items read out, then "more"."""
    return [*items[:n], "more"] if len(items) > n else list(items)


def _name(card: Card, board: Sequence[Card]) -> str:
    """The address, with the unit when the board shows the same address twice."""
    twins = sum(c.address == card.address for c in board) > 1
    return f"{card.address}, unit {card.unit_label}" if twins and card.unit_label else card.address


def _address(catalogue: Catalogue, lid: str) -> str:
    listing = catalogue.by_id.get(lid)
    return listing.address if listing else "that place"


def _join(items: Sequence[str]) -> str:
    items = [i.strip() for i in items if i.strip()]
    if len(items) <= 2:
        return " and ".join(items)
    return f"{', '.join(items[:-1])} and {items[-1]}"


def _ordinal(n: int) -> str:
    if 1 <= n <= len(ORDINALS):
        return ORDINALS[n - 1]
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _money(n: int) -> str:
    return f"${n:,}"


def _when(at: datetime) -> str:
    local = at.astimezone(TORONTO)
    minutes = f":{local:%M}" if local.minute else ""
    return f"{local:%A} at {local.hour % 12 or 12}{minutes} {'pm' if local.hour >= 12 else 'am'}"


def _lower(s: str) -> str:
    return s[:1].lower() + s[1:]
