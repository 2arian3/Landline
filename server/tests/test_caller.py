"""The Caller (A-05) and the sim runner (A-03) without Higgs: spoken slots, grounding and red flags, the
checklist, each tool's rules, and one whole sim call through the Dispatcher with two scripted sessions."""
import asyncio
import json
from datetime import UTC, datetime

import pytest

from landline.agents.caller import outcome, slots, tools
from landline.agents.caller.brief import MAX_GAPS, brief, instructions
from landline.config import Settings
from landline.contracts import (
    CallPlan,
    CallsRequested,
    CallsScheduled,
    Listing,
    SessionEnded,
    SessionStarted,
    Shareable,
    Viewing,
    Window,
)
from landline.contracts.tools import Availability
from landline.db.catalogue import Catalogue
from landline.orchestrator import SessionRuntime
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
    assert outcome.quoted("available from November 1st", SAID)
    assert outcome.quoted("parking is 1850", SAID)                        # $1,850 and 1850 read the same
    assert outcome.quoted("Yes, its available from November 1st", SAID)  # punctuation, a missing apostrophe
    assert not outcome.quoted("Pets are fine", SAID)
    assert not outcome.quoted("", SAID)


def test_red_flags_come_from_the_landlords_words():
    scam = ["I'm working overseas, so I can't show it. Send the first month by e-transfer today to hold it."]
    assert outcome.red_flags(scam) == [outcome.RED_FLAGS["deposit_before_viewing"],
                                       outcome.RED_FLAGS["owner_away"]]
    courier = ["Once you send the deposit by Western Union, my lawyer will courier you the keys."]
    assert outcome.RED_FLAGS["deposit_before_viewing"] in outcome.red_flags(courier)
    assert outcome.RED_FLAGS["owner_away"] in outcome.red_flags(courier)
    legal = ["First and last month's rent once the lease is signed, by e-transfer or cheque."]
    assert outcome.red_flags(legal) == []


def test_the_outcome_keeps_grounded_facts_once_per_field():
    from landline.contracts import Fact
    facts = [Fact(field="parking_cost", value="$1,850", quote="Parking is $1,850", speaker="Agent heard"),
             Fact(field="parking_cost", value="$150", quote="no, $150 a month", speaker="Agent heard"),
             Fact(field="pets", value="cats ok", quote="cats are fine", speaker="Agent heard")]   # never said
    out = outcome.outcome("c_1", "t:1", "done", facts, SAID, ["Guarantor?", "Guarantor?"], None)
    assert [(f.field, f.value) for f in out.facts] == [("parking_cost", "$150")]   # the corrected read-back
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


def test_the_checklist_asks_what_the_listing_doesnt_say():
    b = brief(_listing(), _plan(questions=["Is it quiet?"], pets=["cat"]))
    keys = [i.key for i in b.items]
    assert keys[0] == "available" and keys[-2:] == ["q1", "viewing"]
    assert len(keys) == 1 + MAX_GAPS + 2 and "pets" in keys and "parking" in keys
    assert "a cat" in b.items[1].ask
    known = brief(_listing(pets="cats_only", parking="included", laundry="in_suite",
                           utilities_included=["heat"]), _plan())
    assert [i.key for i in known.items] == ["available", "fees", "viewing"]


def test_an_other_fact_answers_one_thing():
    b = brief(_listing(), _plan(may_book=False, questions=["Is it quiet?", "Any noise?"]))
    b.covered("other", "quiet building")
    assert [i.key for i in b.items if i.done] == ["q1"]
    b.covered("other", "can show it Thursday at 6")
    assert [i.key for i in b.items if i.done] == ["q1", "viewing"]


def test_the_instructions_share_only_what_the_renter_allowed():
    text = instructions(brief(_listing(), _plan(pets=["dog"])))
    assert "I'm an AI assistant calling for a renter about 1 Dial St" in text
    assert "They want to move in December 1 and they have a dog." in text
    assert "Saturday at 12 pm to 4 pm" in text


# ── the tools ───────────────────────────────────────────────────────────────

CALL = "c_caller00001"


async def _ctx(plan: CallPlan | None = None,
               said=("Hi, it's available from November 1st.",)) -> tools.CallerContext:
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
                               runtime=runtime, clock=lambda: NOW, said=list(said))


async def _say(ctx, name, args, n=1):
    return await tools.handle(name, json.dumps(args), f"fc_{n}", ctx)


