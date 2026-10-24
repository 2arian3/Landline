"""The move-in date a landlord says (#138, #157): read in code from their words or the Caller's parts,
counted from the call's own day in Toronto, and told to the renter in words. A model's date is never kept.

Every row of the issue's table, with the call on Sat 24 Oct 2026 at 11:28 Toronto, through `settle` and
then through the Narrator's results text, and the card's date from the same key."""
import json
from datetime import UTC, date, datetime

import pytest

from landline.agents.auditor.audit import CallRecord, ListingContext, ToolFact, Turn, build
from landline.agents.auditor.values import Refused, call_day, settle
from landline.agents.caller import tools
from landline.agents.caller.brief import brief
from landline.contracts import (
    CallEnded,
    CallOutcome,
    CallPlan,
    CallsCompleted,
    CallsRequested,
    CallsScheduled,
    Fact,
    Listing,
    OutcomeReady,
    SessionEnded,
    SessionStarted,
)
from landline.db.catalogue import Catalogue
from landline.orchestrator import SessionRuntime, overrides

CALLED = datetime(2026, 10, 24, 15, 28, tzinfo=UTC)   # Saturday 11:28 in Toronto
SID = "s_dates00001"
CALL = "c_dates00001"

ROWS = [   # what the landlord said, the model's value, the key, what the renter reads
    ("It is available from tomorrow.", "tomorrow", "10-25", "free from Sunday, October 25"),
    ("It is only available from tomorrow.", "2025-09-02", "10-25", "free from Sunday, October 25"),
    ("It's available now.", "now", "now", "available now"),
    ("You can move in right away.", "now", "now", "available now"),
    ("It's free from today.", "today", "now", "available now"),
    # On a Saturday, next week starts on the coming Monday (the issue's first table said 11-02, a typo).
    ("From next week.", "next week", "10-26", "free from Monday, October 26"),
    ("Next Monday.", "next Monday", "10-26", "free from Monday, October 26"),
    ("November first.", "2026-11-01", "11-01", "free from Sunday, November 1"),
    ("From Nov 1.", "Nov 1", "11-01", "free from Sunday, November 1"),
    ("The 1st of November.", "11-01", "11-01", "free from Sunday, November 1"),
    ("The first of next month.", "first of next month", "11-01", "free from Sunday, November 1"),
    ("December.", "December", "12-01", "free from Tuesday, December 1"),
    ("End of the month.", "end of month", "10-31", "free from Saturday, October 31"),
    ("In two weeks.", "two weeks", "11-07", "free from Saturday, November 7"),
    # Already past this year, so the next one: the year is said, since it isn't the call's.
    ("September 2nd.", "September 2", "09-02", "free from Thursday, September 2, 2027"),
]

TRICKY = [   # words that once read as the wrong day
    ("The 2 bedroom is available right away.", "2025-09-02", "now", "available now"),
    ("Yes, the 3 bed unit is free today.", "2025-09-02", "now", "available now"),
    ("It's available now, it's on the second floor.", "2025-09-02", "now", "available now"),
    ("It's free from Sunday, November first.", "Sunday", "11-01", "free from Sunday, November 1"),
    ("Monday, November 2nd works.", "Monday", "11-02", "free from Monday, November 2"),
    ("Available Sunday the 1st.", "Sunday", "11-01", "free from Sunday, November 1"),
    ("It's rented right now, free from December 1st.", "now", "12-01", "free from Tuesday, December 1"),
    ("End of next month.", "next month", "11-30", "free from Monday, November 30"),
    ("11月底", "11-01", "11-30", "free from Monday, November 30"),
    ("Mid-November.", "November", "11-15", "free from Sunday, November 15"),
]


@pytest.mark.parametrize(("quote", "model", "key", "says"), ROWS + TRICKY)
def test_the_day_comes_from_the_quote_counted_from_the_call(quote, model, key, says):
    v = settle("available_from", model, quote, today=call_day(CALLED))
    assert v.value == key
    assert ("available now" if v.display == "now" else f"free from {v.display}") == says


@pytest.mark.parametrize("quote", ["Sometime soon, not sure.", "Yes, it's available.", "It may be free."])
def test_a_quote_that_names_no_day_drops_the_models_date(quote):
    with pytest.raises(Refused):
        settle("available_from", "2025-09-02", quote, today=call_day(CALLED))


def _listing() -> Listing:
    return Listing(listing_id="t:1", source="t", source_id="1", url="https://example.com/1",
                   first_seen=CALLED, last_seen=CALLED, address="77 Wellesley St E", lat=43.66, lng=-79.38,
                   rent=2400, monthly_cost=2400, beds=1, sqft=600)


