"""The Auditor (A-07): grounding, numbers, spoken times, red flags, reconciliation, conflicts, degraded path.

No network: the transcript extractor is a fake that returns what a model might, wrong answers included.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from landline.agents.auditor import CallRecord, ListingContext, ToolFact, Turn, audit, build, grounding, when
from landline.agents.auditor.extract import Candidate, parse
from landline.agents.auditor.flags import red_flags
from landline.agents.auditor.numbers import amounts
from landline.agents.auditor.values import Refused, settle
from landline.contracts import EVENT, OutcomeReady, Viewing
from landline.orchestrator.state import outcome_status

TORONTO = ZoneInfo("America/Toronto")
NOW = datetime(2026, 10, 24, 15, 0, tzinfo=TORONTO)   # a Saturday afternoon
LISTING = ListingContext.of({"listing_id": "livrent:1", "address": "322 Dupont St", "rent": 2690,
                             "monthly_cost": 2690, "parking_cost": 150, "pets": "allowed",
                             "laundry": "in_unit", "fees": []})


def call(turns, tool_facts=(), **kw) -> CallRecord:
    ts = [Turn(i, s, t) for i, (s, t) in enumerate(turns)]
    return CallRecord(call_id="c_1", listing_id="livrent:1", end_reason=kw.pop("end_reason", "done"),
                      turns=ts, listing=kw.pop("listing", LISTING), ended_at=NOW,
                      tool_facts=tool_facts, **kw)


# Grounding

@pytest.mark.parametrize(("quote", "ok"), [
    ("two spots, 180 each", True),
    ("Two spots — $180 each!", True),        # punctuation and case don't matter
    ("we have two spot, 180 each", True),    # fuzzy ≥ 0.85
    ("two spot 180 each", False),            # under five words: exact only
    ("two spots, 150 each", False),          # a changed number never passes as fuzzy
    ("pets are allowed", False),             # the source negates it
    ("no pets are allowed", True),
    ("180", False),                          # one token proves nothing
    ("a friendly building with a gym", False),
])
def test_grounding(quote, ok):
    src = ["We have two spots, $180 each. And no pets are allowed, sorry."]
    assert grounding.verify(quote, src) is ok


def test_grounding_matches_inside_one_source_only():
    src = ["The rent is 2,690", "plus hydro"]
    assert grounding.find("2690 plus hydro", src) is None
    assert grounding.find("rent is 2690", src).source == 0


def test_grounding_mandarin_by_character():
    src = ["停车位一个月一百八十，不能养宠物。"]
    assert grounding.verify("停车位一个月一百八十", src)
    assert grounding.verify("不能养宠物", src)
    assert not grounding.verify("能养宠物", src)       # negated in the source


# Numbers

@pytest.mark.parametrize(("text", "nums"), [
    ("two spots, one eighty each", [2, 180]),
    ("It's $2,690 a month", [2690]),
    ("twenty-six fifty plus utilities", [2650]),
    ("eighteen hundred", [1800]),
    ("a hundred and eighty a month", [180]),
    ("2 grand deposit", [2000]),
    ("2.5k", [2500]),
    ("房租两千六", [2600]),
    ("押金两千六百五十", [2650]),
    ("停车一百八", [180]),
])
def test_amounts(text, nums):
    assert amounts(text) == nums


# Viewing times

@pytest.mark.parametrize(("text", "expected"), [
    ("Saturday at two", datetime(2026, 10, 31, 14, 0, tzinfo=TORONTO)),   # today's 2pm passed: next week
    ("tomorrow at 3:30 pm", datetime(2026, 10, 25, 15, 30, tzinfo=TORONTO)),
    ("Sunday at 11am", datetime(2026, 10, 25, 11, 0, tzinfo=TORONTO)),
    ("October 28th at 10 in the morning", datetime(2026, 10, 28, 10, 0, tzinfo=TORONTO)),
    ("the 30th at noon", datetime(2026, 10, 30, 12, 0, tzinfo=TORONTO)),
    ("明天上午十点半", datetime(2026, 10, 25, 10, 30, tzinfo=TORONTO)),
    ("下周三下午3点", datetime(2026, 10, 28, 15, 0, tzinfo=TORONTO)),
    ("Monday 2 pm", datetime(2026, 10, 26, 14, 0, tzinfo=TORONTO)),
    ("today at two", None),          # already passed
    ("Sunday", None),                # no hour: never guessed
    ("two spots", None),
])
def test_spoken_time(text, expected):
    assert when.parse(text, NOW) == expected


# Red flags: the Caller stand-in's rules (A-05), each with its own case, plus the ones added here

@pytest.mark.parametrize(("said", "codes"), [
    (["Send the deposit by e-transfer before the viewing and it's yours."], {"deposit_before_viewing"}),
    (["Payment is through Western Union only."], {"untraceable_payment"}),
    (["Once you send the first month I can hold it for you. It's e-transfer."], {"deposit_before_viewing"}),
    (["I'm out of the country right now, my cousin handles it."], {"owner_away"}),
    (["I will courier the keys to you."], {"owner_away"}),
    (["We do not do viewings, sorry."], {"no_viewing"}),
    (["看房之前要先转账押金。"], {"deposit_before_viewing"}),
    (["我现在在国外，可以快递钥匙给你。"], {"owner_away"}),
    (["I'm abroad, so I can't show you the unit. E-transfer the deposit and I'll mail the keys."],
     {"deposit_before_viewing", "owner_away", "no_viewing"}),
    # Not flags: rent by e-transfer and first and last at signing are normal; a busy Saturday isn't a refusal.
    (["Rent is by e-transfer on the first. First and last when you sign, after the viewing."], set()),
    (["I can't show it Saturday, but Sunday works."], set()),
])
def test_red_flags(said, codes):
    found = red_flags(said)
    assert {f.code for f in found} == codes
    assert all(f.quote and any(f.quote in s for s in said) for f in found)


# Values from words

def test_two_spots_at_180_is_180_a_spot():
    v = settle("parking_cost", "two spots at 180", "We have two spots, 180 each.")
    assert (v.amount, v.count, v.value) == (180, 2, "180")
    assert "$360 for all" in v.display


def test_two_spots_for_360_is_divided_in_code():
    v = settle("parking_cost", "", "Two spots for 360 a month total.", count=2, each=False)
    assert (v.amount, v.count) == (180, 2)


def test_model_amount_must_be_in_the_quote():
    with pytest.raises(Refused):
        settle("rent", "2700", "The rent is twenty-six ninety.", amount=2700)
    assert settle("rent", "2690", "The rent is twenty-six ninety.", amount=2690).amount == 2690


def test_value_its_quote_contradicts_is_refused():
    with pytest.raises(Refused):
        settle("pets", "allowed", "no pets, sorry")
    assert settle("pets", "", "cats are fine but no dogs").value == "cats_only"


def test_deposit_is_one_time():
    v = settle("fees", "deposit", "There's a deposit of one month, 2,690.", name="deposit")
    assert (v.name, v.period, v.amount) == ("deposit", "one_time", 2690)


# The audit

def test_live_facts_from_agent_turns_are_dropped():
    """PORTING #15: a read-back the landlord only agrees to is the agent's words, not a fact."""
    c = call([("agent", "So it's listed at $2,690 with parking, right?"),
              ("landlord", "Yeah."),
              ("landlord", "Parking is extra though, 180 a month.")],
             tool_facts=[ToolFact("rent", "2690", "listed at $2,690 with parking", 1),
                         ToolFact("parking_cost", "180", "Parking is extra though, 180 a month", 2)])
    a = build(c, [])
    assert [(f.field, f.quote) for f in a.outcome.facts] == [
        ("parking_cost", "Parking is extra though, 180 a month")]
    assert [(d.field, d.why) for d in a.dropped] == [("rent", "agent_words")]
    assert a.tool == {"recorded": 2, "kept": 1, "corrected": 0, "dropped": 1}


