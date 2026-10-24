"""Every fact a call heard reads in plain words in the results text (#187): "available now" for "anytime",
"no other monthly fees" for a no, and the renter's own question with its answer, never "also: yes".

The issue's call: 798 Ossington Ave, Sat 24 Oct 2026 at 12:48 Toronto."""
import json
from datetime import UTC, datetime

import pytest

from landline.agents.auditor.audit import CallRecord, ListingContext, ToolFact, Turn, build
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
from landline.orchestrator import SessionRuntime

CALLED = datetime(2026, 10, 24, 16, 48, tzinfo=UTC)   # Saturday 12:48 in Toronto
SID, CALL = "s_words00001", "c_words00001"
ASKED = "Can I view the unit tomorrow?"
FEES_Q = "Are there any other monthly fees on top of the rent?"


def _listing() -> Listing:
    return Listing(listing_id="t:1", source="t", source_id="1", url="https://example.com/1",
                   first_seen=CALLED, last_seen=CALLED, address="798 Ossington Ave", lat=43.66, lng=-79.42,
                   rent=2300, monthly_cost=2300, beds=1, sqft=600)


def _plan(questions=()) -> CallPlan:
    return CallPlan(session_id=SID, listing_ids=["t:1"], created_at=CALLED, questions=list(questions))


def _record(turns: list[tuple[str, str]], facts: list[ToolFact]) -> CallRecord:
    return CallRecord(call_id=CALL, listing_id="t:1", end_reason="done", ended_at=CALLED,
                      turns=[Turn(i, who, text) for i, (who, text) in enumerate(turns)],
                      listing=ListingContext.of({"listing_id": "t:1", "address": "798 Ossington Ave"}),
                      tool_facts=facts)


async def _results(facts: list[Fact], questions=()) -> str:
    rt = SessionRuntime(Catalogue([_listing()], "test"))
    meta = {"session_id": SID, "at": CALLED}
    await rt.start(SessionStarted(**meta, idem_key=f"{SID}:start", channel="phone"))
    await rt.emit(CallsRequested(**meta, idem_key=f"{SID}:plan", listing_ids=["t:1"],
                                 extra_questions=list(questions)))
    await rt.emit(SessionEnded(**meta, idem_key=f"{SID}:end", reason="hangup"))
    await rt.emit(CallsScheduled(**meta, idem_key=f"{SID}:scheduled", call_ids=[CALL], plan=_plan(questions)))
    await rt.emit(CallEnded(**meta, idem_key=f"{SID}:ended", call_id=CALL, reason="done"))
    out = CallOutcome(call_id=CALL, listing_id="t:1", end_reason="done", facts=facts)
    await rt.emit(OutcomeReady(**meta, idem_key=f"{SID}:outcome", outcome=out))
    view = await rt.emit(CallsCompleted(**meta, idem_key=f"{SID}:done", call_ids=[CALL]))
    return view.results or ""


@pytest.mark.parametrize(("field", "value", "asked", "quote", "says"), [
    ("available_from", "now", "When can I move in?", "it's also available anytime", "available now"),
    ("available_from", "now", "When can I move in?", "It's vacant, move in whenever", "available now"),
    # a quote that names another day still wins over the Caller's `now` (#141's cross-check)
    ("available_from", "now", "When can I move in?", "It's free from December",
     "free from Tuesday, December 1"),
    ("fees", "none", FEES_Q, "No, nothing else.", "no other monthly fees"),
    ("fees", "none", FEES_Q, "No.", "no other monthly fees"),
    ("fees", "none", FEES_Q, "Just the rent.", "no other monthly fees"),
])
async def test_each_fact_the_auditor_keeps_reads_in_words(field, value, asked, quote, says):
    audit = build(_record([("agent", asked), ("landlord", quote)], [ToolFact(field, value, quote)]), [])
    assert [d.why for d in audit.dropped] == []
    results = await _results(audit.outcome.facts)
    assert results == f"At 798 Ossington Ave, {says}."