async def _results(facts: list[Fact]) -> tuple[str, SessionRuntime]:
    rt = SessionRuntime(Catalogue([_listing()], "test"))
    meta = {"session_id": SID, "at": CALLED}
    await rt.start(SessionStarted(**meta, idem_key=f"{SID}:start", channel="phone"))
    await rt.emit(CallsRequested(**meta, idem_key=f"{SID}:plan", listing_ids=["t:1"]))
    await rt.emit(SessionEnded(**meta, idem_key=f"{SID}:end", reason="hangup"))
    plan = CallPlan(session_id=SID, listing_ids=["t:1"], created_at=CALLED)
    await rt.emit(CallsScheduled(**meta, idem_key=f"{SID}:scheduled", call_ids=[CALL], plan=plan))
    await rt.emit(CallEnded(**meta, idem_key=f"{SID}:ended", call_id=CALL, reason="done"))
    out = CallOutcome(call_id=CALL, listing_id="t:1", end_reason="done", facts=facts)
    await rt.emit(OutcomeReady(**meta, idem_key=f"{SID}:outcome", outcome=out))
    view = await rt.emit(CallsCompleted(**meta, idem_key=f"{SID}:done", call_ids=[CALL]))
    return view.results or "", rt


def _fact(quote: str, model: str) -> Fact:
    v = settle("available_from", model, quote, today=call_day(CALLED))   # as the Auditor writes it
    return Fact(field="available_from", value=v.display, quote=quote, speaker="Agent heard", at=CALLED)


@pytest.mark.parametrize(("quote", "model", "key", "says"), ROWS + TRICKY)
async def test_the_results_text_says_the_day_in_words(quote, model, key, says):
    results, _ = await _results([_fact(quote, model)])
    assert f"At 77 Wellesley St E, {says}." in results
    assert "2025" not in results and "-" not in results.split("St E")[1]   # never a date in digits


async def test_the_card_keeps_the_calls_day_after_it_has_passed():
    _, rt = await _results([_fact("It is only available from tomorrow.", "2025-09-02")])
    later = overrides.from_calls(rt.state(SID), rt.catalogue, today=date(2026, 10, 27))
    assert later["t:1"]["available_from"] == date(2026, 10, 25)   # not next year's October 25


async def test_a_dropped_date_says_nothing_about_the_day():
    results, _ = await _results([])
    assert "free from" not in results and "2025" not in results


# The Caller's record_available_from (#157): the model fills in the parts, code makes the day

def _parts(kind: str, **kw) -> dict:
    return {"kind": kind, **kw}


TOOL_ROWS = [   # the tool's parts, the landlord's words, the key, what the renter reads
    (_parts("tomorrow"), "It is available from tomorrow.", "10-25", "free from Sunday, October 25"),
    (_parts("now"), "It's free right now.", "now", "available now"),
    (_parts("weekday", weekday="monday"), "From Monday.", "10-26", "free from Monday, October 26"),
    (_parts("next_week"), "Next week.", "10-26", "free from Monday, October 26"),
    (_parts("in_weeks", weeks=2), "In two weeks.", "11-07", "free from Saturday, November 7"),
    (_parts("date", month=11, day=1), "November first.", "11-01", "free from Sunday, November 1"),
    (_parts("month", month=12), "December.", "12-01", "free from Tuesday, December 1"),
    (_parts("end_of_month"), "End of the month.", "10-31", "free from Saturday, October 31"),
    (_parts("date", month=9, day=2), "September 2nd.", "09-02", "free from Thursday, September 2, 2027"),
    # words no pattern reads: the parts carry the day
    (_parts("date", month=11, day=1), "Right after Halloween.", "11-01", "free from Sunday, November 1"),
    # the words that once read as the wrong day (TRICKY), with the parts that say them
    (_parts("now"), "The 2 bedroom is available right away.", "now", "available now"),
    (_parts("now"), "Yes, the 3 bed unit is free today.", "now", "available now"),
    (_parts("now"), "It's available now, it's on the second floor.", "now", "available now"),
    (_parts("date", month=11, day=1), "It's free from Sunday, November first.", "11-01",
     "free from Sunday, November 1"),
    (_parts("date", month=11, day=2), "Monday, November 2nd works.", "11-02", "free from Monday, November 2"),
    (_parts("date", month=11, day=1), "Available Sunday the 1st.", "11-01", "free from Sunday, November 1"),
    (_parts("date", month=12, day=1), "It's rented right now, free from December 1st.", "12-01",
     "free from Tuesday, December 1"),
    (_parts("date", month=11, day=30), "End of next month.", "11-30", "free from Monday, November 30"),
    (_parts("date", month=11, day=30), "11月底", "11-30", "free from Monday, November 30"),
    (_parts("date", month=11, day=15), "Mid-November.", "11-15", "free from Sunday, November 15"),
]


