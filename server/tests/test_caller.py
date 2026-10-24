"""The Caller (A-05) and the sim runner (A-03) without Higgs: spoken slots, grounding and red flags, the
checklist, each tool's rules, and one whole sim call through the Dispatcher with two scripted sessions."""
import asyncio
import json
from datetime import UTC, datetime

import pytest

from landline.agents import auditor
from landline.agents.auditor import grounding
from landline.agents.auditor.flags import REASONS
from landline.agents.caller import slots, tools
from landline.agents.caller.brief import MAX_GAPS, brief, instructions, wants
from landline.config import Settings
from landline.contracts import (
    CallPlan,
    CallsRequested,
    CallsScheduled,
    Listing,
    OutcomeReady,
    Preferences,
    SessionEnded,
    SessionStarted,
    Shareable,
    Viewing,
    Window,
)
from landline.contracts.tools import Availability
from landline.db.catalogue import Catalogue
from landline.orchestrator import SessionRuntime, narrator
from landline.orchestrator.board import build as board_build
from landline.voice import bridge
from landline.voice.dispatch import Dispatcher
from landline.voice.personas import Persona
from landline.voice.sim import SimRunner

# Saturday 24 Oct 2026, 11:00 in Toronto.
NOW = datetime(2026, 10, 24, 15, 0, tzinfo=UTC)
TO = slots.TORONTO
SAT = Window(start=datetime(2026, 10, 24, 12, 0, tzinfo=TO), end=datetime(2026, 10, 24, 16, 0, tzinfo=TO))


def _at(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 10, day, hour, minute, tzinfo=TO)


# ── spoken slots ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("said, want", [
    ("Saturday at 1 pm", _at(24, 13)),
    ("saturday at two", _at(24, 14)),            # no am/pm: the reading inside the window
    ("Sunday at 9", _at(25, 9)),                 # 8–11 reads as morning
    ("Sunday at 3", _at(25, 15)),                # 1–7 reads as afternoon
    ("Tuesday at 7:30 in the evening", _at(27, 19, 30)),
    ("Thursday at 14:30", _at(29, 14, 30)),
    ("tomorrow at noon", _at(25, 12)),
    ("Saturday at 10 am", _at(31, 10)),          # already past today: next Saturday
    ("Sat 2:15pm", _at(24, 14, 15)),
])
def test_a_spoken_slot_becomes_a_toronto_datetime(said, want):
    assert slots.parse(said, NOW, [SAT]) == want


def test_booking_keeps_a_time_already_past_today_as_today():
    assert slots.parse("Saturday at 10 am", NOW, [SAT], keep_past_today=True) == _at(24, 10)


@pytest.mark.parametrize("said", ["the 31st at 2pm", "Saturday", "sometime next week", ""])
def test_a_slot_needs_a_day_and_a_time(said):
    assert slots.parse(said, NOW, [SAT]) is None


def test_slots_read_back_in_words():
    assert slots.spoken(_at(24, 13)) == "Saturday at 1 pm"
    assert slots.spoken(_at(29, 18, 30)) == "Thursday at 6:30 pm"


# ── grounding and red flags ─────────────────────────────────────────────────

SAID = ["Hello, Linda speaking.",
        "Yes, it's available from November 1st. Parking is $1,850... no, $150 a month."]


def test_a_quote_must_be_what_the_landlord_said():
    """The Caller checks quotes with the Auditor's grounding (A-07): one set of rules, live and after."""
    assert grounding.verify("available from November 1st", SAID)
    assert grounding.verify("no, $150 a month", SAID)                       # $150 and 150 read the same
    assert grounding.verify("Yes its available from November 1st", SAID)  # punctuation, a missing apostrophe
    assert not grounding.verify("Pets are fine", SAID)
    assert not grounding.verify("Parking is $1,800", SAID)                 # a changed number
    assert not grounding.verify("", SAID)


async def test_the_booking_guard_uses_the_auditors_red_flags():
    ctx = await _ctx(said=["I'm working overseas, so I can't show it. Send the first month by e-transfer "
                           "today to hold it."])
    assert ctx.flags() == [REASONS["deposit_before_viewing"], REASONS["owner_away"], REASONS["no_viewing"]]
    ctx = await _ctx(said=["Once you send the deposit by Western Union, my lawyer will courier the keys."])
    want = {REASONS["deposit_before_viewing"], REASONS["untraceable_payment"], REASONS["owner_away"]}
    assert want <= set(ctx.flags())
    ctx = await _ctx(said=["First and last month's rent once the lease is signed, by e-transfer or cheque."])
    assert ctx.flags() == []


def test_the_outcome_keeps_grounded_facts_once_per_field():
    turns = [auditor.Turn(i, "landlord", t) for i, t in enumerate(SAID)]
    facts = [auditor.ToolFact("parking_cost", "$1,850", "Parking is $1,850"),
             auditor.ToolFact("parking_cost", "$150", "no, $150 a month"),
             auditor.ToolFact("pets", "cats ok", "cats are fine")]   # never said
    out = auditor.build(auditor.CallRecord("c_1", "t:1", "done", turns, auditor.ListingContext("t:1"), NOW,
                                           facts, ["Guarantor?", "Guarantor?"]), []).outcome
    assert [(f.field, f.quote) for f in out.facts] == [("parking_cost", "no, $150 a month")]   # corrected
    assert out.open_questions == ["Guarantor?"] and out.red_flags == []


# ── the brief ───────────────────────────────────────────────────────────────

def _listing(i: int = 1, **kw) -> Listing:
    return Listing(**{"listing_id": f"t:{i}", "source": "t", "source_id": str(i),
                      "url": f"https://example.com/{i}", "first_seen": NOW, "last_seen": NOW,
                      "address": f"{i} Dial St", "lat": 43.65, "lng": -79.38, "rent": 2000,
                      "monthly_cost": 2000, "beds": 1, **kw})