async def test_a_fee_with_an_amount_is_still_a_fee():
    quote = "No, just a $40 amenity fee every month."
    audit = build(_record([("agent", FEES_Q), ("landlord", quote)], [ToolFact("fees", "none", quote)]), [])
    assert [f.value for f in audit.outcome.facts] != ["no other monthly fees"]


async def _caller(questions=()) -> tools.CallerContext:
    rt = SessionRuntime(Catalogue([_listing()], "test"))
    meta = {"session_id": SID, "at": CALLED}
    await rt.start(SessionStarted(**meta, idem_key=f"{SID}:start", channel="phone"))
    await rt.emit(CallsRequested(**meta, idem_key=f"{SID}:plan", listing_ids=["t:1"]))
    await rt.emit(SessionEnded(**meta, idem_key=f"{SID}:end", reason="hangup"))
    plan = _plan(questions)
    await rt.emit(CallsScheduled(**meta, idem_key=f"{SID}:scheduled", call_ids=[CALL], plan=plan))
    return tools.CallerContext(session_id=SID, call_id=CALL, listing_id="t:1", brief=brief(_listing(), plan),
                               runtime=rt, clock=lambda: CALLED)


async def test_the_issues_call_reads_every_fact_and_the_renters_question():
    turns = [("agent", "Hi, I'm an AI assistant calling for a renter. Is 798 Ossington Ave still available?"),
             ("landlord", "Yes, it's also available anytime."),
             ("agent", "Does parking come with it, and what does it cost?"),
             ("landlord", "Parking is $100 a month extra."),
             ("agent", FEES_Q),
             ("landlord", "No, nothing else."),
             ("agent", ASKED),
             ("landlord", "Yeah, you can.")]
    ctx = await _caller([ASKED])
    calls = [("record_fact", {"field": "available", "value": "yes",
                              "quote": "Yes, it's also available anytime."}),
             ("record_available_from", {"kind": "now", "quote": "it's also available anytime"}),
             ("record_fact", {"field": "parking_cost", "value": "$100 a month",
                              "quote": "Parking is $100 a month extra."}),
             ("record_fact", {"field": "fees", "value": "none", "quote": "No, nothing else."}),
             ("record_fact", {"field": "other", "value": "yes", "quote": "Yeah, you can."})]
    landlord = [text for who, text in turns if who == "landlord"]
    for n, (name, args) in enumerate(calls):
        ctx.said = landlord[:[1, 1, 2, 3, 4][n]]   # each fact from the landlord's latest words (#192)
        reply = await tools.handle(name, json.dumps(args), f"fc_{n}", ctx)
        assert "Recorded " in reply, reply   # after the disclosure reminder on a first reply (#185)
    assert ctx.facts[-1].value == f"{ASKED} yes"   # the answer keeps the renter's question
    audit = build(_record(turns, [ToolFact(f.field, f.value, f.quote) for f in ctx.facts]), [])
    results = await _results(audit.outcome.facts, [ASKED])
    assert results == ("At 798 Ossington Ave, available now; parking: $100 a month per spot; no other "
                       "monthly fees. You asked, “Can I view the unit tomorrow?” They said yes.")
    assert "also:" not in results


async def test_two_questions_answered_yes_are_both_kept():
    asked = ["Can I view the unit tomorrow?", "Is there a gym?"]
    ctx = await _caller(asked)
    ctx.said = ["Yeah, you can.", "Yes, there is."]
    first = await tools.handle("record_fact", json.dumps({"field": "other", "value": "yes",
                                                          "quote": "Yeah, you can."}), "fc_1", ctx)
    second = await tools.handle("record_fact", json.dumps({"field": "other", "value": "yes",
                                                           "quote": "Yes, there is."}), "fc_2", ctx)
    assert "Recorded other" in first and "Recorded other" in second
    assert [f.value for f in ctx.facts] == [f"{asked[0]} yes", f"{asked[1]} yes"]