def test_corrected_read_back_wins():
    c = call([("landlord", "Rent is 2,690."),
              ("agent", "Great, so 2,690 a month?"),
              ("landlord", "Oh sorry, it went up, it's 2,750 now.")],
             tool_facts=[ToolFact("rent", "2690", "Rent is 2,690", 0)])
    a = build(c, [Candidate("rent", "2750", "it's 2,750 now", turn=2, amount=2750)])
    assert [f.value for f in a.outcome.facts] == ["$2,750 a month"]
    assert a.tool["corrected"] == 1
    rent = next(x for x in a.outcome.conflicts if x.field == "rent")
    assert (rent.listed, rent.heard) == ("$2,690", "$2,750")
    cost = next(x for x in a.outcome.conflicts if x.field == "monthly_cost")
    assert cost.heard == "$2,750 a month"


def test_invented_quotes_and_amounts_never_reach_the_outcome():
    c = call([("landlord", "It's available from November first. Utilities are extra.")])
    a = build(c, [Candidate("available_from", "Nov 1", "available from November first", turn=0),
                  Candidate("utilities_included", "heat, water", "heat and water included", turn=0),
                  Candidate("rent", "2500", "available from November first", turn=0, amount=2500)])
    assert [f.field for f in a.outcome.facts] == ["available_from"]
    assert {d.why.split(":")[0] for d in a.dropped} == {"ungrounded", "unreadable"}


