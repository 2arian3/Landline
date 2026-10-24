"""The Caller's tools (A-05): record_fact, note_question, book_viewing, end_call.

Each runs in code against the session runtime. The session, call and listing come from the `CallerContext`
the runner fills from the Dispatcher's `Dial`, never from the arguments (hard rule 2), and every event's
idem_key is the Higgs tool-call id. The reply is what the Caller hears back, so it says what to do next.

- record_fact keeps a fact only if its quote is something the landlord said (hard rule 3), checked with the
  Auditor's own `grounding` (A-07), so live and after-call facts pass the same test. Its reply hands the
  Caller the read-back sentence for any number, price, date or time.
- book_viewing turns the landlord's words into a Toronto datetime and books it only if the renter allowed
  booking, it is inside one of their windows, it doesn't clash with a viewing already booked in the plan,
  the listing isn't dead or suspect, and no red-flag rule (the Auditor's `flags`) matched this landlord's
  words. When the renter didn't allow booking, it notes the offered time for the renter instead, if the
  landlord said it.
- end_call with `done` is refused once while checklist items are still open; any other reason ends it.
"""
from __future__ import annotations

import json
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import ValidationError

from landline.agents.auditor import grounding
from landline.agents.auditor.flags import red_flags
from landline.agents.caller import slots
from landline.agents.caller.brief import Brief
from landline.contracts import EndReason, Fact, FactRecorded, QuestionNoted, ViewingBooked
from landline.contracts.tools import (
    CALLER_TOOLS,
    BookViewing,
    EndCall,
    NoteQuestion,
    RecordFact,
    caller_tools,
)
from landline.orchestrator import Refused, SessionRuntime

log = logging.getLogger(__name__)

TOOLS: list[dict[str, Any]] = caller_tools()
CLASH = timedelta(minutes=60)   # two viewings in one plan need this much between them
WHEN = re.compile(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b|\bnow\b|\bthis week\b|"
                  r"\bnext (week|month)\b|\bimmediately\b|\bright away\b|\d", re.I)
# The Caller promising to come back to the landlord on something.
PROMISE = re.compile(r"check with (my|the) client|get back to you|ask my client|"
                     r"pass (that|it|this) (on|along) to my client", re.I)
NOT_CAUGHT = "Sorry, I didn't catch that. Try again with the field, the value and their exact words."
UNKNOWN = "That tool doesn't exist."
BROKEN = "Something went wrong on my side. Carry on with the checklist."
SAY_GOODBYE = "You haven't said goodbye yet. Thank them and say goodbye now, in their language; the line " \
              "closes after it."


@dataclass
class CallerContext:
    session_id: str
    call_id: str
    listing_id: str
    brief: Brief
    runtime: SessionRuntime
    clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    said: list[str] = field(default_factory=list)     # the landlord's turns so far, for grounding and flags
    facts: list[Fact] = field(default_factory=list)   # recorded and grounded
    questions: list[str] = field(default_factory=list)
    booked: datetime | None = None
    slot_text: str | None = None
    ungrounded: int = 0                               # record_fact calls refused for a quote not said
    noted_by_code: int = 0                            # questions kept_promise noted for the Caller
    end_reason: EndReason | None = None
    asked_to_finish: bool = False                     # end_call(done) already refused once
    hang_up: bool = False                             # set by end_call; the runner ends after the goodbye
    bye_response: str | None = None                   # as in the Concierge (voice/bridge.py run_tool)
    readbacks: list[str] = field(default_factory=list)   # read-back sentences for the landlord's last turn
    readback_turn: int = -1                           # which landlord turn they belong to (len(said))

    def flags(self) -> list[str]:
        return [f.reason for f in red_flags(self.said)]


async def handle(name: str, arguments: str, tool_call_id: str, ctx: CallerContext) -> str:
    if name not in CALLER_TOOLS:
        log.warning("call %s: unknown tool %r", ctx.call_id, name)
        return UNKNOWN
    model, _ = CALLER_TOOLS[name]
    try:
        raw = json.loads(arguments or "{}")
        args = model.model_validate(raw if isinstance(raw, dict) else {})
    except (ValueError, ValidationError) as exc:
        log.warning("call %s: bad arguments for %s: %s", ctx.call_id, name, exc)
        return NOT_CAUGHT
    log.info("caller tool %s call=%s args=%s", name, ctx.call_id, args.model_dump_json(exclude_none=True))
    meta = {"session_id": ctx.session_id, "at": ctx.clock(), "idem_key": f"{ctx.call_id}:{tool_call_id}"}
    try:
        return await HANDLERS[name](args, ctx, meta)
    except Refused as exc:
        return str(exc)