async def test_record_fact_keeps_only_what_the_landlord_said():
    ctx = await _ctx()
    reply = await _say(ctx, "record_fact", {"field": "pets", "value": "cats ok", "quote": "Cats are fine"})
    assert reply.startswith("Not recorded") and ctx.ungrounded == 1
    assert ctx.runtime.state(ctx.session_id).heard == {}
    reply = await _say(ctx, "record_fact", {"field": "available", "value": "yes",
                                            "quote": "it's available from November 1st"}, 2)
    assert reply.startswith("Recorded available: yes.") and "also record available_from" in reply
    assert [f.field for f in ctx.runtime.state(ctx.session_id).heard["t:1"]] == ["available"]
    assert ctx.brief.items[0].done


async def test_book_viewing_books_only_inside_the_renters_windows():
    ctx = await _ctx()
    assert "outside the renter's times" in await _say(ctx, "book_viewing", {"slot": "Tuesday at 7 pm"})
    assert "couldn't tell" in await _say(ctx, "book_viewing", {"slot": "sometime soon"}, 2)
    reply = await _say(ctx, "book_viewing", {"slot": "Saturday at 1 pm"}, 3)
    assert reply == "Booked for Saturday at 1 pm. Read it back to them to confirm."
    viewing = ctx.runtime.state(ctx.session_id).viewings["t:1"]
    assert viewing.starts_at == _at(24, 13) and viewing.slot_text == "Saturday at 1 pm"
    assert "Already booked" in await _say(ctx, "book_viewing", {"slot": "Saturday at 3 pm"}, 4)


async def test_book_viewing_refuses_without_permission_a_clash_or_a_red_flag():
    ctx = await _ctx(_plan(may_book=False))
    assert "didn't allow booking" in await _say(ctx, "book_viewing", {"slot": "Saturday at 1 pm"})
    ctx = await _ctx()
    other = ctx.runtime._get(ctx.session_id).state   # another call already booked the renter at 1:30
    other.viewings["t:2"] = Viewing(starts_at=_at(24, 13, 30), slot_text="Saturday at 1:30")
    assert "already has a viewing Saturday at 1:30 pm" in await _say(ctx, "book_viewing",
                                                                      {"slot": "Saturday at 1 pm"})
    ctx = await _ctx(said=["Send the deposit by e-transfer first to hold it, I'm abroad."])
    assert "scam warning" in await _say(ctx, "book_viewing", {"slot": "Saturday at 1 pm"})
    assert ctx.booked is None


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


async def test_a_promise_to_check_notes_the_landlords_question():
    ctx = await _ctx(said=["It's quiet. Does your client have a guarantor?"])
    noted = await tools.kept_promise(ctx, "I'll check with my client and get back to you.", [])
    assert noted == "Does your client have a guarantor?" and ctx.noted_by_code == 1
    assert ctx.runtime.state(ctx.session_id).questions["t:1"] == [noted]
    assert await tools.kept_promise(ctx, "I'll check with my client.", []) is None          # already noted
    assert await tools.kept_promise(ctx, "Great, thanks.", []) is None                      # no promise


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
        _tool("c7", "end_call", {"reason": "done"}),    # refused once: pets and utilities are still open
        _tool("c8", "end_call", {"reason": "done"}),    # accepted, but nothing said: a goodbye is asked for
        _text("c9", "Thanks, Linda. Goodbye!")])
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
    assert [(f.field, f.value) for f in out.facts] == [("available_from", "November 1st"),
                                                       ("parking_cost", "$150 a month per spot")]
    assert out.viewing.starts_at == _at(24, 13)
    trace = next(iter(runner.traces.values()))
    assert [t.speaker for t in trace.turns][:3] == ["landlord", "caller", "landlord"]
    assert trace.turns[-1].text.endswith("Goodbye!")
    # The landlord heard the Caller's words, and the Caller was asked for its goodbye in end_call's answer.
    assert landlord.sent[1]["item"]["content"][0]["text"].startswith("Hi, I'm an AI assistant")
    outputs = [e["item"]["output"] for e in caller.sent
               if e.get("item", {}).get("type") == "function_call_output"]
    assert outputs[-2].startswith("Not done yet") and outputs[-1] == tools.SAY_GOODBYE
    assert caller.closed and landlord.closed
    assert "voice" not in {c.listing_id for c in state.calls.values()}   # the plan's one call, ended
    await runtime.close()


def test_the_sim_runner_never_touches_the_phone_network():
    assert SimRunner.uses_phone is False and SimRunner.transport == "sim"
