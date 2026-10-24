"""The Concierge's catch-ups (A-04): a renter line in another script that got a reply with no tool is read
for its preferences and saved through update_preferences, never for English; and an English line asking
for calls or a viewing that got no call_listings records the plan (#127). No network: the text model is a
fake."""
import asyncio
import json
from datetime import UTC, datetime

import pytest

from landline.agents.concierge import tools
from landline.agents.concierge.backstop import (
    PLANNED,
    Turn,
    asks_for_calls,
    catch_up,
    catch_up_calls,
    claims_save,
    foreign,
    refs_in,
)
from landline.contracts import Listing, ListingFocused, PreferencesUpdated, SessionStarted
from landline.contracts.tools import PreferenceDelta
from landline.db.catalogue import Catalogue
from landline.orchestrator import SessionRuntime
from landline.voice import bridge
from landline.voice.line import Line
from landline.voice.recorder import PCMU_8K, CallRecorder

NOW = datetime(2026, 10, 24, 15, 0, tzinfo=UTC)
SID = "s_back000001"
KOREAN = "사실 한국어가 더 편해요. 방 두 개짜리 아파트를 찾고 있고, 한 달에 3000달러까지 괜찮아요."
FARSI = "سلام. دنبال یه آپارتمان یک خواب هستم تا 2,500 دلار در ماه."
READ = {"max_monthly": 3000, "min_beds": 2, "pets": [], "areas": [], "must_have": [], "move_in": None,
        "occupants": None, "language": "ko"}


class FakeReader:
    def __init__(self, out=None, error=None):
        self.out, self.error, self.asked = out, error, []

    async def json(self, system, user, schema, name="result"):
        self.asked.append(user)
        if self.error:
            raise self.error
        return self.out


async def _ctx() -> tools.ToolContext:
    listings = [Listing(listing_id=f"t:{i}", source="t", source_id=str(i), url=f"https://example.com/{i}",
                        first_seen=NOW, last_seen=NOW, address=f"{i} Main St", lat=43.65, lng=-79.38,
                        rent=2000 + 400 * i, monthly_cost=2000 + 400 * i, beds=i % 3) for i in range(1, 7)]
    rt = SessionRuntime(Catalogue(listings, "test"))
    await rt.start(SessionStarted(session_id=SID, at=NOW, idem_key=f"{SID}:start", channel="browser"))
    return tools.ToolContext(session_id=SID, call_id="c_back000001", runtime=rt)


def test_only_lines_mostly_in_another_script_count():
    assert foreign(KOREAN) and foreign(FARSI) and foreign("我有一只狗。") and foreign("ਮੇਰੇ ਕੋਲ ਇੱਕ ਕੁੱਤਾ ਹੈ।")
    assert not foreign("I need a two bedroom under 3000.")
    assert not foreign("Busco un apartamento de una habitación.")   # Latin script: never read again
    assert not foreign("Ok 네")                                       # too few letters to tell
    assert not foreign("[renter is looking at] 412 Markham St: 1 bed, $2,350 a month")


async def test_a_line_answered_with_no_tool_is_saved_through_update_preferences():
    ctx, reader = await _ctx(), FakeReader(READ)
    reply = await catch_up(reader, KOREAN, ctx)
    p = ctx.runtime.state(SID).preferences
    assert (p.max_monthly, p.min_beds, p.language) == (3000, 2, "ko")
    assert reply == ctx.runtime.get(SID).agent_says and reader.asked == [KOREAN]


async def test_english_no_reader_a_failure_or_nothing_said_saves_nothing():
    ctx = await _ctx()
    assert await catch_up(FakeReader(READ), "I need two bedrooms under 3000.", ctx) is None
    assert await catch_up(None, KOREAN, ctx) is None
    assert await catch_up(FakeReader(error=RuntimeError("429")), KOREAN, ctx) is None
    empty = dict(READ, max_monthly=None, min_beds=None, language="en")
    assert await catch_up(FakeReader(empty), KOREAN, ctx) is None
    p = ctx.runtime.state(SID).preferences
    assert (p.max_monthly, p.min_beds, p.language) == (None, None, None)