async def record_fact(args: RecordFact, ctx: CallerContext, meta: dict) -> str:
    if not grounding.verify(args.quote, ctx.said):
        ctx.ungrounded += 1
        return ("Not recorded: that quote isn't what they said. Use their exact words, or ask again if you "
                "aren't sure.")
    await ctx.runtime.emit(FactRecorded(**meta, call_id=ctx.call_id, field=args.field, value=args.value,
                                        quote=args.quote, turn_idx=len(ctx.said) - 1))
    ctx.facts.append(Fact(field=args.field, value=args.value, quote=args.quote, speaker="Agent heard",
                          at=meta["at"]))
    ctx.brief.covered(args.field, args.value)
    when = ""
    if args.field == "available" and WHEN.search(args.quote) and \
            not any(f.field == "available_from" for f in ctx.facts):
        when = " They said when, so also record available_from with that date."
    elif args.field == "available" and not WHEN.search(args.quote) and \
            not any(f.field == "available_from" for f in ctx.facts) and "no" not in args.value.lower():
        when = " Next, ask from when it's available, then record available_from."
    left = ctx.brief.missing()
    back = _pending(ctx, readback(args.field, args.value))
    say = f'Start your reply with "{back}", then' if back else "Now"
    if left:
        nxt = f" {say} ask {left[0].ask}."
    elif back:
        nxt = f" {say} thank them and end the call: that's the checklist."
    else:
        nxt = " That's the checklist: thank them and end the call."
    return f"Recorded {args.field.replace('_', ' ')}: {args.value}.{when}{nxt}"


READBACK = {"rent": "the rent is", "parking_cost": "parking is", "parking": "parking is",
            "available_from": "it's available from", "available": "it's", "fees": "there's a",
            "sqft": "it's", "lease_months": "the lease is", "floor": "it's on", "other": ""}


def _pending(ctx: CallerContext, back: str | None) -> str | None:
    """Every read-back owed for the landlord's last turn, so the last tool reply in a turn still carries
    the ones before it (two facts in one turn, or a fact and a question)."""
    if ctx.readback_turn != len(ctx.said):
        ctx.readbacks, ctx.readback_turn = [], len(ctx.said)
    if back and back not in ctx.readbacks:
        ctx.readbacks.append(back)
    if not ctx.readbacks:
        return None
    first, *more = ctx.readbacks
    return " ".join([first, *("And " + b[3:] for b in more)])   # "So parking is $150. And the rent is …"


def readback(field: str, value: str) -> str | None:
    """The sentence that repeats a number, price, date or time back, from the recorded value; None when the
    value has none (a yes or no needs no read-back)."""
    if not (any(ch.isdigit() for ch in value) or WHEN.search(value)):
        return None
    lead = READBACK.get(field, f"for {field.replace('_', ' ')},")
    return f"So {lead + ' ' if lead else ''}{value.strip().rstrip('.')}."


async def note_question(args: NoteQuestion, ctx: CallerContext, meta: dict) -> str:
    await ctx.runtime.emit(QuestionNoted(**meta, call_id=ctx.call_id, question=args.question))
    if args.question not in ctx.questions:
        ctx.questions.append(args.question)
    left = ctx.brief.missing()
    also = (f" If that same turn of theirs also told you {left[0].ask}, record_fact it now, from their words."
            if left else "")
    back = _pending(ctx, None)
    first = f'Start your reply with "{back}", then tell' if back else "Tell"
    return f"Noted for the renter.{also} {first} them you'll check with your client, then carry on."


async def book_viewing(args: BookViewing, ctx: CallerContext, meta: dict) -> str:
    plan, b = ctx.brief.plan, ctx.brief
    windows = plan.windows
    if not plan.may_book:
        return await _offered(args.slot, ctx, meta)
    if ctx.booked is not None:
        return f"Already booked for {slots.spoken(ctx.booked)}. Don't book another."
    if ctx.flags():
        return ("Don't book: what they said matches a scam warning. Don't agree to anything; say you'll pass "
                "it to your client, and end the call.")
    state = ctx.runtime.state(ctx.session_id)
    if state.status(ctx.listing_id) in ("dead", "suspect"):
        return "Don't book this one. Thank them and end the call."
    at = slots.parse(args.slot, ctx.clock(), windows)
    if at is None:
        return "I couldn't tell which day and time that is. Ask them for a day and a time."
    if not slots.within(at, windows):
        return (f"{slots.spoken(at)} is outside the renter's times. Ask for a time "
                f"{_windows(windows)}.")
    clash = [v for lid, v in state.viewings.items()
             if lid != ctx.listing_id and abs(v.starts_at - at) < CLASH]
    if clash:
        taken = clash[0].starts_at
        return (f"The renter already has a viewing {slots.spoken(taken)}. Ask for another time "
                f"{_windows(windows)}, at least an hour before or after it.")
    await ctx.runtime.emit(ViewingBooked(**meta, call_id=ctx.call_id, listing_id=ctx.listing_id, starts_at=at,
                                         slot_text=args.slot))
    ctx.booked, ctx.slot_text = at, args.slot
    for item in b.items:
        if item.key == "viewing":
            item.done = True
    when = slots.spoken(at)
    return f"Booked for {when}. Start your reply with \"So that's {when},\" to confirm it."