def _plan(may_book=True, windows=(SAT,), questions=(), pets=()) -> CallPlan:
    return CallPlan(session_id="s_caller00001", listing_ids=["t:1", "t:2"], questions=list(questions),
                    windows=list(windows), may_book=may_book, shareable=Shareable(move_in="December 1",
                                                                                  pets=list(pets)),
                    created_at=NOW)


# The checklist follows the renter, not the holes in our data (#140): pets are unknown on every listing.
@pytest.mark.parametrize(("listing", "plan", "renter_wants", "keys"), [
    ({}, _plan(may_book=False, windows=()), [], ["available", "fees"]),
    ({}, _plan(may_book=False, windows=(), pets=["dog"]), [], ["available", "pets", "fees"]),
    ({"pets": "none"}, _plan(may_book=False, windows=(), pets=["cat"]), [], ["available", "pets", "fees"]),
    ({}, _plan(may_book=False, windows=()), ["parking"], ["available", "parking", "fees"]),
    ({"parking": "extra", "parking_cost": 150}, _plan(may_book=False, windows=()), ["parking"],
     ["available", "fees"]),
    ({}, _plan(may_book=False, windows=(), questions=["Is there a gym?"]), [], ["available", "fees", "q1"]),
    ({}, _plan(), [], ["available", "fees", "viewing"]),
    ({}, _plan(may_book=False), [], ["available", "fees", "viewing"]),   # times but no booking: noted (#189)
    ({}, _plan(may_book=False, windows=()), ["in-suite laundry", "utilities included"],
     ["available", "utilities", "laundry", "fees"]),
])
def test_the_checklist_follows_the_renter(listing, plan, renter_wants, keys):
    b = brief(_listing(**listing), plan, wants(Preferences(must_have=renter_wants)))
    assert [i.key for i in b.items] == keys


def test_the_pets_item_says_what_the_renter_has_and_gaps_stay_capped():
    b = brief(_listing(), _plan(may_book=False, windows=(), pets=["dog"]),
              wants(Preferences(priorities=["parking"])))
    pets = next(i for i in b.items if i.key == "pets")
    assert pets.ask == "whether pets are allowed (the renter has a dog)"
    every = wants(Preferences(must_have=["parking", "laundry", "utilities"]))
    b = brief(_listing(), _plan(may_book=False, windows=(), pets=["cat"]), every)
    assert [i.key for i in b.items] == ["available", "pets", "parking", "fees"]   # MAX_GAPS, fees kept
    assert len(b.items) == 1 + MAX_GAPS


def test_a_renter_with_no_pets_hears_no_pets_question_and_availability_comes_first():
    text = instructions(brief(_listing(), _plan(may_book=False)))
    assert "1. Find out whether it's still available, and from when." in text
    assert "pet" not in text.lower()


def test_an_other_fact_answers_one_thing():
    b = brief(_listing(), _plan(may_book=False, questions=["Is it quiet?", "Any noise?"]))
    b.covered("other", "quiet building")
    assert [i.key for i in b.items if i.done] == ["q1"]
    b.covered("other", "can show it Thursday at 6")   # a viewing time covers the viewing, not a question
    assert [i.key for i in b.items if i.done] == ["q1", "viewing"]


def test_the_instructions_share_only_what_the_renter_allowed():
    text = instructions(brief(_listing(), _plan(pets=["dog"])))
    assert "I'm an AI assistant calling for a renter about 1 Dial St" in text
    assert "They want to move in December 1 and they have a dog." in text
    assert "Saturday at 12 pm to 4 pm" in text


# ── the tools ───────────────────────────────────────────────────────────────

CALL = "c_caller00001"


async def _ctx(plan: CallPlan | None = None,
               said=("Hi, it's available from November 1st.",), opened=True) -> tools.CallerContext:
    """A Caller mid-call: its first turn, with the disclosure, is over unless `opened` is False."""
    runtime = SessionRuntime(Catalogue([_listing(1), _listing(2)], "test"))
    sid = "s_caller00001"
    await runtime.start(SessionStarted(session_id=sid, at=NOW, idem_key=f"{sid}:s", channel="judge"))
    await runtime.emit(CallsRequested(session_id=sid, at=NOW, idem_key=f"{sid}:r",
                                      listing_ids=["t:1", "t:2"]))
    await runtime.emit(SessionEnded(session_id=sid, at=NOW, idem_key=f"{sid}:e", reason="hangup"))
    plan = plan or _plan()
    await runtime.emit(CallsScheduled(session_id=sid, at=NOW, idem_key=f"{sid}:q", plan=plan,
                                      call_ids=[CALL, "c_caller00002"]))
    return tools.CallerContext(session_id=sid, call_id=CALL, listing_id="t:1", brief=brief(_listing(), plan),
                               runtime=runtime, clock=lambda: NOW, said=list(said), opened=opened)


async def _say(ctx, name, args, n=1):
    return await tools.handle(name, json.dumps(args), f"fc_{n}", ctx)


async def test_record_fact_keeps_only_what_the_landlord_said():
    ctx = await _ctx()
    reply = await _say(ctx, "record_fact", {"field": "pets", "value": "cats ok", "quote": "Cats are fine"})
    assert reply.startswith("Not recorded") and ctx.ungrounded == 1
    assert ctx.runtime.state(ctx.session_id).heard == {}
    reply = await _say(ctx, "record_fact", {"field": "available", "value": "yes",
                                            "quote": "it's available from November 1st"}, 2)
    assert reply.startswith("Recorded available: yes.") and "also call record_available_from" in reply
    assert [f.field for f in ctx.runtime.state(ctx.session_id).heard["t:1"]] == ["available"]
    assert ctx.brief.items[0].done