async def test_a_language_already_saved_is_left_alone_and_odd_codes_are_dropped():
    ctx = await _ctx()
    await ctx.runtime.emit(PreferencesUpdated(session_id=SID, at=NOW, idem_key=f"{SID}:lang",
                                              delta=PreferenceDelta(language="fa-IR")))
    await catch_up(FakeReader(dict(READ, language="fa")), FARSI, ctx)
    assert ctx.runtime.state(SID).preferences.language == "fa-IR"
    await catch_up(FakeReader(dict(READ, max_monthly=2500, language="persian")), FARSI, ctx)
    assert ctx.runtime.state(SID).preferences.language == "fa-IR"
    assert ctx.runtime.state(SID).preferences.max_monthly == 2500


# The turn watch, on audio's event order


def _done(*outputs, status="completed"):
    return {"type": "response.done", "response": {"status": status, "output": [{"type": t} for t in outputs]}}


def test_a_reply_with_no_tool_gives_the_line_once_whichever_comes_first():
    turn = Turn()
    turn.speech_started()
    turn.response_created()
    turn.response_done(_done("message"))
    assert turn.line() is None            # the transcript isn't in yet
    turn.transcript(FARSI)
    assert turn.line() == FARSI
    assert turn.line() is None            # once a turn
    turn.speech_started()
    turn.transcript(KOREAN)               # the transcript first, this time
    turn.response_created()
    turn.response_done(_done("message"))
    assert turn.line() == KOREAN


def test_a_tool_call_an_english_line_or_a_cut_off_reply_gives_nothing():
    turn = Turn()
    turn.speech_started()
    turn.transcript(KOREAN)
    turn.response_created()
    turn.tool_called()
    turn.response_done(_done("function_call"))
    turn.response_created()
    turn.response_done(_done("message"))   # the reply after the tool
    assert turn.line() is None
    turn.speech_started()
    turn.transcript("Two bedrooms, please.")
    turn.response_created()
    turn.response_done(_done("message"))
    assert turn.line() is None
    turn.speech_started()
    turn.response_done(_done("message"))               # the reply they talked over, finishing late
    turn.response_created()
    turn.response_done(_done("message", status="cancelled"))
    turn.transcript(KOREAN)
    assert turn.line() is None


# ── Calls the renter asked for (#127) ───────────────────────────────────────

ASKS = ["Can you can you call the first one? I really like it.", "Can you call them?", "Call the top three.",
        "Let's let's set a house visit for the first one.", "Book a viewing for number two.",
        "Contact the landlord.", "Could you call the second and the third?",
        "Can you check if it's still available?", "Set up a viewing for the last one, please.",
        # A hedge before the ask is still an ask (#133 review).
        "I'm not yet sure, but call the first one.", "I'm not sure, but call the first one.",
        "Please call the top three for me. I'm free Saturday afternoon, and you can book a viewing."]
NOT_ASKS = ["I'll call you back", "What do you call that area?", "Call me Sara", "Don't call yet",
            "Don't call them yet.", "I really like the first one.", "I called the landlord last week.",
            "Yeah, yeah. Let's do that.", "Who should I call?", "그 집에 전화해 주세요",
            # The renter doing it, naming something, or another sense of the word (#133 review).
            "Should I call the landlord myself?", "I'll call them myself.", "What do you call those?",
            "Can you set the map to only showing two-bedrooms?",
            "Is it close enough that I can get to visit my mom?",
            # The demo brief's other lines.
            "Hi. I'm looking for a one bedroom in Toronto, under twenty eight hundred dollars a month.",
            "Yes, please go ahead.", "That's everything, thanks."]
CALL_FIRST = "Can you can you call the first one? I really like it."


def test_only_a_plain_ask_for_calls_or_a_viewing_counts():
    for said in ASKS:
        assert asks_for_calls(said), said
    for said in NOT_ASKS:
        assert not asks_for_calls(said), said


async def test_the_listings_come_from_the_line_or_the_card_in_focus():
    ctx = await _ctx()
    assert refs_in(CALL_FIRST, ctx) == ["1"]
    assert refs_in("Call the top three.", ctx) == ["1", "2", "3"]
    assert refs_in("Book a viewing for number two.", ctx) == ["2"]
    assert refs_in("Could you call the second and the third?", ctx) == ["2", "3"]
    assert refs_in("Contact the landlord.", ctx) == []   # nothing named: call_listings decides
    fourth = ctx.runtime.get(SID).board[3]
    await ctx.runtime.emit(ListingFocused(session_id=SID, at=NOW, idem_key=f"{SID}:tap", origin="ui",
                                          listing_id=fourth.listing_id))
    assert refs_in("Contact the landlord.", ctx) == [str(fourth.rank)]
    # Only the words the request is about (#133 review), and a plural target is never the card in focus.
    for said, refs in [("Can you call the first one? I'd like to move in on the third.", ["1"]),
                       ("Call the first one, I saw it last week.", ["1"]),
                       ("Can you call them? I really like it.", []),
                       ("Call the top one.", []), ("Can you call the place on Markham?", [])]:
        assert refs_in(said, ctx) == refs, said