def test_required_fee_changes_monthly_cost_in_code():
    c = call([("landlord", "There's a mandatory amenity fee of 75 a month on top of rent.")])
    a = build(c, [Candidate("fees", "amenity fee", "mandatory amenity fee of 75 a month", turn=0, amount=75,
                            period="monthly", mandatory=True, name="amenity")])
    cost = next(x for x in a.outcome.conflicts if x.field == "monthly_cost")
    assert (cost.listed, cost.heard) == ("$2,690 a month", "$2,765 a month")


def test_listing_conflicts_and_flags():
    c = call([("landlord", "No pets, sorry. And it's coin laundry in the basement."),
              ("landlord", "I'm overseas, so e-transfer the deposit to hold it and I'll mail the keys.")])
    a = build(c, [Candidate("pets", "no_pets", "No pets, sorry", turn=0),
                  Candidate("laundry", "in_building", "coin laundry in the basement", turn=0)])
    assert {x.field for x in a.outcome.conflicts} == {"pets", "laundry"}
    assert "wants money before anyone sees the unit" in a.outcome.red_flags
    assert "the owner is away and can't show the unit in person" in a.outcome.red_flags


def test_viewing_from_slot_words_and_from_booking():
    c = call([("landlord", "Saturday at two works.")], slot_text="Saturday at two")
    assert build(c, []).outcome.viewing.starts_at == datetime(2026, 10, 31, 14, tzinfo=TORONTO)
    booked = Viewing(starts_at=datetime(2026, 10, 25, 11, tzinfo=TORONTO), slot_text="Sunday at eleven")
    assert build(call([], viewing=booked), []).outcome.viewing == booked


def test_fact_time_is_codes_and_outcome_fits_the_event():
    c = call([("landlord", "Yes, it's still available.")], tool_facts=[ToolFact("available", "yes",
                                                                                "it's still available", 0)])
    out = build(c, []).outcome
    assert out.facts[0].at == NOW and out.facts[0].speaker == "Agent heard"
    ev = OutcomeReady(session_id="s_1", at=NOW, idem_key="c_1:outcome", outcome=out)
    assert EVENT.validate_json(ev.model_dump_json()) == ev


async def test_extractor_timeout_keeps_the_live_record():
    async def slow(*_):
        await asyncio.sleep(5)
        return {"facts": []}

    c = call([("landlord", "Yes, it's still available.")],
             tool_facts=[ToolFact("available", "yes", "it's still available", 0)])
    a = await audit(c, complete=slow, timeout_s=0.01)
    assert a.extractor == "timeout" and not a.degraded
    assert [f.field for f in a.outcome.facts] == ["available"]


async def test_failure_degrades_to_facts_unknown():
    c = call([("landlord", "hi")], questions=["Do you have a cosigner?"])
    object.__setattr__(c, "turns", None)        # something unexpected deep inside
    a = await audit(c)
    assert a.degraded and a.outcome.facts == [] and a.outcome.end_reason == "done"
    assert a.outcome.open_questions == ["Do you have a cosigner?"]