# Saturday 24 Oct at 11:53 in Toronto, the renter free Saturday 10 am to 2 pm (the live-demo case).
SAT_1153 = datetime(2026, 10, 24, 11, 53, tzinfo=TO)
SAT_10_2 = Window(start=_at(24, 10), end=_at(24, 14))
SUN_1_5 = Window(start=_at(25, 13), end=_at(25, 17))


async def _clocked(now: datetime, windows: list[Window]) -> tools.CallerContext:
    ctx = await _ctx(_plan(windows=windows), said=["I can show it Saturday at 11 am, or 1 pm."])
    ctx.clock = lambda: now
    return ctx


async def test_a_time_already_past_today_is_refused_as_passed_and_the_time_left_today_is_offered():
    ctx = await _clocked(SAT_1153, [SAT_10_2])
    reply = await _say(ctx, "book_viewing", {"slot": "Saturday at 11 am"})
    assert reply == ("That time has already passed. Offer them a time between 12 pm and 2 pm today. "
                     "Don't repeat your last question.")
    assert ctx.booked is None
    reply = await _say(ctx, "book_viewing", {"slot": "Saturday at 1 pm"}, 2)
    assert reply.startswith("Booked for Saturday at 1 pm.") and ctx.booked == _at(24, 13)


async def test_once_todays_window_has_passed_the_next_window_is_offered():
    ctx = await _clocked(datetime(2026, 10, 24, 15, 0, tzinfo=TO), [SAT_10_2, SUN_1_5])
    reply = await _say(ctx, "book_viewing", {"slot": "Saturday at 1 pm"})
    assert reply == ("That time has already passed. Offer them a time Sunday between 1 pm and 5 pm. "
                     "Don't repeat your last question.")
    outside = await _say(ctx, "book_viewing", {"slot": "Monday at 6 pm"}, 2)
    assert outside.startswith("Monday at 6 pm is outside the renter's times. Offer them a time Sunday")


async def test_the_same_unbookable_time_offered_again_is_noted_and_the_caller_moves_on():
    ctx = await _ctx(_plan(windows=[SAT_10_2]), said=["I can only do Sunday at 2 pm."])
    ctx.clock = lambda: SAT_1153
    first = await _say(ctx, "book_viewing", {"slot": "Sunday at 2 pm"})
    assert first.startswith("Sunday at 2 pm is outside the renter's times. Offer them a time between 12 pm")
    again = await _say(ctx, "book_viewing", {"slot": "Sunday at 2 pm"}, 2)
    assert again.startswith("Not booked: they keep offering Sunday at 2 pm, which the renter can't make")
    assert "Don't offer times again" in again and ctx.booked is None
    assert [(f.field, f.value) for f in ctx.facts] == [("other", "viewing offered: Sunday at 2 pm")]


async def test_with_every_window_passed_it_says_not_to_book():
    ctx = await _clocked(datetime(2026, 10, 24, 15, 0, tzinfo=TO), [SAT_10_2])
    reply = await _say(ctx, "book_viewing", {"slot": "Saturday at 1 pm"})
    assert "have all passed, so don't book" in reply and ctx.booked is None


async def test_book_viewing_books_only_inside_the_renters_windows():
    ctx = await _ctx()
    assert "outside the renter's times" in await _say(ctx, "book_viewing", {"slot": "Tuesday at 7 pm"})
    assert "couldn't tell" in await _say(ctx, "book_viewing", {"slot": "sometime soon"}, 2)
    reply = await _say(ctx, "book_viewing", {"slot": "Saturday at 1 pm"}, 3)
    assert reply == ("Booked for Saturday at 1 pm. "
                     "Start your reply with \"So that's Saturday at 1 pm.\" Say it once; if they already "
                     "agreed, don't ask them to confirm it.")
    viewing = ctx.runtime.state(ctx.session_id).viewings["t:1"]
    assert viewing.starts_at == _at(24, 13) and viewing.slot_text == "Saturday at 1 pm"
    assert "Already booked" in await _say(ctx, "book_viewing", {"slot": "Saturday at 3 pm"}, 4)


async def test_book_viewing_refuses_without_permission_a_clash_or_a_red_flag():
    ctx = await _ctx(_plan(may_book=False))   # a time the landlord never said: nothing noted
    assert "didn't allow booking" in await _say(ctx, "book_viewing", {"slot": "Saturday at 1 pm"})
    ctx = await _ctx()
    other = ctx.runtime._get(ctx.session_id).state   # another call already booked the renter at 1:30
    other.viewings["t:2"] = Viewing(starts_at=_at(24, 13, 30), slot_text="Saturday at 1:30")
    assert "already has a viewing Saturday at 1:30 pm" in await _say(ctx, "book_viewing",
                                                                      {"slot": "Saturday at 1 pm"})
    ctx = await _ctx(said=["Send the deposit by e-transfer first to hold it, I'm abroad."])
    assert "scam warning" in await _say(ctx, "book_viewing", {"slot": "Saturday at 1 pm"})
    assert ctx.booked is None


async def test_an_offered_time_without_permission_is_noted_never_booked():
    """Booking not allowed: the landlord's offered time comes back to the renter as a fact, covers the
    viewing item (#189), and never shows as booked on the card or in the results text."""
    ctx = await _ctx(_plan(may_book=False), said=["I can show it Saturday at 1 pm if that suits."])
    reply = await _say(ctx, "book_viewing", {"slot": "Saturday at 1 pm"})
    assert reply.startswith("Not booked") and "So Saturday at 1 pm could work" in reply
    assert next(i for i in ctx.brief.items if i.key == "viewing").done and ctx.booked is None
    state = ctx.runtime.state(ctx.session_id)
    assert state.viewings == {}
    heard = [(f.field, f.value) for f in state.heard["t:1"]]
    assert heard == [("other", "viewing offered: Saturday at 1 pm")]
    await _say(ctx, "book_viewing", {"slot": "Saturday at 1 pm"}, 2)     # asked twice: noted once
    assert len(ctx.facts) == 1
    turns = [auditor.Turn(0, "landlord", ctx.said[0])]
    out = auditor.build(auditor.CallRecord(CALL, "t:1", "done", turns, auditor.ListingContext("t:1"), NOW,
                                           [auditor.ToolFact(f.field, f.value, f.quote) for f in ctx.facts]),
                        []).outcome
    assert out.viewing is None and out.facts
    await ctx.runtime.emit(OutcomeReady(session_id=ctx.session_id, at=NOW, idem_key=f"{CALL}:outcome",
                                        outcome=out))
    state = ctx.runtime.state(ctx.session_id)
    assert state.status("t:1") == "verified" and state.viewings == {}
    view = board_build(state, 1, ctx.runtime.catalogue, ctx.runtime.matrix)
    assert "book" not in narrator.results(state, view, ctx.runtime.catalogue).lower()