async def test_a_target_it_cant_read_or_a_plan_not_recorded_gives_no_note():
    ctx = await _ctx()
    for said in ["Can you call them?", "Call the top one.", "Could you call number nine?", "Call #9."]:
        assert await catch_up_calls(said, ctx) is None, said   # never the top three by default
    assert ctx.runtime.state(SID).request is None


def _answered(turn: Turn, said: str, tools_called: tuple[str, ...] = ()) -> None:
    turn.speech_started()
    turn.transcript(said)
    turn.response_created()
    for name in tools_called:
        turn.tool_called(name)
    turn.response_done({"response": {"status": "completed", "output": [{"type": "message"}]}})


def test_a_turn_without_call_listings_gives_the_line_once_and_one_with_it_gives_nothing():
    turn = Turn()
    _answered(turn, CALL_FIRST, ("give_feedback",))   # "I really like it" alone isn't the call
    assert turn.call_line() == CALL_FIRST and turn.call_line() is None
    _answered(turn, CALL_FIRST, ("call_listings",))
    assert turn.call_line() is None
    _answered(turn, "I'll call you back.")
    assert turn.call_line() is None


async def test_call_the_first_one_records_the_plan_for_listing_1():
    ctx = await _ctx()
    first = ctx.runtime.get(SID).board[0]
    reply = await catch_up_calls(CALL_FIRST, ctx)
    assert ctx.runtime.state(SID).request.listing_ids == [first.listing_id]
    assert reply.startswith(f"Calling {first.address} right after you hang up")
    assert await catch_up_calls("I'll call you back.", ctx) is None


class _Higgs:
    def __init__(self, events):
        self._events = events

    async def send(self, event):
        pass

    async def events(self):
        for event in self._events:
            yield event


class _NoAudio:
    async def send_audio(self, b64):
        pass

    async def send_clear(self):
        pass


async def test_through_the_bridge_a_reply_with_no_call_listings_records_the_plan_once_and_reads_it_back():
    ctx = await _ctx()
    first = ctx.runtime.get(SID).board[0].listing_id
    no_tool = {"type": "response.done", "response": {"status": "completed", "output": [{"type": "message"}]}}
    events = [
        {"type": "input_audio_buffer.speech_started", "item_id": "u1", "audio_start_ms": 0},
        {"type": "input_audio_buffer.speech_stopped", "item_id": "u1", "audio_end_ms": 900},
        {"type": "response.created"},
        {"type": "conversation.item.input_audio_transcription.completed", "item_id": "u1",
         "transcript": CALL_FIRST},
        {"type": "response.output_audio_transcript.done", "item_id": "a1",
         "transcript": "I can't call the landlord directly, but I can help you contact them."},
        no_tool,
        {"type": "response.created"}, no_tool,   # another reply in the same turn: still once
    ]
    sent: list[dict] = []

    async def send(event):
        sent.append(event)

    line = Line(send)
    await bridge._higgs_to_renter(_Higgs(events), _NoAudio(), ctx, bridge.Playback(8000),
                                  CallRecorder(PCMU_8K), line)
    await asyncio.gather(*list(bridge._CATCHING))
    assert ctx.runtime.state(SID).request.listing_ids == [first]
    notes = [e["item"]["content"][0]["text"] for e in sent if e["type"] == "conversation.item.create"]
    assert len(notes) == 1 and notes[0].startswith(f"{PLANNED} Calling ")
    assert sent[-1] == {"type": "response.create"}


# ── A save the Concierge claimed without a tool (#96, #129) ─────────────────

CLAIMS = ["I've updated your budget to three thousand dollars.", "Perfect, I've saved that for you.",
          "Okay, I’ve noted that you have a dog.", "I have set your move-in date to December first.",
          # Explicit claims after a filler (#135 review).
          "Got it. I've updated your budget to 3000.", "Got it! I've saved that.",
          "So, I've updated your search.", "Okay, so I've updated your budget."]
NO_CLAIMS = ["Got it.", "What's your budget?", "I can't call the landlord directly.",
             "Sure! How many bedrooms do you need?", "You said you've updated your plans?", "",
             # An acknowledgement isn't a claim to have saved anything (#135 review).
             "Got it, two bedrooms under three thousand.", "Got it, just you. What's your budget?",
             "Got it, what area are you looking in?"]
