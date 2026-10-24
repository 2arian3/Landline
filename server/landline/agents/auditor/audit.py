"""The Auditor (A-07): one listing call's transcript → its `CallOutcome`. Off the hot path.

Runs after `finalize()`, from the call runner, which hands the result to the Dispatcher as
`CallResult.outcome`; the Dispatcher emits it as `OutcomeReady` (contracts/events.py). Inputs are all
code's: the speaker-tagged turns, the listing, what the Caller recorded live (`record_fact`), the questions
it noted and the viewing it booked. The model, when there is one, only proposes facts.

1. Facts. Every quote must be in a *landlord* turn (`grounding.find`, exact or fuzzy ≥ 0.85), so the
   agent's own words, read-backs included, never count (PORTING #15). The value is read from that quote
   in code (`values.settle`), amounts included (#12, #13). The rest are dropped and counted as ungrounded.
2. Reconcile. The Caller's live facts and the transcript's are merged per field; the later turn wins, so a
   corrected read-back replaces the first answer. Each live fact is reported kept, corrected or dropped.
3. Conflicts. A heard value that differs from the listing, and a monthly cost recomputed in code from the
   heard rent and required fees.
4. Red flags, from rules over the landlord's words (`flags.py`).
5. The viewing the Caller booked; if only its words are known, they become a Toronto datetime in code (#16),
   with the Caller's own `slots.parse`, then `when.parse` for dates and Mandarin.

Code stamps each fact's time (#14). If the extractor fails or runs out of time, the rest still runs; if
the Auditor itself fails, the result is degraded: no facts, the end reason, and the transcript kept.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from landline.agents.auditor import extract as extractor
from landline.agents.auditor import grounding, when
from landline.agents.auditor.flags import Flag, red_flags
from landline.agents.auditor.values import Refused, Value, call_day, fee_name, settle
from landline.agents.caller import slots
from landline.contracts import CallOutcome, Conflict, EndReason, Fact, Viewing

log = logging.getLogger(__name__)

LANDLORD = "landlord"
TIMEOUT_S = 25.0
_DISPLAY_PETS = {"cats": "cats_only", "dogs": "dogs_only", "no": "no_pets", "yes": "allowed",
                 "cats_ok": "cats_only", "dogs_ok": "dogs_only", "none": "no_pets", "no_pets": "no_pets",
                 "allowed": "allowed", "unknown": None, "case_by_case": "case_by_case", "small": "small_only"}


@dataclass(frozen=True)
class Turn:
    idx: int
    speaker: str             # landlord | agent (the Caller); anything but landlord is not the landlord
    text: str
    lang: str | None = None


@dataclass(frozen=True)
class ToolFact:
    """A `record_fact` the Caller made live (FactRecorded)."""
    field: str
    value: str
    quote: str
    turn_idx: int | None = None


@dataclass(frozen=True)
class ListingContext:
    listing_id: str
    address: str = ""
    rent: int | None = None
    monthly_cost: int | None = None
    parking_cost: int | None = None
    pets: str | None = None
    laundry: str | None = None
    fees: tuple[dict[str, Any], ...] = ()

    @classmethod
    def of(cls, listing: Any) -> ListingContext:
        """From a `Listing`, a listing row, or a fixture's dict."""
        get = listing.get if isinstance(listing, dict) else lambda k, d=None: getattr(listing, k, d)
        fees = tuple(f if isinstance(f, dict) else f.model_dump() for f in get("fees", None) or ())
        rent = get("rent")
        monthly = get("monthly_cost") or (rent + sum(f["amount"] for f in fees if f.get("mandatory")
                                                      and f.get("period") == "monthly") if rent else None)
        unknown = {None, "", "unknown"}
        return cls(listing_id=str(get("listing_id", "") or ""), address=get("address", "") or "", rent=rent,
                   monthly_cost=monthly, parking_cost=get("parking_cost"),
                   pets=None if get("pets") in unknown else get("pets"),
                   laundry=None if get("laundry") in unknown else get("laundry"), fees=fees)

    def summary(self) -> str:
        bits = [self.address or "the unit"]
        if self.rent:
            bits.append(f"listed at ${self.rent:,} a month")
        return ", ".join(bits)


@dataclass(frozen=True)
class CallRecord:
    """Everything the Auditor reads, all from code."""
    call_id: str
    listing_id: str
    end_reason: EndReason
    turns: Sequence[Turn]
    listing: ListingContext
    ended_at: datetime
    tool_facts: Sequence[ToolFact] = ()
    questions: Sequence[str] = ()
    viewing: Viewing | None = None
    slot_text: str | None = None     # a booked slot's words, when the runner has no datetime for it