async def _offered(slot: str, ctx: CallerContext, meta: dict) -> str:
    """Booking isn't allowed: the time they offered still goes back to the renter. Recorded as an `other`
    fact when the landlord said it (the slot's words are in their turns), so the viewing item is covered
    even when the Caller reaches for book_viewing instead of record_fact."""
    match = grounding.find(slot, ctx.said)
    if match is None:
        return "The renter didn't allow booking. Ask which times they could offer and record them as other."
    value = f"viewing offered: {slot}"
    if not any(f.field == "other" and f.value == value for f in ctx.facts):
        await ctx.runtime.emit(FactRecorded(**meta, call_id=ctx.call_id, field="other", value=value,
                                            quote=slot, turn_idx=match.source))
        ctx.facts.append(Fact(field="other", value=value, quote=slot, speaker="Agent heard", at=meta["at"]))
    for item in ctx.brief.items:
        if item.key == "viewing":
            item.done = True
    left = ctx.brief.missing()
    nxt = f"then ask {left[0].ask}." if left else "then thank them and end the call."
    return (f"Not booked: the renter books viewings themselves, so I noted {slot} for them. Start your reply "
            f"with \"So {slot} could work, I'll pass that to my client,\" {nxt}")


async def end_call(args: EndCall, ctx: CallerContext, meta: dict) -> str:
    left = ctx.brief.missing()
    if args.reason == "done" and left and not ctx.asked_to_finish and not ctx.flags():   # a scam ends now
        ctx.asked_to_finish = True
        return (f"Not done yet: nothing is recorded for {left[0].ask}. If they already told you, record_fact "
                "it now from their words; if not, ask. If they don't know, end the call again.")
    ctx.end_reason, ctx.hang_up = args.reason, True
    return "Goodbye."


async def kept_promise(ctx: CallerContext, caller_said: str, called: list[str]) -> str | None:
    """Note a landlord's question the Caller left behind, in their own words, so it still comes back in the
    results: when the Caller promised to check with its client but noted nothing that turn, or when the
    landlord asks the same question a second time and it was never noted. Code, not the model, decides
    this, like the goodbye guard. Returns the question noted, if any."""
    if "note_question" in called or not ctx.said:
        return None
    asked = _questions(ctx.said[-1])
    if not asked:
        return None
    earlier = {grounding.normalize(q) for turn in ctx.said[:-1] for q in _questions(turn)}
    repeated = [q for q in asked if grounding.normalize(q) in earlier]
    if PROMISE.search(caller_said):
        question = asked[-1]
    elif repeated:
        question = repeated[-1]
    else:
        return None
    if _noted(question, ctx.questions):
        return None
    await ctx.runtime.emit(QuestionNoted(session_id=ctx.session_id, at=ctx.clock(), call_id=ctx.call_id,
                                         idem_key=f"{ctx.call_id}:promised:{len(ctx.said)}",
                                         question=question))
    ctx.questions.append(question)
    ctx.noted_by_code += 1
    return question


def _questions(turn: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?？])\s*", turn) if s.strip().endswith(("?", "？"))]


def _noted(question: str, noted: list[str]) -> bool:
    """Already noted, word for word or in the Caller's own words (most of its words are in a noted one)."""
    words = set(grounding.tokens(question)) - {"your", "the", "a", "to", "for", "client", "renter", "my"}
    for n in noted:
        if n == question or (words and len(words & set(grounding.tokens(n))) >= 0.6 * len(words)):
            return True
    return False


HANDLERS: dict[str, Callable[[Any, CallerContext, dict], Awaitable[str]]] = {
    "record_fact": record_fact,
    "note_question": note_question,
    "book_viewing": book_viewing,
    "end_call": end_call,
}


def _windows(windows) -> str:
    if not windows:
        return "when the renter can make it"
    spoken = [f"{slots.spoken(w.start)} to {slots.spoken(w.end).split(' at ')[1]}" for w in windows]
    return "on " + (spoken[0] if len(spoken) == 1 else ", ".join(spoken[:-1]) + " or " + spoken[-1])