BUDGET = "My max budget is three k per month."
BUDGET_READ = {"max_monthly": 3000, "min_beds": None, "pets": [], "areas": [], "must_have": [],
               "move_in": None, "occupants": None, "language": "en"}
NOT_A_TOOL = {"type": "response.done", "response": {"status": "completed", "output": [{"type": "message"}]}}


def test_only_a_reply_that_starts_by_claiming_a_save_counts():
    for reply in CLAIMS:
        assert claims_save(reply), reply
    for reply in NO_CLAIMS:
        assert not claims_save(reply), reply


def _turn(renter: str, agent: str, *, tool: str | None = None) -> list[dict]:
    events = [{"type": "input_audio_buffer.speech_started", "item_id": "u1", "audio_start_ms": 0},
              {"type": "input_audio_buffer.speech_stopped", "item_id": "u1", "audio_end_ms": 900},
              {"type": "conversation.item.input_audio_transcription.completed", "item_id": "u1",
               "transcript": renter}]
    if tool:
        events += [{"type": "response.created"},
                   {"type": "response.function_call_arguments.done", "name": tool, "call_id": "fc_1",
                    "arguments": '{"max_monthly": 3000}'},
                   {"type": "response.done", "response": {"status": "completed",
                                                          "output": [{"type": "function_call"}]}}]
    return events + [{"type": "response.created"},
                     {"type": "response.output_audio_transcript.done", "item_id": "a1", "transcript": agent},
                     NOT_A_TOOL]


async def _through_the_bridge(ctx, events, reader) -> None:
    await bridge._higgs_to_renter(_Higgs(events), _NoAudio(), ctx, bridge.Playback(8000),
                                  CallRecorder(PCMU_8K), reader=reader)
    await asyncio.gather(*list(bridge._CATCHING))


async def test_a_claimed_save_with_no_tool_reads_the_line_and_saves_it_once():
    ctx, reader = await _ctx(), FakeReader(BUDGET_READ)
    claim = "I've updated your budget to three thousand dollars."
    events = _turn(BUDGET, claim) + [{"type": "response.created"},   # a second reply in the same turn
                                     {"type": "response.output_audio_transcript.done", "item_id": "a2",
                                      "transcript": claim}, NOT_A_TOOL]
    await _through_the_bridge(ctx, events, reader)
    assert reader.asked == [BUDGET]
    assert ctx.runtime.state(SID).preferences.max_monthly == 3000


async def test_no_read_when_a_tool_ran_or_the_reply_claims_nothing():
    ctx, reader = await _ctx(), FakeReader(BUDGET_READ)
    await _through_the_bridge(ctx, _turn(BUDGET, "I've updated your budget to three thousand dollars.",
                                         tool="update_preferences"), reader)
    assert reader.asked == []   # the tool saved it itself
    ctx, reader = await _ctx(), FakeReader(BUDGET_READ)
    await _through_the_bridge(ctx, _turn(BUDGET, "What area are you looking in?"), reader)
    assert reader.asked == [] and ctx.runtime.state(SID).preferences.max_monthly is None


async def test_a_catch_up_adds_to_lists_instead_of_replacing_them():
    ctx = await _ctx()
    await ctx.runtime.emit(PreferencesUpdated(session_id=SID, at=NOW, idem_key=f"{SID}:p0",
                                              delta=PreferenceDelta(pets=["dog"], must_have=["laundry"])))
    read = {**BUDGET_READ, "max_monthly": None, "pets": ["cat", "dog"], "must_have": ["parking"]}
    await catch_up(FakeReader(read), "We have a cat too, and I need parking.", ctx, claimed=True)
    p = ctx.runtime.state(SID).preferences
    assert (p.pets, p.must_have) == (["dog", "cat"], ["laundry", "parking"])


class SlowReader(FakeReader):
    """Reads "three k" while the renter corrects it to 3500 and the Concierge saves that."""

    def __init__(self, out, ctx):
        super().__init__(out)
        self.ctx = ctx

    async def json(self, system, user, schema, name="result"):
        await self.ctx.runtime.emit(PreferencesUpdated(session_id=SID, at=NOW, idem_key=f"{SID}:newer",
                                                       delta=PreferenceDelta(max_monthly=3500)))
        return await super().json(system, user, schema, name)