async def test_extractor_output_is_parsed_and_grounded():
    async def fake(system, user, schema):
        assert "[0] LANDLORD" in user and "[1] AGENT" in user
        return {"facts": [
            {"field": "pets", "turn": 0, "quote": "cats are fine, no dogs", "value": "cats_only",
             "amount": None, "count": None, "each": None, "period": None, "mandatory": None, "name": None},
            {"field": "rent", "turn": 1, "quote": "so 2,690 a month", "value": "2690", "amount": 2690,
             "count": None, "each": None, "period": "monthly", "mandatory": True, "name": None},
            {"field": "nonsense", "turn": 0, "quote": "x y", "value": "", "amount": None, "count": None,
             "each": None, "period": None, "mandatory": None, "name": None},
        ]}

    c = call([("landlord", "Cats are fine, no dogs."), ("agent", "Got it, so 2,690 a month?")])
    a = await audit(c, complete=fake)
    assert [(f.field, f.value) for f in a.outcome.facts] == [("pets", "cats only")]
    assert [d.why for d in a.dropped] == ["agent_words"]
    assert len(parse({"facts": [{"field": "nonsense", "quote": "x"}]})) == 0


def test_lease_then_month_to_month_is_the_year():
    assert settle("lease_months", "", "One year lease, then month to month.").value == "12"
    assert settle("lease_months", "", "It's month to month.").value == "0"


def test_a_rented_listing_ends_dead_on_the_card():
    """Card status reads the outcome's words (orchestrator.state.outcome_status): gone must read gone."""
    c = call([("landlord", "Sorry, that one's been rented already.")],
             tool_facts=[ToolFact("available", "no", "that one's been rented already", 0)])
    assert outcome_status(build(c, []).outcome) == "dead"
    live = call([("landlord", "Yes, it's still available.")],
                tool_facts=[ToolFact("available", "yes", "it's still available", 0)])
    assert outcome_status(build(live, []).outcome) == "verified"


def test_mandarin_laundry_and_street_parking():
    assert settle("laundry", "basement, shared", "洗衣房在地下室，是共用的。").value == "in_building"
    assert settle("parking", "street only", "没有专属停车位，只有街边停车位。").value == "none"
    assert settle("parking", "", "Only street parking, sorry.").value == "none"


def test_utilities_split_on_but():
    assert settle("utilities_included", "", "Heat is included but hydro is extra.").value == "heat"


# A bare yes or no answers the Caller's question (#139)

PETS_Q, PARKING_Q = "Are pets allowed in the unit?", "Does parking come with it?"


@pytest.mark.parametrize(("question", "field", "value", "quote", "stored", "shown"), [
    (PETS_Q, "pets", "yes", "Yes.", "allowed", "pets allowed"),
    (PETS_Q, "pets", "no", "No.", "no_pets", "no pets"),
    (PETS_Q, "pets", "cats only", "Cats only.", "cats_only", "cats only"),
    (PETS_Q, "pets", "small dogs ok", "Small dogs are fine.", "small_only", "small pets only"),
    (PETS_Q, "pets", "case by case", "We decide case by case.", "case_by_case", "pets case by case"),
    (PARKING_Q, "parking", "yes", "Yes.", "included", "parking included"),
    (PARKING_Q, "parking", "no", "No.", "none", "no parking"),
    ("Are utilities included?", "utilities_included", "yes", "Yes, all of them.", "included",
     "utilities included"),
])
def test_a_bare_yes_or_no_answers_the_question_asked(question, field, value, quote, stored, shown):
    got = settle(field, value, quote, question=question)
    assert (got.value, got.display) == (stored, shown)


@pytest.mark.parametrize(("question", "quote"), [
    (None, "Yes."),                              # no question: nothing to answer
    ("Is it still available?", "Yes."),          # a yes to another question isn't about pets
    (PETS_Q, "No problem."),                     # not a no
])
def test_a_bare_yes_or_no_without_its_question_is_still_refused(question, quote):
    with pytest.raises(Refused):
        settle("pets", "yes", quote, question=question)


def test_the_last_of_several_answers_to_the_same_question_is_kept():
    turns = [("agent", "Is it still available?"), ("landlord", "Yes."),
             ("agent", PETS_Q), ("landlord", "Yes."), ("agent", PETS_Q), ("landlord", "Yes."),
             ("agent", PETS_Q), ("landlord", "No.")]
    facts = (ToolFact("available", "yes", "Yes."), ToolFact("pets", "yes", "Yes."),
             ToolFact("pets", "yes", "Yes."), ToolFact("pets", "no", "No."))
    out = build(call(turns, facts), []).outcome
    assert [(f.field, f.value) for f in out.facts] == [("available", "still available"), ("pets", "no pets")]