async def _caller(said: str) -> tools.CallerContext:
    rt = SessionRuntime(Catalogue([_listing()], "test"))
    meta = {"session_id": SID, "at": CALLED}
    await rt.start(SessionStarted(**meta, idem_key=f"{SID}:start", channel="phone"))
    await rt.emit(CallsRequested(**meta, idem_key=f"{SID}:plan", listing_ids=["t:1"]))
    await rt.emit(SessionEnded(**meta, idem_key=f"{SID}:end", reason="hangup"))
    plan = CallPlan(session_id=SID, listing_ids=["t:1"], created_at=CALLED)
    await rt.emit(CallsScheduled(**meta, idem_key=f"{SID}:scheduled", call_ids=[CALL], plan=plan))
    return tools.CallerContext(session_id=SID, call_id=CALL, listing_id="t:1", brief=brief(_listing(), plan),
                               runtime=rt, clock=lambda: CALLED, said=[said])


def _record(said: str, facts) -> CallRecord:
    turns = [Turn(0, "agent", "From when is it available?"), Turn(1, "landlord", said)]
    return CallRecord(call_id=CALL, listing_id="t:1", end_reason="done", turns=turns, ended_at=CALLED,
                      listing=ListingContext.of({"listing_id": "t:1", "address": "77 Wellesley St E"}),
                      tool_facts=facts)


@pytest.mark.parametrize(("parts", "quote", "key", "says"), TOOL_ROWS)
async def test_the_caller_gives_the_parts_and_code_makes_the_day(parts, quote, key, says):
    ctx = await _caller(quote)
    reply = await tools.handle("record_available_from", json.dumps({**parts, "quote": quote}), "fc_1", ctx)
    words = "now" if says == "available now" else says.removeprefix("free from ")
    assert reply.startswith(f"Recorded available from: {words}.")
    # the Auditor keeps the day, the renter reads it in words, and the card gets the same day
    out = build(_record(quote, [ToolFact(f.field, f.value, f.quote) for f in ctx.facts]), []).outcome
    results, rt = await _results(out.facts)
    assert f"At 77 Wellesley St E, {says}." in results and "2025" not in results
    card = overrides.from_calls(rt.state(SID), rt.catalogue, today=call_day(CALLED))
    assert card["t:1"]["available_from"] == overrides._day(key, call_day(CALLED))


@pytest.mark.parametrize(("parts", "quote", "said", "reply"), [
    (_parts("unknown"), "Not sure yet.", "Not sure yet.", "Not recorded: that names no day."),
    (_parts("weekday"), "From Monday.", "From Monday.", "Not recorded: that names no day."),   # no weekday
    # the parts and the words disagree: ask, don't store
    (_parts("date", month=9, day=2), "It is available from tomorrow.", "It is available from tomorrow.",
     "Not recorded: their words sound like Sunday, October 25, not Thursday, September 2, 2027."),
    (_parts("tomorrow"), "It's free from tomorrow.", "Hi, who's calling?", tools.UNSAID),   # never said
    (_parts("date", month=13, day=1), "The 1st.", "The 1st.", tools.NOT_CAUGHT),   # the schema refuses
])
async def test_a_day_the_words_dont_back_is_never_kept(parts, quote, said, reply):
    ctx = await _caller(said)
    got = await tools.handle("record_available_from", json.dumps({**parts, "quote": quote}), "fc_1", ctx)
    assert got.startswith(reply)
    assert ctx.facts == [] and ctx.runtime.state(SID).heard == {}


async def test_record_fact_still_reads_the_day_from_the_words_never_the_models_value():
    quote = "It is only available from tomorrow."
    ctx = await _caller(quote)
    args = {"field": "available_from", "value": "2025-09-02", "quote": quote}
    reply = await tools.handle("record_fact", json.dumps(args), "fc_1", ctx)
    assert reply.startswith("Recorded available from: Sunday, October 25.")
    assert [f.value for f in ctx.facts] == ["Sunday, October 25"]


async def test_record_fact_with_words_that_name_no_day_points_to_the_parts():
    ctx = await _caller("Right after Halloween.")
    args = {"field": "available_from", "value": "November 1", "quote": "Right after Halloween."}
    reply = await tools.handle("record_fact", json.dumps(args), "fc_1", ctx)
    assert reply.startswith("Not recorded: call record_available_from") and ctx.facts == []


def test_the_auditor_never_takes_a_models_date_from_a_live_fact():
    quote = "Sometime soon, not sure."
    out = build(_record(quote, [ToolFact("available_from", "2025-09-02", quote)]), []).outcome
    assert [f for f in out.facts if f.field == "available_from"] == []
