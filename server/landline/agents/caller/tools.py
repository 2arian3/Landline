"""The Caller's tools (A-05): record_fact, record_available_from, note_question, book_viewing, end_call.

Each runs in code against the session runtime. The session, call and listing come from the `CallerContext`
the runner fills from the Dispatcher's `Dial`, never from the arguments (hard rule 2), and every event's
idem_key is the Higgs tool-call id. The reply is what the Caller hears back, so it says what to do next.

- record_fact keeps a fact only if its quote is something the landlord said (hard rule 3), checked with the
  Auditor's own `grounding` (A-07), so live and after-call facts pass the same test. The same call again in
  one turn is a no-op, and a turn is capped, so a retry doesn't loop (#215). Its reply hands the Caller the
  read-back sentence for any number, price, date or time, or the next checklist item when nothing was kept.
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
from landline.agents.auditor.numbers import amounts
from landline.agents.auditor.values import bare, call_day, fee_name, free_from, from_parts, spoken_day
from landline.agents.caller import slots
from landline.agents.caller.brief import CONFIRM_LINE, Brief, disclosure
from landline.contracts import EndReason, Fact, FactRecorded, QuestionNoted, Shareable, ViewingBooked
from landline.contracts.tools import (
    CALLER_TOOLS,
    AvailableFrom,
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
MAX_FACT_CALLS = 8              # record_fact calls in one landlord turn; the rest are a no-op (#215)
WHEN = re.compile(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b|\bnow\b|\bthis week\b|"
                  r"\bnext (week|month)\b|\bimmediately\b|\bright away\b|\d", re.I)
# The Caller promising to come back to the landlord on something.
PROMISE = re.compile(r"check with (my|the) client|get back to you|ask my client|"
                     r"pass (that|it|this) (on|along) to my client", re.I)
NOT_CAUGHT = "Sorry, I didn't catch that. Try again with the field, the value and their exact words."
UNSAID = ("Not recorded: that quote isn't what they said. Use their exact words, or ask again if you aren't "
          "sure.")
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
    refused: list[datetime] = field(default_factory=list)   # times book_viewing turned down this call
    ungrounded: int = 0                               # record_fact calls refused for a quote not said
    noted_by_code: int = 0                            # questions kept_promise noted for the Caller
    end_reason: EndReason | None = None
    asked_to_finish: bool = False                     # end_call(done) already refused once
    hang_up: bool = False                             # set by end_call; the runner ends after the goodbye
    bye_response: str | None = None                   # as in the Concierge (voice/bridge.py run_tool)
    readbacks: list[str] = field(default_factory=list)   # read-back sentences for the landlord's last turn
    readback_turn: int = -1                           # which landlord turn they belong to (len(said))
    flagged_at: int | None = None                     # the landlord turn a scam warning first matched
    covered_at: int | None = None                     # the landlord turn by which the checklist was covered
    ended_by_code: bool = False                       # `over` ended it: the Caller never called end_call
    opened: bool = False                              # its first turn is over, so the disclosure is said
    unnoted: int = 0                                  # offered times _offered couldn't note
    spoke: bool = False                               # this response spoke before its tool call (run_tool)
    fact_turn: int = -1                               # len(said) that fact_calls belongs to
    fact_calls: list[tuple[str, str, str]] = field(default_factory=list)  # record_fact this turn
    hold_tools: bool = False                          # identical record_fact: don't ask the model again

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
    # A new thing they said lifts the brake from the previous turn, so the next fact can still be answered.
    if ctx.fact_turn != len(ctx.said):
        ctx.hold_tools = False
    try:
        reply = wrap_up(ctx, name, await HANDLERS[name](args, ctx, meta))
    except Refused as exc:
        reply = str(exc)
    return first_words(ctx, reply)


def first_words(ctx: CallerContext, reply: str) -> str:
    """Before the Caller's first turn is over, a tool reply leads with the disclosure (hard rule 6). A
    landlord who picks up with a fact gets a tool call first, and its "Start your reply with" put the
    read-back ahead of "I'm an AI assistant" (omar-scam-abroad-3). Once the first turn is over, replies are
    as they were."""
    if ctx.opened:
        return reply
    reply = reply.replace("Start your reply with", "After it, say")
    return f'Your first words are the disclosure: "{disclosure(ctx.brief)}" Then: {reply}'


def _roll_facts(ctx: CallerContext) -> None:
    """fact_calls counts one landlord turn. Their next sentence starts it over."""
    turn = len(ctx.said)
    if ctx.fact_turn != turn:
        ctx.fact_turn = turn
        ctx.fact_calls = []
        ctx.hold_tools = False


def _move_on(ctx: CallerContext) -> str:
    """What to say instead of calling record_fact again (#215). No "ask again": that reply is what made the
    model call the same fact twenty-one times and never speak."""
    left = ctx.brief.missing()
    nxt = f"Ask {left[0].ask} next." if left else "Thank them and end the call."
    return f"{nxt} Don't call record_fact again until they say something new."


def _quote_ok(args: RecordFact, ctx: CallerContext) -> bool:
    """The quote has to be in the landlord's turns. "No." is not in a bare "Yeah.", so that fact is dropped
    (#215) instead of stored as the opposite of what they said."""
    return grounding.verify(args.quote, ctx.said)


async def record_fact(args: RecordFact, ctx: CallerContext, meta: dict) -> str:
    _roll_facts(ctx)
    sig = (args.field, args.value.strip(), args.quote.strip())
    if sig in ctx.fact_calls or len(ctx.fact_calls) >= MAX_FACT_CALLS:
        # The same arguments again, or a turn that's already full: nothing is written, and the runner
        # doesn't ask the model for another reply (hold_tools), which is how the loop stayed alive.
        ctx.hold_tools = True
        if any(f.field == args.field and f.quote.strip() == sig[2] for f in ctx.facts):
            return await _record(args, ctx, meta)
        return f"Already noted. {_move_on(ctx)}"
    ctx.fact_calls.append(sig)
    if not _quote_ok(args, ctx):
        ctx.ungrounded += 1
        return f"Not recorded: that quote isn't what they said. {_move_on(ctx)}"
    if args.field == "fees":
        fees = _fees(args, ctx)
        if isinstance(fees, str):
            return fees
        args = fees or args
    if args.field == "available_from":   # the day comes from their words, in code, never the model's value
        today = call_day(meta["at"])
        if (got := free_from(args.quote, today)) is None:
            return "Not recorded: call record_available_from for when it's free, with the parts they said."
        args = args.model_copy(update={"value": spoken_day(got, today)})
    return await _record(args, ctx, meta)


FEES_WHICH = ("Not recorded yet: they said there are fees. Ask which ones and how much, then record_fact "
              "fees from their answer.")
FEES_LATEST = "Not recorded: record fees from what they just said, in their latest words."


def _fees(args: RecordFact, ctx: CallerContext) -> RecordFact | str | None:
    """A fees answer (#175). When their latest turn is a bare yes or no, it answers the fees question, so a
    quote from an earlier turn is refused (c_yVmI3iydTn kept "$100 parking" after "No, nothing else.").
    A bare "Yeah" to "any monthly fees?" isn't a fee, so the reply asks which and how much; a bare "No" is
    fees: none."""
    latest = ctx.said[-1] if ctx.said else ""
    if bare(latest) is not None and not grounding.verify(args.quote, [latest]):
        return FEES_LATEST
    said = bare(args.quote)
    if said == "yes" and not any(n >= 10 for n in amounts(args.quote)):   # "Yes, still $150" says how much
        return FEES_WHICH
    if said == "no":
        return args.model_copy(update={"value": "none"})
    return None


async def record_available_from(args: AvailableFrom, ctx: CallerContext, meta: dict) -> str:
    """When it's free, from the Caller's parts (#157): code makes the day from the call's day in Toronto. The
    quote must be the landlord's words, and when those words plainly name another day nothing is kept."""
    if not grounding.verify(args.quote, ctx.said):
        ctx.ungrounded += 1
        return UNSAID
    today = call_day(meta["at"])
    day = from_parts(args.kind, today, weekday=args.weekday, weeks=args.weeks, month=args.month, day=args.day)
    if day is None:
        return "Not recorded: that names no day. Ask them from when it's free, or move on."
    said = free_from(args.quote, today)
    if said is not None and said != day:
        return (f"Not recorded: their words sound like {spoken_day(said, today)}, not "
                f"{spoken_day(day, today)}. Ask them to confirm the day.")
    fact = RecordFact(field="available_from", value=spoken_day(day, today), quote=args.quote)
    return await _record(fact, ctx, meta)


async def _record(args: RecordFact, ctx: CallerContext, meta: dict) -> str:
    """Keep a grounded fact, and say what to do next."""
    value = args.value.strip().lower()
    asked = ctx.brief.answers(args.value) if args.field == "other" else None   # a renter's question (#187)
    asks = ctx.brief.plan.questions
    if asked is None and any(f.field == args.field and _answer(f.value, asks).strip().lower() == value
                             for f in ctx.facts):
        # The same answer again: the Caller asked twice (#139). A different answer is a correction and is
        # recorded; the Auditor keeps the last.
        left = ctx.brief.missing()
        nxt = f"Don't ask about it again. Ask {left[0].ask} next." if left else \
            "That's the checklist: thank them and end the call."
        return f"Already recorded {args.field.replace('_', ' ')}: {args.value}. {nxt}"
    # The answer to a renter's question keeps the question, so the results can say what was asked.
    stored = f"{asked.strip()} {args.value.strip()}" if asked else args.value
    await ctx.runtime.emit(FactRecorded(**meta, call_id=ctx.call_id, field=args.field, value=stored,
                                        quote=args.quote, turn_idx=len(ctx.said) - 1))
    ctx.facts.append(Fact(field=args.field, value=stored, quote=args.quote, speaker="Agent heard",
                          at=meta["at"]))
    ctx.brief.covered(args.field, args.value)
    if args.field == "fees" and fee_name(f"{args.value} {args.quote}") == "pet_fee":
        ctx.brief.covered("pets")   # a pet fee answers whether pets are allowed; the Auditor files both
    when = ""
    if args.field == "available" and WHEN.search(args.quote) and \
            not any(f.field == "available_from" for f in ctx.facts):
        when = " They said when, so also call record_available_from with the parts of what they said."
    elif args.field == "available" and not WHEN.search(args.quote) and \
            not any(f.field == "available_from" for f in ctx.facts) and "no" not in args.value.lower():
        when = " Next, ask from when it's available, then call record_available_from."
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


def _answer(value: str, questions: list[str]) -> str:
    """An `other` fact's answer, without the renter's question it keeps in front (#187)."""
    q = next((q.strip() for q in questions if value.startswith(f"{q.strip()} ")), None)
    return value[len(q) + 1:] if q else value


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
    if field == "available_from" and value.strip().lower() == "now":   # code's word for today (#157)
        return "So it's available now."
    lead = READBACK.get(field, f"for {field.replace('_', ' ')},")
    return f"So {lead + ' ' if lead else ''}{value.strip().rstrip('.')}."


async def note_question(args: NoteQuestion, ctx: CallerContext, meta: dict) -> str:
    await _note(ctx, args.question, meta["idem_key"], meta["at"])
    left = ctx.brief.missing()
    also = (f" If that same turn of theirs also told you {left[0].ask}, record_fact it now, from their words."
            if left else "")
    back = _pending(ctx, None)
    first = f'Start your reply with "{back}", then tell' if back else "Tell"
    return f"Noted for the renter.{also} {first} them you'll check with your client, then carry on."


async def book_viewing(args: BookViewing, ctx: CallerContext, meta: dict) -> str:
    plan, b = ctx.brief.plan, ctx.brief
    windows = plan.windows
    now = ctx.clock()
    if (day := slots.day_only(args.slot, now)) is not None:   # "Yeah, tomorrow you can": no time yet (#189)
        return _ask_time(day, windows, now)
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
    at = slots.parse(args.slot, now, windows, keep_past_today=True)
    if at is None:
        return "I couldn't tell which day and time that is. Ask them for a day and a time."
    if at <= now or not slots.within(at, windows):
        if at in ctx.refused:   # they keep offering the same time: pass it on instead of going round again
            await _note_offer(args.slot, ctx, meta)
            left = ctx.brief.missing()
            nxt = f"then ask {left[0].ask}." if left else "then thank them and end the call."
            return (f"Not booked: they keep offering {slots.spoken(at)}, which the renter can't make, so I "
                    f"noted it for them. Don't offer times again. Say you'll pass it to your client, {nxt}")
        ctx.refused.append(at)
        why = ("That time has already passed." if at <= now
               else f"{slots.spoken(at)} is outside the renter's times.")
        if (left := slots.open_times(windows, now)) is None:
            return (f"{why} The renter's viewing times have all passed, so don't book. Say your client will "
                    f"be in touch about a viewing, and carry on.")
        if names := slots.bound_days(windows):   # the default bounds are not hours to offer (#218)
            return (f"{why} Ask what time on {names} works. Don't name hours as when the renter can view. "
                    "Don't repeat your last question.")
        return f"{why} Offer them a time {left}. Don't repeat your last question."
    clash = [v for lid, v in state.viewings.items()
             if lid != ctx.listing_id and abs(v.starts_at - at) < CLASH]
    if clash:
        taken = clash[0].starts_at
        return (f"The renter already has a viewing {slots.spoken(taken)}. Ask for another time "
                f"{_windows(windows)}, at least an hour before or after it.")
    await ctx.runtime.emit(ViewingBooked(**meta, call_id=ctx.call_id, listing_id=ctx.listing_id, starts_at=at,
                                         slot_text=args.slot))
    ctx.booked, ctx.slot_text = at, args.slot
    ctx.questions = [q for q in ctx.questions if not about_viewing(q)]   # the booking answered them
    for item in b.items:
        if item.key == "viewing":
            item.done = True
    when = slots.spoken(at)
    told = f' Then say once: "{CONFIRM_LINE}"' if b.texts_confirm else ""   # O-08b: a number to text
    return (f"Booked for {when}. Start your reply with \"So that's {when}.\" Say it once; if they already "
            f"agreed, don't ask them to confirm it.{told}")


def _ask_time(day, windows, now: datetime) -> str:
    """They said a day but no time: nothing is booked or noted until they give one in the renter's times."""
    today = now.astimezone(slots.TORONTO).date()
    said = "today" if day == today else "tomorrow" if (day - today).days == 1 else f"on {day:%A}"
    that_day = [w for w in windows if w.start.astimezone(slots.TORONTO).date() == day]
    if that_day and all(slots.day_bound(w) for w in that_day):
        name = slots.WEEKDAYS[day.weekday()].capitalize()
        return (f"They said {said} but no time. Ask what time on {name} works. "
                "Don't name hours as when the renter can view. Then pass it to book_viewing.")
    left = slots.open_times(that_day, now)
    can = f": the renter can do {left}" if left else ""
    return (f"They said {said} but no time. Ask what time {said} works for them{can}. "
            f"Then pass it to book_viewing.")


async def _offered(slot: str, ctx: CallerContext, meta: dict) -> str:
    """Booking isn't allowed: the time they offered still goes back to the renter. Recorded as an `other`
    fact when the landlord said it (the slot's words are in their turns), so the viewing item is covered
    even when the Caller reaches for book_viewing instead of record_fact."""
    if not await _note_offer(slot, ctx, meta):
        ctx.unnoted += 1
        if ctx.unnoted > 1:   # never round again (priya asked for a time three times; #156's repeat rule)
            left = [i for i in ctx.brief.missing() if i.key != "viewing"]
            nxt = f"then ask {left[0].ask}." if left else "then thank them and end the call."
            return f"Don't ask about times again. Say you'll pass the viewing time to your client, {nxt}"
        return "The renter didn't allow booking. Ask which times they could offer and record them as other."
    left = ctx.brief.missing()
    nxt = f"then ask {left[0].ask}." if left else "then thank them and end the call."
    return (f"Not booked: the renter books viewings themselves, so I noted {slot} for them. Start your reply "
            f"with \"So {slot} could work, I'll pass that to my client,\" {nxt}")


async def _note_offer(slot: str, ctx: CallerContext, meta: dict) -> bool:
    """The time they offered goes back to the renter as an `other` fact, when the landlord said it (the slot's
    words are in their turns), and covers the viewing item. False when they didn't say it."""
    match = grounding.find(slot, ctx.said)
    quote, source = slot, match.source if match else -1
    if match is None:
        # Their words in another order (priya: "6:30 pm on Thursday" for "Thursday at 6:30 pm", asked three
        # times): the same time named in their latest turn counts, quoted from that turn.
        # A time alone ("6:30 pm works perfectly") answers the day the Caller asked about, when that's one of
        # the renter's days.
        now, windows = ctx.clock(), ctx.brief.plan.windows
        at = slots.parse(slot, now, windows)
        latest = ctx.said[-1] if ctx.said else ""
        said = slots.parse(latest, now, windows) or (
            slots.parse(f"{latest} {at.astimezone(slots.TORONTO):%A}", now, windows)
            if at is not None and slots.within(at, windows) else None)
        if at is None or said != at:
            return False
        quote, source = ctx.said[-1], len(ctx.said) - 1
    value = f"viewing offered: {slot}"
    if not any(f.field == "other" and f.value == value for f in ctx.facts):
        await ctx.runtime.emit(FactRecorded(**meta, call_id=ctx.call_id, field="other", value=value,
                                            quote=quote, turn_idx=source))
        ctx.facts.append(Fact(field="other", value=value, quote=quote, speaker="Agent heard", at=meta["at"]))
    for item in ctx.brief.items:
        if item.key == "viewing":
            item.done = True
    return True


async def end_call(args: EndCall, ctx: CallerContext, meta: dict) -> str:
    left = ctx.brief.missing()
    # "Not done yet" only before a goodbye (#175): after theirs, or the Caller's own in this response, it
    # hangs up.
    goodbye = ctx.spoke or bool(ctx.said and BYE.search(ctx.said[-1]))
    if args.reason == "done" and left and not ctx.asked_to_finish and not ctx.flags() and not goodbye:
        ctx.asked_to_finish = True
        return (f"Not done yet: nothing is recorded for {left[0].ask}. If they already told you, record_fact "
                "it now from their words; if not, ask. If they don't know, end the call again.")
    ctx.end_reason, ctx.hang_up = args.reason, True
    return "Goodbye."


# ── the call ends itself ────────────────────────────────────────────────────
# On the team's phone test the Caller said goodbye and never called end_call, so the line stayed open. The
# tool replies say to end in the same turn, and `over` ends the call in code when the Caller still doesn't.

END_CALL = ("say one short thank-you and goodbye, in their language, and call end_call with done in this "
            "same turn")
END_NOW = f"{END_CALL}. Ask nothing more."
SCAM_END = ("What they said matches a scam warning, so stop the checklist: say you'll pass it on to your "
            f"client, then {END_NOW}")
OLD_END = "thank them and end the call"
# The Caller's or the landlord's goodbye.
BYE = re.compile(r"\b(good ?bye|bye|have a (great|good|nice|lovely|wonderful) "
                 r"(day|evening|night|one|weekend)|take care|you too|hang up)\b", re.I)


def wrap_up(ctx: CallerContext, name: str, reply: str) -> str:
    """A tool's reply once the call is over: a scam warning stops the checklist, and a covered checklist
    ends the call in this same turn."""
    if name == "end_call" or ctx.hang_up:
        return reply
    if ctx.flags():
        return SCAM_END
    if ctx.brief.missing():
        return reply
    if OLD_END in reply:
        return reply.replace(OLD_END, END_CALL)
    return f"{reply} Then {END_NOW}"


def over(ctx: CallerContext, caller_said: str, called: list[str]) -> bool:
    """After a Caller turn that didn't end the call: whether it is over anyway, so the runner ends it in code
    (through its usual return, so `finalize` runs once). It is when the Caller or the landlord said
    goodbye, or a turn has passed since a scam warning or since the checklist was covered. A call still
    on its checklist goes on. The runners call it after every Caller turn, so it also marks the first one
    over (`first_words`)."""
    ctx.opened = True
    if ctx.hang_up or "end_call" in called:
        return False
    turn = len(ctx.said)
    if ctx.flagged_at is None and ctx.flags():
        ctx.flagged_at = turn
    if ctx.covered_at is None and not ctx.brief.missing():
        ctx.covered_at = turn
    said_bye = bool(BYE.search(caller_said) or (ctx.said and BYE.search(ctx.said[-1])))
    late = any(at is not None and turn > at for at in (ctx.flagged_at, ctx.covered_at))
    if not (said_bye or late):
        return False
    ctx.end_reason = ctx.end_reason or "done"
    ctx.ended_by_code = True
    log.info("call %s: over (%s), ending it in code", ctx.call_id, "goodbye" if said_bye else "a turn late")
    return True


async def kept_promise(ctx: CallerContext, caller_said: str, called: list[str]) -> str | None:
    """Note a landlord's question the Caller left behind, in their own words, so it still comes back in the
    results: when the Caller promised to check with its client but noted nothing that turn, when the
    landlord asks the same question a second time and it was never noted, or when they asked about the
    renter and what the renter shares doesn't answer it (raj-upsell: the Caller talked past "Would your
    client want the premium storage locker?" and promised nothing). Code, not the model, decides this, like
    the goodbye guard. Returns the last question noted, if any."""
    if "note_question" in called or not ctx.said:
        return None
    asked = _questions(ctx.said[-1])
    if not asked:
        return None
    earlier = {grounding.normalize(q) for turn in ctx.said[:-1] for q in _questions(turn)}
    repeated = [q for q in asked if grounding.normalize(q) in earlier]
    left = {asked[-1]} if PROMISE.search(caller_said) else {repeated[-1]} if repeated else set()
    left |= {q for q in asked if about_renter(q, ctx.brief.plan.shareable)}
    noted = None
    for n, question in enumerate(asked):
        if question not in left or _noted(question, ctx.questions) or \
                (about_viewing(question) and ctx.booked is not None):
            continue
        await _note(ctx, question, f"{ctx.call_id}:promised:{len(ctx.said)}:{n}", ctx.clock())
        ctx.noted_by_code += 1
        noted = question
    return noted


# A landlord's question about the renter: who they are, what they'd pay for, what they'd agree to.
SENSITIVE = re.compile(
    r"\b(income|guarantor|co-?signer|credit|employ\w*|jobs?|salary|earn\w*|for work|for a living|references?|"
    r"pay\w*|stubs?|students?|smok\w*|deposit|background|documents?|application|fees?|cost\w*|price|"
    r"dollars|locker|storage|sign)\b|\$\d", re.I)
ABOUT_RENTER = re.compile(
    r"\b(your|the|my) (client|renter)s?\b|\b(your|the) (client|renter)'s\b|"
    r"\b(would|do|does|will) (you|they|he|she)( \w+)? (want|like|need|take)\b", re.I)
WRAP_UP = re.compile(r"\b(anything else|other questions?|any questions?)\b", re.I)
# What the renter's shareable answers cover, by the Shareable field the Caller needs for it.
SHAREABLE = {"move_in": re.compile(r"\bmov(e|es|ing)\b|\bstart\b", re.I),
             "occupants": re.compile(r"\bhow many\b|\bpeople\b|\boccupants?\b|\balone\b|\broommates?\b|"
                                     r"\bliv(e|es|ing) (there|with|in)\b", re.I),
             "pets": re.compile(r"\bpets?\b|\bdogs?\b|\bcats?\b|\banimals?\b", re.I)}


def about_renter(question: str, share: Shareable) -> bool:
    """A question about the renter that the Caller may not answer from what the renter shares, so it has to
    go back to the renter. A viewing question is left to book_viewing."""
    if about_viewing(question) or WRAP_UP.search(question):
        return False
    if SENSITIVE.search(question):
        return True
    if not ABOUT_RENTER.search(question):
        return False
    return not any(rx.search(question) and getattr(share, key) for key, rx in SHAREABLE.items())


# A landlord asking to set up a viewing ("Would you like to schedule a viewing?", "When can you come by?").
VIEWING_QUESTION = re.compile(
    r"\b(view(ing)?|visit|tour|showing|come (by|see|over|and see)|see (it|the (unit|place|apartment))|"
    r"schedul\w*|book\w*|appointment|open house|(that|this|what) time)\b", re.I)


def about_viewing(question: str) -> bool:
    return bool(VIEWING_QUESTION.search(question))


async def _note(ctx: CallerContext, question: str, idem_key: str, at: datetime) -> None:
    """Keep a landlord's question for the renter. One about setting up a viewing stays with this call
    only until the call ends, so a viewing booked in the same call closes it: it reaches the renter
    through the outcome when nothing was booked, and is dropped when something was. Any other question
    is noted on the session at once."""
    if about_viewing(question):
        if ctx.booked is None and question not in ctx.questions:
            ctx.questions.append(question)
        return
    await ctx.runtime.emit(QuestionNoted(session_id=ctx.session_id, at=at, call_id=ctx.call_id,
                                         idem_key=idem_key, question=question))
    if question not in ctx.questions:
        ctx.questions.append(question)


def _questions(turn: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?？])\s*", turn) if s.strip().endswith(("?", "？"))]


# Words every question to the renter shares, so "Does your client have a car?" isn't taken for a noted
# "Does your client have a guarantor?".
QUESTION_WORDS = {"your", "the", "a", "an", "to", "for", "client", "renter", "my", "do", "does", "did", "is",
                  "are", "have", "has", "would", "will", "can", "could", "you", "they", "what", "how", "want",
                  "be", "in", "of", "on", "it", "and", "also", "any"}


def _noted(question: str, noted: list[str]) -> bool:
    """Already noted, word for word or in the Caller's own words (most of its words are in a noted one)."""
    words = set(grounding.tokens(question)) - QUESTION_WORDS
    for n in noted:
        if n == question or (words and len(words & set(grounding.tokens(n))) >= 0.6 * len(words)):
            return True
    return False


HANDLERS: dict[str, Callable[[Any, CallerContext, dict], Awaitable[str]]] = {
    "record_fact": record_fact,
    "record_available_from": record_available_from,
    "note_question": note_question,
    "book_viewing": book_viewing,
    "end_call": end_call,
}


def _windows(windows) -> str:
    if not windows:
        return "when the renter can make it"
    if names := slots.bound_days(windows):
        return f"on {names}"
    spoken = [f"{slots.spoken(w.start)} to {slots.spoken(w.end).split(' at ')[1]}" for w in windows]
    return "on " + (spoken[0] if len(spoken) == 1 else ", ".join(spoken[:-1]) + " or " + spoken[-1])