async def test_a_catch_up_that_lands_after_a_newer_save_is_dropped():
    ctx = await _ctx()
    assert await catch_up(SlowReader(BUDGET_READ, ctx), BUDGET, ctx, claimed=True) is None
    assert ctx.runtime.state(SID).preferences.max_monthly == 3500


# #174: "set up a visit tomorrow" recorded a call with no viewing window and no leave to book, so nothing
# was booked. The call catch-up reads the whole ask; call_listings keeps only times the renter said.
@pytest.mark.parametrize(("said", "refs", "window", "may_book"), [
    ("Can you call the first option landlord to us set up a visit tomorrow?", [1],
     [("tomorrow", "09:00", "18:00")], True),
    ("Call the top two and book a viewing Saturday afternoon", [1, 2],
     [("saturday", "12:00", "17:00")], True),
    # A time to see it is its window; "see it" alone isn't leave to book (#154's "see if it's available").
    ("Call the first one, I can see it tomorrow between 1 and 5", [1],
     [("tomorrow", "13:00", "17:00")], False),
    ("Call the first one and ask if it's available", [1], [], False),
    ("Call the second one, don't book anything", [2], [], False),
    ("Go ahead and call the best ones for me.", [1, 2, 3], [], False),
])
async def test_the_call_catch_up_reads_the_whole_ask(said, refs, window, may_book):
    ctx = await _ctx()
    ctx.renter_said = said
    ctx.renter_lines.append(said)
    board = ctx.runtime.get(SID).board
    assert await catch_up_calls(said, ctx) is not None
    plan = ctx.runtime.state(SID).request
    assert plan.listing_ids == [board[r - 1].listing_id for r in refs]
    assert [(a.day, a.from_, a.to) for a in plan.availability] == window
    assert plan.may_book is may_book


async def test_a_short_time_answer_after_the_plan_is_its_viewing_window():
    ctx = await _ctx()
    await catch_up_calls("Call the first one.", ctx)
    ids = ctx.runtime.state(SID).request.listing_ids
    for line in ["I'm moving in tomorrow.", "Next week, probably."]:   # a move-in date, or no day
        assert await catch_up_calls(line, ctx) is None, line
    ctx.renter_lines.append("Tomorrow morning")
    assert await catch_up_calls("Tomorrow morning", ctx) is not None
    plan = ctx.runtime.state(SID).request
    assert plan.listing_ids == ids and [(a.day, a.from_, a.to) for a in plan.availability] == [
        ("tomorrow", "09:00", "12:00")]
    turn = Turn()
    _answered(turn, "Tomorrow morning")
    assert turn.call_line() == "Tomorrow morning"   # the bridge hands a time answer to the catch-up too


_fc = iter(range(1000))


async def _call(ctx, **args):
    return await tools.handle("call_listings", json.dumps(args), f"fc_test_{next(_fc)}", ctx)


async def test_call_listings_keeps_only_viewing_times_the_renter_said():
    """f2's holdout: the model gave "tomorrow 09:00-17:00" the renter never said, with leave to book."""
    ctx = await _ctx()
    ctx.renter_lines = ["Two bedrooms, up to 3,000 a month.", "Go ahead and call the best ones for me."]
    ctx.renter_said = ctx.renter_lines[-1]
    window = [{"day": "tomorrow", "from": "09:00", "to": "17:00"}]
    reply = await _call(ctx, listing_refs=["1"], availability=window)
    plan = ctx.runtime.state(SID).request
    assert plan.availability == [] and "Ask when they are free for a viewing." in reply
    assert len(plan.listing_ids) == 3   # "the best ones": the top three, not the one the model picked
    ctx.renter_lines.append("I can view on Sunday from noon to 4.")
    ctx.renter_said = ctx.renter_lines[-1]
    await _call(ctx, availability=[{"day": "sunday", "from": "12:00", "to": "16:00"}])
    assert [(a.day, a.from_, a.to) for a in ctx.runtime.state(SID).request.availability] == [
        ("sunday", "12:00", "16:00")]
    ctx = await _ctx()   # off the phone, no lines to go by: the model's word stands
    await _call(ctx, listing_refs=["1"], availability=window)
    assert len(ctx.runtime.state(SID).request.availability) == 1