@dataclass(frozen=True)
class Heard:
    value: Value
    quote: str
    turn: int                # the landlord turn the quote is in
    source: str              # tool | transcript


@dataclass(frozen=True)
class Dropped:
    field: str
    quote: str
    source: str
    why: str                 # ungrounded | agent_words | unreadable


@dataclass
class Audit:
    outcome: CallOutcome
    heard: list[Heard] = field(default_factory=list)
    dropped: list[Dropped] = field(default_factory=list)
    flags: list[Flag] = field(default_factory=list)
    tool: dict[str, int] = field(default_factory=dict)    # live facts: kept, corrected, dropped
    extractor: str = "none"     # none | ok | failed | timeout
    degraded: bool = False


async def audit(call: CallRecord, *, complete: extractor.Complete | None = None,
                timeout_s: float = TIMEOUT_S) -> Audit:
    """The call's audit. Never raises: on any failure it returns the degraded result."""
    try:
        candidates, status = await _extract(call, complete, timeout_s)
        return build(call, candidates, status)
    except Exception:
        log.exception("audit of call %s failed; facts unknown, transcript kept", call.call_id)
        return degraded(call)


async def outcome(call: CallRecord, *, complete: extractor.Complete | None = None,
                  timeout_s: float = TIMEOUT_S) -> CallOutcome:
    """What a call runner puts in `CallResult.outcome`."""
    return (await audit(call, complete=complete, timeout_s=timeout_s)).outcome


def degraded(call: CallRecord) -> Audit:
    """Facts unknown, transcript kept: the call's turns stay in call_turns; the outcome claims nothing."""
    return Audit(CallOutcome(call_id=call.call_id, listing_id=call.listing_id, end_reason=call.end_reason,
                             open_questions=list(dict.fromkeys(call.questions)), viewing=call.viewing),
                 extractor="none", degraded=True)


def build(call: CallRecord, candidates: Sequence[extractor.Candidate], status: str = "none") -> Audit:
    landlord = [t for t in call.turns if t.speaker == LANDLORD]
    others = [t for t in call.turns if t.speaker != LANDLORD]
    result = Audit(CallOutcome(call_id=call.call_id, listing_id=call.listing_id, end_reason=call.end_reason),
                   extractor=status)

    def ground(f: str, quote: str, source: str) -> Turn | None:
        m = grounding.find(quote, [t.text for t in landlord])
        if m is not None:
            return landlord[m.source]
        why = "agent_words" if grounding.find(quote, [t.text for t in others]) else "ungrounded"
        result.dropped.append(Dropped(f, quote, source, why))
        return None

    day = call_day(call.ended_at)   # "tomorrow" counts from the call's own day (#138)
    live: list[Heard] = []
    for tf in call.tool_facts:
        turn = ground(tf.field, tf.quote, "tool")
        if turn is None:
            continue
        try:
            live.append(Heard(settle(tf.field, tf.value, tf.quote, today=day), tf.quote, turn.idx, "tool"))
        except Refused as e:
            result.dropped.append(Dropped(tf.field, tf.quote, "tool", f"unreadable: {e}"))
    read: list[Heard] = []
    for c in candidates:
        turn = ground(c.field, c.quote, "transcript")
        if turn is None:
            continue
        try:
            v = settle(c.field, c.value, c.quote, amount=c.amount, count=c.count, each=c.each,
                       period=c.period, mandatory=c.mandatory, name=c.name, today=day)
        except Refused as e:
            result.dropped.append(Dropped(c.field, c.quote, "transcript", f"unreadable: {e}"))
            continue
        read.append(Heard(v, c.quote, turn.idx, "transcript"))
        if c.field == "parking" and c.amount is not None:   # a price said with "parking" is the spot's price
            try:
                read.append(Heard(settle("parking_cost", c.value, c.quote, amount=c.amount, count=c.count,
                                         each=c.each), c.quote, turn.idx, "transcript"))
            except Refused:
                pass

    result.heard = merge(live + read)
    kept = {id(h) for h in result.heard}
    result.tool = {"recorded": len(call.tool_facts), "kept": sum(id(h) in kept for h in live),
                   "corrected": sum(id(h) not in kept for h in live),
                   "dropped": sum(d.source == "tool" for d in result.dropped)}
    result.flags = red_flags([t.text for t in landlord])
    viewing = call.viewing or _viewing(call.slot_text, call.ended_at)
    result.outcome = CallOutcome(
        call_id=call.call_id, listing_id=call.listing_id, end_reason=call.end_reason,
        facts=[Fact(field=h.value.field, value=h.value.display, quote=h.quote, speaker="Agent heard",
                    at=call.ended_at) for h in result.heard],
        conflicts=conflicts(result.heard, call.listing),
        red_flags=[f.reason for f in result.flags],
        open_questions=list(dict.fromkeys(call.questions)),
        viewing=viewing,
    )
    for d in result.dropped:
        log.info("call %s: dropped %s fact from %s (%s): %r", call.call_id, d.field, d.source, d.why, d.quote)
    return result