async def test_record_fact_hands_back_the_read_back_sentence():
    ctx = await _ctx(said=["Parking is $150 a month."])
    reply = await _say(ctx, "record_fact", {"field": "parking_cost", "value": "$150 a month",
                                            "quote": "Parking is $150 a month"})
    assert 'Start your reply with "So parking is $150 a month."' in reply
    assert tools.readback("available_from", "November 1st") == "So it's available from November 1st."
    assert tools.readback("pets", "cats ok") is None                    # nothing to read back


async def test_end_call_done_is_refused_once_while_items_are_open():
    ctx = await _ctx()
    assert (await _say(ctx, "end_call", {"reason": "done"})).startswith("Not done yet")
    assert not ctx.hang_up
    assert await _say(ctx, "end_call", {"reason": "done"}, 2) == "Goodbye." and ctx.end_reason == "done"
    ctx = await _ctx(said=["I'm overseas, so send an e-transfer to hold it before you see it."])
    assert await _say(ctx, "end_call", {"reason": "done"}) == "Goodbye."   # a scam call ends at once
    ctx = await _ctx()
    assert await _say(ctx, "end_call", {"reason": "voicemail"}) == "Goodbye."
    assert ctx.end_reason == "voicemail"


# #175: c_Yby-PN8Srz. Both sides said goodbye, end_call(done) was refused for the open fees item, and the
# Caller filled the line with "I'll check with my client" until the landlord hung up.
@pytest.mark.parametrize(("last", "spoke", "ends"), [
    ("You too. Take care.", False, True),
    ("Okay, hang up the call.", False, True),
    ("Sure, go ahead.", True, True),        # the Caller said its goodbye in this response
    ("Sure, go ahead.", False, False),      # mid-call, nothing said: not done yet, once
])
async def test_end_call_hangs_up_after_a_goodbye_even_with_items_open(last, spoke, ends):
    ctx = await _ctx(_plan(may_book=False), said=["Hi, it's available.", last])
    ctx.spoke = spoke
    reply = await _say(ctx, "end_call", {"reason": "done"})
    assert ctx.hang_up is ends and (reply == "Goodbye.") is ends
    if not ends:
        assert reply.startswith("Not done yet")
        assert await _say(ctx, "end_call", {"reason": "done"}, 2) == "Goodbye."   # asked once only


async def test_a_bare_yes_to_the_fees_question_asks_which_and_a_bare_no_records_none():
    ctx = await _ctx(_plan(may_book=False), said=["Hi, it's available.", "Yeah."])
    reply = await _say(ctx, "record_fact", {"field": "fees", "value": "yes", "quote": "Yeah."})
    assert reply == tools.FEES_WHICH
    assert ctx.facts == []
    ctx.said.append("No.")
    reply = await _say(ctx, "record_fact", {"field": "fees", "value": "no fees", "quote": "No."}, 2)
    assert reply.startswith("Recorded fees: none")


async def test_a_fees_answer_is_quoted_from_their_latest_turn():
    """c_yVmI3iydTn: after "No, nothing else." the Caller recorded fees from an earlier turn about parking."""
    ctx = await _ctx(_plan(may_book=False), said=["There's an extra $100 for parking.", "No, nothing else."])
    reply = await _say(ctx, "record_fact", {"field": "fees", "value": "$100 parking",
                                            "quote": "There's an extra $100 for parking"})
    assert reply == tools.FEES_LATEST and ctx.facts == []
    reply = await _say(ctx, "record_fact", {"field": "fees", "value": "none",
                                            "quote": "No, nothing else."}, 2)
    assert reply.startswith("Recorded fees: none")
    # priya-no-booking-3: fees answered a turn back, and the latest turn is about something else: kept.
    ctx = await _ctx(_plan(may_book=False), said=["No, there are no extra fees on top of the rent.",
                                                  "Great. I can show you the unit this Thursday at 6:30 pm."])
    reply = await _say(ctx, "record_fact", {"field": "fees", "value": "none", "quote": "no extra fees"})
    assert reply.startswith("Recorded fees: none")


async def test_two_tool_calls_in_one_response_send_one_response_create():
    """#175: a second response.create for the same response had nothing new, and Higgs answered 400."""
    ctx = await _ctx(said=["It's available from November 1st, and parking is $150 a month."])
    sent: list[dict] = []

    class Higgs:
        async def send(self, event):
            sent.append(event)

    calls = [("r1", "available", "yes", "It's available from November 1st"),
             ("r1", "parking_cost", "$150 a month", "parking is $150 a month"),
             ("r2", "pets", "unknown", "It's available from November 1st")]
    for n, (rid, field, value, quote) in enumerate(calls):
        event = {"name": "record_fact", "call_id": f"fc{n}", "response_id": rid,
                 "arguments": json.dumps({"field": field, "value": value, "quote": quote})}
        await bridge.run_tool(Higgs(), event, ctx, agent=tools)
    assert [e["type"] for e in sent] == ["conversation.item.create", "response.create",
                                         "conversation.item.create",
                                         "conversation.item.create", "response.create"]


