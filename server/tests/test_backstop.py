"""The Concierge's catch-up (A-04): a renter line in another script that got a reply with no tool is read
for its preferences and saved through update_preferences. Never for English. No network: the text model
is a fake."""
from datetime import UTC, datetime

from landline.agents.concierge import tools
from landline.agents.concierge.backstop import Turn, catch_up, foreign
from landline.contracts import Listing, PreferencesUpdated, SessionStarted
from landline.contracts.tools import PreferenceDelta
from landline.db.catalogue import Catalogue
from landline.orchestrator import SessionRuntime

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