def merge(heard: Sequence[Heard]) -> list[Heard]:
    """One value per field, the latest landlord turn winning (a correction replaces the first answer). Fees
    are one per fee name; `other`, `light` and `outdoor_space` keep every distinct answer. On the same turn
    the Caller's live record and the transcript agree or the transcript, read whole, wins."""
    order = sorted(heard, key=lambda h: (h.turn, h.source == "transcript"))
    keep: dict[tuple[str, str], Heard] = {}
    for h in order:
        v = h.value
        if v.field == "fees":
            key = ("fees", v.name or "other")
        elif v.field in ("other", "light", "outdoor_space"):
            key = (v.field, v.value.lower())
        else:
            key = (v.field, "")
        keep[key] = h
    parking = keep.get(("parking", ""))
    if ("parking_cost", "") in keep and parking is not None and parking.value.value == "available":
        del keep[("parking", "")]   # a price says parking is available; one fact, not two
    return sorted(keep.values(), key=lambda h: h.turn)


def conflicts(heard: Sequence[Heard], listing: ListingContext) -> list[Conflict]:
    by = {h.value.field: h for h in heard if h.value.field != "fees"}
    out: list[Conflict] = []

    def money(field: str, listed: int | None) -> None:
        h = by.get(field)
        if h and listed is not None and h.value.amount is not None and h.value.amount != listed:
            out.append(Conflict(field=field, listed=f"${listed:,}", heard=f"${h.value.amount:,}",
                                quote=h.quote))

    money("rent", listing.rent)
    money("parking_cost", listing.parking_cost)
    for f, listed in (("pets", _DISPLAY_PETS.get(str(listing.pets).lower(), listing.pets)),
                      ("laundry", listing.laundry)):
        h = by.get(f)
        if h and listed and h.value.value != listed:
            out.append(Conflict(field=f, listed=str(listed).replace("_", " "), heard=h.value.display,
                                quote=h.quote))
    if (h := by.get("available")) and h.value.value == "no":
        out.append(Conflict(field="available", listed="listed as available", heard=h.value.display,
                            quote=h.quote))
    cost = monthly_cost(heard, listing)
    if cost is not None and listing.monthly_cost is not None and cost[0] != listing.monthly_cost:
        out.append(Conflict(field="monthly_cost", listed=f"${listing.monthly_cost:,} a month",
                            heard=f"${cost[0]:,} a month", quote=cost[1]))
    return out


def monthly_cost(heard: Sequence[Heard], listing: ListingContext) -> tuple[int, str] | None:
    """Rent plus required monthly fees, computed in code from what was heard over what was listed. None
    when the call changed neither the rent nor a required monthly fee. Returns the cost and the quote that
    moved it most recently."""
    rent_h = next((h for h in heard if h.value.field == "rent"), None)
    fees_h = [h for h in heard
              if h.value.field == "fees" and h.value.period == "monthly" and h.value.mandatory]
    if rent_h is None and not fees_h:
        return None
    rent = rent_h.value.amount if rent_h else listing.rent
    if rent is None:
        return None
    fees = {fee_name(str(f.get("name", ""))): f["amount"] for f in listing.fees
            if f.get("mandatory") and f.get("period") == "monthly"}
    for h in fees_h:
        fees[h.value.name or "other"] = (h.value.amount or 0) * h.value.count
    movers = [h for h in [rent_h, *fees_h] if h is not None]
    return rent + sum(fees.values()), max(movers, key=lambda h: h.turn).quote


def _viewing(slot_text: str | None, at: datetime) -> Viewing | None:
    if not slot_text:
        return None
    # The parser the Caller booked with first, so both read a slot the same way; ours adds dates and Mandarin.
    starts = slots.parse(slot_text, at) or when.parse(slot_text, at)
    return Viewing(starts_at=starts, slot_text=slot_text) if starts else None


async def _extract(call: CallRecord, complete: extractor.Complete | None,
                   timeout_s: float) -> tuple[list[extractor.Candidate], str]:
    if complete is None or not any(t.speaker == LANDLORD for t in call.turns):
        return [], "none"
    try:
        async with asyncio.timeout(timeout_s):
            return await extractor.extract(complete, call.turns, call.listing.summary()), "ok"
    except TimeoutError:
        log.warning("call %s: transcript extractor timed out; using the live record only", call.call_id)
        return [], "timeout"
    except Exception:
        log.exception("call %s: transcript extractor failed; using the live record only", call.call_id)
        return [], "failed"