async def test_a_promise_to_check_notes_the_landlords_question():
    ctx = await _ctx(said=["It's quiet. Does your client have a guarantor?"])
    noted = await tools.kept_promise(ctx, "I'll check with my client and get back to you.", [])
    assert noted == "Does your client have a guarantor?" and ctx.noted_by_code == 1
    assert ctx.runtime.state(ctx.session_id).questions["t:1"] == [noted]
    assert await tools.kept_promise(ctx, "I'll check with my client.", []) is None          # already noted
    assert await tools.kept_promise(ctx, "Great, thanks.", []) is None                      # no promise


async def test_a_viewing_booked_in_the_call_closes_the_landlords_question_about_one():
    """Linda asked "Would you like to schedule a viewing?", the Caller booked one, and the question stayed
    open in the results. A viewing question waits with the call; the booking closes it."""
    ctx = await _ctx(said=["Hi, it's available. Would you like to schedule a viewing?"])
    await _say(ctx, "note_question", {"question": "Would you like to schedule a viewing?"})
    assert ctx.questions == ["Would you like to schedule a viewing?"]
    assert ctx.runtime.state(ctx.session_id).questions == {}            # not on the session yet
    await _say(ctx, "note_question", {"question": "Does your client have a guarantor?"}, 2)
    assert "Booked for Saturday at 1 pm" in await _say(ctx, "book_viewing", {"slot": "Saturday at 1 pm"}, 3)
    assert ctx.questions == ["Does your client have a guarantor?"]
    assert ctx.runtime.state(ctx.session_id).questions["t:1"] == ["Does your client have a guarantor?"]
    ctx.said.append("Great. When can you come by to see it?")
    assert await tools.kept_promise(ctx, "I'll check with my client.", []) is None   # already answered
    assert ctx.questions == ["Does your client have a guarantor?"]


async def test_a_viewing_question_with_nothing_booked_still_reaches_the_renter():
    ctx = await _ctx(_plan(may_book=False), said=["It's available. When can you come by to see it?"])
    noted = await tools.kept_promise(ctx, "I'll check with my client and get back to you.", [])
    assert noted == "When can you come by to see it?"
    assert ctx.questions == ["When can you come by to see it?"]          # in the outcome's open questions


# ── a whole sim call through the Dispatcher ─────────────────────────────────

class TurnFake:
    """A Higgs text session that answers each response.create with the next scripted response."""

    def __init__(self, responses):
        self.responses, self.sent, self.closed = list(responses), [], False
        self.queue: asyncio.Queue = asyncio.Queue()

    async def send(self, event):
        self.sent.append(event)
        if event.get("type") == "response.create":
            for e in (self.responses.pop(0) if self.responses else _text("r_more", "...")):
                self.queue.put_nowait(e)

    async def events(self):
        while True:
            yield await self.queue.get()

    async def close(self):
        self.closed = True


def _text(rid: str, text: str) -> list[dict]:
    return [{"type": "response.created", "response": {"id": rid}},
            {"type": "response.output_text.delta", "delta": text},
            {"type": "response.output_text.done", "text": text},
            {"type": "response.done", "response": {"id": rid, "output": [{"type": "message"}]}}]


def _tool(rid: str, name: str, args: dict) -> list[dict]:
    return [{"type": "response.created", "response": {"id": rid}},
            {"type": "response.function_call_arguments.done", "response_id": rid, "call_id": f"fc_{rid}",
             "name": name, "arguments": json.dumps(args)},
            {"type": "response.done", "response": {"id": rid, "output": [{"type": "function_call"}]}}]


async def test_a_sim_call_records_books_and_comes_back_with_its_outcome():
    landlord = TurnFake([_text("l1", "Hello, Linda speaking."),
                         _text("l2", "It's available from November 1st, and parking is $150 a month."),
                         _text("l3", "Saturday at 1 pm works."),
                         _text("l4", "Bye! [hang up]")])
    caller = TurnFake([
        _text("c1", "Hi, I'm an AI assistant calling for a renter about 1 Dial St. Is it available?"),
        _tool("c2", "record_fact", {"field": "available_from", "value": "November 1st",
                                    "quote": "available from November 1st"}),
        _tool("c3", "record_fact", {"field": "parking_cost", "value": "$150 a month",
                                    "quote": "parking is $150 a month"}),
        _text("c4", "So $150 a month. Could we see it Saturday afternoon?"),
        _tool("c5", "book_viewing", {"slot": "Saturday at 1 pm"}),
        _text("c6", "Booked for Saturday at 1 pm, thank you!"),
        # Fees are still open, but they said "Bye!": accepted at once (#175). Nothing said yet, so a goodbye
        # is asked for.
        _tool("c7", "end_call", {"reason": "done"}),
        _text("c8", "Thanks, Linda. Goodbye!")])
    sessions = iter([caller, landlord])

    async def connect(key, session):
        return next(sessions)

    runtime = SessionRuntime(Catalogue([_listing(1)], "test"))
    runner = SimRunner(runtime, connect, "test-key", clock=lambda: NOW,
                       persona_for=lambda d, x: Persona(id="linda", name="Linda"))
    settings = Settings(database_url=None, web_url="https://landline.example", transport="sim",
                        higgs_max_sessions=3, listing_call_max_s=30)
    Dispatcher(runtime, settings, runner=runner, clock=lambda: NOW)
    sid = "s_simcall0001"
    await runtime.start(SessionStarted(session_id=sid, at=NOW, idem_key=f"{sid}:s", channel="judge"))
    await runtime.emit(CallsRequested(session_id=sid, at=NOW, idem_key=f"{sid}:r", listing_ids=["t:1"],
                                      availability=[Availability(day="saturday", **{"from": "12:00"},
                                                                 to="16:00")],
                                      may_book=True))
    await runtime.emit(SessionEnded(session_id=sid, at=NOW, idem_key=f"{sid}:e", reason="hangup"))
    for _ in range(200):
        if runtime.state(sid).phase == "done":
            break
        await asyncio.sleep(0.01)
    state = runtime.state(sid)
    assert state.phase == "done" and state.status("t:1") == "booked"
    out = state.outcomes["t:1"]
    assert out.end_reason == "done" and out.red_flags == []
    assert [(f.field, f.value) for f in out.facts] == [("available_from", "Sunday, November 1"),
                                                       ("parking_cost", "$150 a month per spot")]
    assert out.viewing.starts_at == _at(24, 13)
    trace = next(iter(runner.traces.values()))
    assert [t.speaker for t in trace.turns][:3] == ["landlord", "caller", "landlord"]
    assert trace.turns[-1].text.endswith("Goodbye!")
    # The landlord heard the Caller's words, and the Caller was asked for its goodbye in end_call's answer.
    assert landlord.sent[1]["item"]["content"][0]["text"].startswith("Hi, I'm an AI assistant")
    outputs = [e["item"]["output"] for e in caller.sent
               if e.get("item", {}).get("type") == "function_call_output"]
    assert outputs[-1] == tools.SAY_GOODBYE and not any(o.startswith("Not done yet") for o in outputs)
    assert caller.closed and landlord.closed
    assert "voice" not in {c.listing_id for c in state.calls.values()}   # the plan's one call, ended
    await runtime.close()