async def test_new_viewing_times_with_nothing_said_about_booking_ask_whether_to_book():
    """#189: the renter gave tomorrow, nothing was said about booking, and the Concierge never asked."""
    tomorrow = [{"day": "tomorrow", "from": "09:00", "to": "17:00"}]
    ctx = await _ctx()
    ctx.renter_lines = ["Call the first one, I can come see it tomorrow."]
    ctx.renter_said = ctx.renter_lines[-1]
    reply = await _call(ctx, listing_refs=["1"], availability=tomorrow)
    assert reply.endswith("Ask whether you may book a viewing in that window.")
    reply = await _call(ctx, availability=tomorrow)   # the same times again: asked once
    assert reply.endswith("Booking: I won't book.")
    ctx = await _ctx()
    ctx.renter_lines = ["Call the first one tomorrow, but don't book anything."]
    ctx.renter_said = ctx.renter_lines[-1]
    reply = await _call(ctx, listing_refs=["1"], availability=tomorrow)
    assert "Ask whether" not in reply and "Booking: I won't book." in reply


async def test_a_complete_plan_tells_the_agent_to_say_goodbye_and_end_the_call():
    """Mahyar's test: the agent waited for the renter's bye. Listings, viewing times, and booking allowed or
    turned down make the plan complete; until then the reply asks for what's missing."""
    friday = [{"day": "friday", "from": "17:00", "to": "20:00"}]
    done = ("The plan is complete: tell them you'll call as soon as you hang up and text the results, say "
            "goodbye, and call end_session.")
    ctx = await _ctx()
    ctx.renter_lines = ["Can you call the top two places?"]
    ctx.renter_said = ctx.renter_lines[-1]
    assert done not in await _call(ctx, listing_refs=["1", "2"])                    # no times yet
    ctx.renter_lines.append("Friday evening works for me, from 5 to 8.")
    ctx.renter_said = ctx.renter_lines[-1]
    reply = await _call(ctx, availability=friday)
    assert done not in reply and "Ask whether you may book" in reply               # booking not asked yet
    ctx.renter_lines.append("Sure, book a viewing if they can do that time.")
    ctx.renter_said = ctx.renter_lines[-1]
    assert (await _call(ctx, may_book=True)).endswith(done)
    ctx = await _ctx()
    ctx.renter_lines = ["Call the first one, I'm free Sunday noon to 4, but don't book anything."]
    ctx.renter_said = ctx.renter_lines[-1]
    sunday = [{"day": "sunday", "from": "12:00", "to": "16:00"}]
    reply = await _call(ctx, listing_refs=["1"], availability=sunday)
    assert reply.endswith(done)                                                     # booking turned down


async def test_a_plain_yes_to_the_agents_booking_question_books_that_time():
    """#174's row: "Should I book tomorrow at 10?" "Yes." with no call_listings in the reply."""
    ctx = await _ctx()
    await catch_up_calls("Call the first one.", ctx)
    ctx.renter_lines = ["Call the first one.", "Yes."]
    ctx.renter_said, ctx.agent_before, ctx.agent_said = "Yes.", "Should I book tomorrow at 10?", "Great!"
    assert await catch_up_calls("Yes.", ctx) is not None
    plan = ctx.runtime.state(SID).request
    assert [(a.day, a.from_, a.to) for a in plan.availability] == [("tomorrow", "10:00", "11:00")]
    assert plan.may_book is True
    turn = Turn()
    _answered(turn, "Yes.")
    assert turn.call_line() == "Yes."


async def test_a_viewing_the_reply_promised_records_its_times_and_asks_before_booking():
    """#174's row: "Yeah, that's true." got "Great, I'll set up the viewing for tomorrow." and no tool.
    The times are kept (the renter said tomorrow); leave to book is asked for, never assumed."""
    ctx = await _ctx()
    first = "Can you call the first one? I could see it tomorrow."
    ctx.renter_lines, ctx.renter_said = [first], first
    await catch_up_calls(first, ctx)
    ctx.renter_lines.append("Yeah, that's true.")
    ctx.renter_said, ctx.agent_before = "Yeah, that's true.", "What time works best for you to view it?"
    ctx.agent_said = "Great, I'll set up the viewing for tomorrow."
    reply = await catch_up_calls("Yeah, that's true.", ctx)
    plan = ctx.runtime.state(SID).request
    assert reply is not None and "Ask whether you may book a viewing." in reply and not plan.may_book
    assert [(a.day, a.from_, a.to) for a in plan.availability] == [("tomorrow", "09:00", "18:00")]
    turn = Turn()
    _answered(turn, "Yeah, that's true.")
    turn.agent_said("Great, I'll set up the viewing for tomorrow.")
    assert turn.call_line() == "Yeah, that's true."