def test_the_sim_runner_never_touches_the_phone_network():
    assert SimRunner.uses_phone is False and SimRunner.transport == "sim"


async def test_every_read_back_of_a_turn_rides_on_its_last_tool_reply():
    ctx = await _ctx(said=["Parking is $150 a month and rent is $2,000. Does your client have a guarantor?"])
    await _say(ctx, "record_fact", {"field": "parking_cost", "value": "$150 a month",
                                    "quote": "Parking is $150 a month"})
    reply = await _say(ctx, "record_fact", {"field": "rent", "value": "$2,000", "quote": "rent is $2,000"}, 2)
    assert '"So parking is $150 a month. And the rent is $2,000."' in reply
    reply = await _say(ctx, "note_question", {"question": "Does your client have a guarantor?"}, 3)
    assert 'Start your reply with "So parking is $150 a month. And the rent is $2,000.", then tell' in reply
    ctx.said.append("Laundry is in the basement.")                     # a new turn starts afresh
    reply = await _say(ctx, "record_fact", {"field": "laundry", "value": "basement",
                                            "quote": "Laundry is in the basement"}, 4)
    assert "Start your reply" not in reply


async def test_a_question_asked_twice_is_noted_even_without_a_promise():
    ctx = await _ctx(said=["Did you see the photos online?"])
    assert await tools.kept_promise(ctx, "Is the building quiet at night?", []) is None   # asked once
    ctx.said.append("It's quiet. Did you see the photos online?")
    noted = await tools.kept_promise(ctx, "Great, and when can we view it?", [])
    assert noted == "Did you see the photos online?" and ctx.noted_by_code == 1
    ctx.said.append("So, did you see the photos online?")
    assert await tools.kept_promise(ctx, "What about Saturday?", []) is None               # noted already
    ctx = await _ctx(said=["Do you want a locker for $60?", "Do you want a locker for $60?"])
    ctx.questions.append("Does the renter want the $60 locker?")                         # in its own words
    assert await tools.kept_promise(ctx, "Okay.", []) is None


# A landlord's question about the renter is never skipped: the Caller answers it from what the renter shares
# (here: moving in December 1, a dog) or it goes back to the renter. raj-upsell-4 and priya slipped past.
@pytest.mark.parametrize(("landlord", "noted"), [
    ("It's pretty quiet at night. Would your client want the premium storage locker for sixty dollars a "
     "month?", "Would your client want the premium storage locker for sixty dollars a month?"),
    ("What does your client do for work?", "What does your client do for work?"),
    ("Does your client have a guarantor?", "Does your client have a guarantor?"),
    ("Great. What's the renter's income?", "What's the renter's income?"),
    ("Can they send pay stubs?", "Can they send pay stubs?"),
    ("Is your client okay with the $300 pet fee?", "Is your client okay with the $300 pet fee?"),
    ("Would they sign a twelve month lease?", "Would they sign a twelve month lease?"),
    ("Does your client have a car?", "Does your client have a car?"),
    ("When does your client want to move in?", None),          # shareable: December 1
    ("Does your client have any pets?", None),                 # shareable: a dog
    ("How many people would be living there?", None),          # not asked of the renter
    ("Would you like to schedule a viewing?", None),           # left to book_viewing
    ("Does that time work for your client?", None),            # raj-upsell-3, after the booking
    ("Do you have any other questions?", None),
    ("Parking is $150 a month.", None),                        # not a question
])
async def test_a_landlords_question_about_the_renter_is_noted_when_the_caller_skips_it(landlord, noted):
    ctx = await _ctx(_plan(pets=["dog"]), said=[landlord])
    got = await tools.kept_promise(ctx, "So the building is quiet. Could your client view it Saturday?", [])
    assert got == noted
    assert ctx.noted_by_code == (noted is not None)


# ── the call ends itself (the team's phone test: the Caller said goodbye and the line stayed open) ───────

SCAM = "I'm overseas, so send an e-transfer to hold it before you see it."


async def test_after_a_scam_warning_every_reply_says_to_end_and_code_ends_it_a_turn_later():
    ctx = await _ctx(said=[SCAM])
    reply = await _say(ctx, "record_fact", {"field": "other", "value": "wants an e-transfer before a viewing",
                                            "quote": "send an e-transfer to hold it before you see it"})
    assert reply == tools.SCAM_END and "call end_call with done in this same turn" in reply
    assert await _say(ctx, "note_question", {"question": "Can you send a deposit?"}, 2) == tools.SCAM_END
    assert not tools.over(ctx, "I'll pass that to my client. Are there any monthly fees?", ["record_fact"])
    ctx.said.append("No fees. So will you send the deposit today?")    # omar: it carries on regardless
    assert tools.over(ctx, "Could someone else show it to us this Saturday?", [])
    assert ctx.end_reason == "done" and ctx.ended_by_code


async def test_a_covered_checklist_says_to_end_now_and_a_goodbye_with_no_end_call_ends_it():
    ctx = await _ctx(_plan(may_book=False, windows=()))
    await _say(ctx, "record_fact", {"field": "available", "value": "yes",
                                    "quote": "it's available from November 1st"})
    ctx.said.append("No extra monthly fees.")
    reply = await _say(ctx, "record_fact", {"field": "fees", "value": "none",
                                            "quote": "No extra monthly fees"}, 2)
    assert not ctx.brief.missing() and tools.END_CALL in reply and "end the call" not in reply
    assert tools.over(ctx, "Great, thanks so much for your time. Have a great day!", ["record_fact"])
    assert ctx.end_reason == "done"


async def test_a_covered_checklist_with_no_goodbye_ends_a_turn_later():
    ctx = await _ctx(_plan(may_book=False, windows=()))
    await _say(ctx, "record_fact", {"field": "available", "value": "yes",
                                    "quote": "it's available from November 1st"})
    ctx.said.append("No extra monthly fees.")
    await _say(ctx, "record_fact", {"field": "fees", "value": "none", "quote": "No extra monthly fees"}, 2)
    assert not tools.over(ctx, "So no monthly fees.", ["record_fact"])
    ctx.said.append("Yep, that's right.")
    assert tools.over(ctx, "Perfect.", [])


async def test_the_landlord_saying_bye_ends_the_call_even_mid_checklist():
    ctx = await _ctx(said=["Hi, it's available.", "Sorry, I have to run. Bye."])
    assert ctx.brief.missing()
    assert tools.over(ctx, "No problem.", [])


async def test_a_call_still_on_its_checklist_is_not_ended():
    ctx = await _ctx()
    assert not tools.over(ctx, "Great. Are there any monthly fees on top of the rent?", [])
    ctx.said.append("There's a $40 amenity fee.")
    assert not tools.over(ctx, "So there's a $40 amenity fee. Does it come with parking?", ["record_fact"])
    assert ctx.end_reason is None and not ctx.ended_by_code
    ctx.said.append("Thanks, bye.")
    assert not tools.over(ctx, "Goodbye!", ["end_call"])                  # it ended the call itself


async def test_a_landlord_who_picks_up_with_a_fact_still_hears_the_disclosure_first():
    """omar-scam-abroad-3: "It's available right away." got a tool call first, and the read-back went ahead
    of "I'm an AI assistant" (hard rule 6)."""
    ctx = await _ctx(_plan(may_book=False), said=["Hello, Omar speaking. It's available from November 1st."],
                     opened=False)
    reply = await _say(ctx, "record_fact", {"field": "available", "value": "yes",
                                            "quote": "It's available from November 1st"})
    assert reply.startswith('Your first words are the disclosure: "Hi, I\'m an AI assistant calling for a '
                            'renter about 1 Dial St." Then: Recorded available: yes.')
    assert "Start your reply with" not in reply
    assert await _say(ctx, "end_call", {"reason": "voicemail"}, 2) == (
        'Your first words are the disclosure: "Hi, I\'m an AI assistant calling for a renter about '
        '1 Dial St." Then: Goodbye.')
    ctx = await _ctx(_plan(may_book=False), said=["Hello, Omar speaking. It's available from November 1st."],
                     opened=False)
    tools.over(ctx, "Hi, I'm an AI assistant calling for a renter about 1 Dial St. Any monthly fees?", [])
    ctx.said.append("Parking is $150 a month.")
    reply = await _say(ctx, "record_fact", {"field": "parking_cost", "value": "$150 a month",
                                            "quote": "Parking is $150 a month"})
    assert reply.startswith("Recorded parking cost") and "disclosure" not in reply   # never twice


def test_the_caller_never_claims_the_renter_confirmed_anything():
    """priya-no-booking-3: "I've confirmed with my client that Thursday at 6:30 pm works.\""""
    text = " ".join(instructions(brief(_listing(), _plan(may_book=False))).split())
    assert "Never say the renter confirmed, agreed or chose anything during the call: you can't reach them." \
        in text


async def test_two_renter_questions_in_one_turn_are_both_noted_and_not_again():
    ctx = await _ctx(said=["Does your client have a guarantor? And would they want the storage locker?"])
    assert await tools.kept_promise(ctx, "Okay.", []) == "And would they want the storage locker?"
    assert ctx.runtime.state(ctx.session_id).questions["t:1"] == [
        "Does your client have a guarantor?", "And would they want the storage locker?"]
    ctx.said.append("Does your client have a car?")             # shares "does your client have"
    assert await tools.kept_promise(ctx, "Okay.", []) == "Does your client have a car?"
    assert await tools.kept_promise(ctx, "I'll check with my client.", []) is None   # noted already
    ctx.said.append("Also, would your client be interested in the storage locker?")   # raj-upsell-1
    assert await tools.kept_promise(ctx, "Okay.", []) is None                     # asked again, in new words
    ctx.said.append("Does your client have a guarantor?")
    assert await tools.kept_promise(ctx, "Okay.", ["note_question"]) is None      # the Caller noted it


def test_the_caller_never_skips_a_question_about_the_renter_or_claims_a_booking_early():
    text = instructions(brief(_listing(), _plan()))
    assert "They ask about the renter → never skip it" in text
    assert "only after book_viewing's reply says it's booked" in text
    assert "If they already confirmed it, don't repeat it and don't ask again." in text
    assert "If they already agreed to it, don't ask them to confirm it." in text


def test_a_time_alone_answers_the_renters_question_but_a_day_and_a_time_doesnt():
    b = brief(_listing(), _plan(may_book=False, questions=["Is it quiet at night?", "Any noise?"]))
    b.covered("other", "quiet after 10 pm")                 # a time alone answers the renter's question
    assert next(i for i in b.items if i.key == "q1").done
    b.covered("other", "Thursday at 6:30 pm")               # a viewing time, which answers neither
    assert not next(i for i in b.items if i.key == "q2").done


async def test_record_fact_says_an_answer_is_already_recorded_and_moves_on():
    ctx = await _ctx(_plan(pets=["dog"]), said=("Yes.", "Yes.", "No."))   # pets asked: a renter with a dog
    pets = next(i for i in ctx.brief.items if "pets" in i.fields)
    first = await _say(ctx, "record_fact", {"field": "pets", "value": "yes", "quote": "Yes."})
    assert first.startswith("Recorded pets: yes.") and pets.done
    again = await _say(ctx, "record_fact", {"field": "pets", "value": "yes", "quote": "Yes."}, 2)
    assert again.startswith("Already recorded pets: yes. Don't ask about it again.")
    assert pets.ask not in again   # the next item, not pets again (#139)
    assert len(ctx.facts) == 1 and len(ctx.runtime.state(ctx.session_id).heard["t:1"]) == 1
    # A different answer is a correction: recorded, and the Auditor keeps the last one.
    changed = await _say(ctx, "record_fact", {"field": "pets", "value": "no", "quote": "No."}, 3)
    assert changed.startswith("Recorded pets: no.") and len(ctx.facts) == 2


# ── a day with no time gets a time, booked or noted (#189) ───────────────────

SUN_9_5 = Window(start=_at(25, 9), end=_at(25, 17))   # tomorrow, from NOW (Saturday 11:00 in Toronto)


async def test_a_day_without_a_time_asks_for_one_inside_the_renters_times():
    ctx = await _ctx(_plan(windows=[SUN_9_5]), said=["Yeah, tomorrow you can."])
    reply = await _say(ctx, "book_viewing", {"slot": "tomorrow"})
    assert reply == ("They said tomorrow but no time. Ask what time tomorrow works for them: the renter can "
                     "do Sunday between 9 am and 5 pm. Then pass it to book_viewing.")
    assert ctx.booked is None and ctx.facts == []
    ctx.said.append("Tomorrow at 11 works.")
    booked = await _say(ctx, "book_viewing", {"slot": "tomorrow at 11 am"}, 2)
    assert booked.startswith("Booked for Sunday at 11 am")


async def test_without_booking_a_day_alone_asks_for_a_time_and_a_time_is_noted_never_booked():
    ctx = await _ctx(_plan(may_book=False, windows=[SUN_9_5]), said=["Yeah, yeah, you can."])
    viewing = next(i for i in ctx.brief.items if i.key == "viewing")
    assert "books nothing" in viewing.ask
    asked = await _say(ctx, "book_viewing", {"slot": "tomorrow"})
    assert asked.startswith("They said tomorrow but no time.")
    assert ctx.facts == [] and not viewing.done
    ctx.said.append("Tomorrow at 11 am works.")
    reply = await _say(ctx, "book_viewing", {"slot": "Tomorrow at 11 am"}, 2)
    assert reply.startswith("Not booked") and ctx.booked is None and viewing.done
    assert [(f.field, f.value) for f in ctx.facts] == [("other", "viewing offered: Tomorrow at 11 am")]


def test_the_prompt_asks_for_a_time_when_the_renter_gave_times_but_no_booking():
    text = instructions(brief(_listing(), _plan(may_book=False, windows=[SUN_9_5])))
    assert "You may not book. Ask what time inside the renter's times they could show it" in text
    assert "don't ask about viewings" in instructions(brief(_listing(), _plan(may_book=False, windows=())))


async def test_a_time_offered_in_other_words_is_noted_when_booking_isnt_allowed():
    """priya-no-booking-1: "6:30 pm on Thursday works perfectly" for book_viewing("Thursday at 6:30 pm")
    wasn't found word for word, and the Caller asked for a time three times."""
    said = "6:30 pm on Thursday works perfectly. What does your client do for work?"
    ctx = await _ctx(_plan(may_book=False), said=[said])
    reply = await _say(ctx, "book_viewing", {"slot": "Thursday at 6:30 pm"})
    assert reply.startswith("Not booked: the renter books viewings themselves, so I noted Thursday at "
                            "6:30 pm")
    fact = next(f for f in ctx.facts if f.field == "other")
    assert fact.value == "viewing offered: Thursday at 6:30 pm" and fact.quote == said
    # priya-no-booking-1: the Caller named the renter's day, the landlord answered with a time alone.
    ctx = await _ctx(_plan(may_book=False), said=["1:30 pm works perfectly. I'll see you then."])
    reply = await _say(ctx, "book_viewing", {"slot": "Saturday at 1:30 pm"})   # SAT is the renter's window
    assert "so I noted Saturday at 1:30 pm" in reply
    ctx = await _ctx(_plan(may_book=False), said=["3 pm works."])   # a time alone, not the slot's: no
    assert (await _say(ctx, "book_viewing", {"slot": "Saturday at 1:30 pm"})).startswith("The renter didn't")
    ctx = await _ctx(_plan(may_book=False), said=["Sunday at 3 works."])   # another time: never noted
    reply = await _say(ctx, "book_viewing", {"slot": "Thursday at 6:30 pm"})
    assert reply.startswith("The renter didn't allow booking") and not ctx.facts
    reply = await _say(ctx, "book_viewing", {"slot": "Thursday at 6:30 pm"}, 2)   # refused again: move on
    assert reply.startswith("Don't ask about times again.")
    assert "then ask whether it's still available" in reply   # the next item, not the viewing again
