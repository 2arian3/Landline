"""The Narrator (O-06): one template per kind of re-rank in the brief, and per kind of call outcome in the
results. The lines a tool reply speaks stay within 25 words."""
from datetime import UTC, datetime, timedelta

import pytest

from landline.contracts import (
    CallEnded,
    CallOutcome,
    CallPlan,
    CallsCompleted,
    CallsRequested,
    CallsScheduled,
    Card,
    Conflict,
    Fact,
    FeedbackGiven,
    Listing,
    OutcomeReady,
    PreferenceDelta,
    PreferencesUpdated,
    QuestionNoted,
    SessionEnded,
    SessionStarted,
    SessionView,
    Viewing,
)
from landline.db.catalogue import Catalogue
from landline.orchestrator import SessionRuntime
from landline.orchestrator.narrator import MAX_WORDS, WIDEN, acknowledge, rank_diff

T0 = datetime(2026, 10, 24, 1, 0, tzinfo=UTC)
SID = "s_narr000001"
SATURDAY_2PM = datetime(2026, 10, 24, 18, 0, tzinfo=UTC)   # 14:00 in Toronto


def _card(n: int, rank: int, address: str | None = None, unit: str | None = None) -> Card:
    return Card(listing_id=f"t:{n}", address=address or f"{n} Test St", unit_label=unit, rent=2000,
                monthly_cost=2000, beds=1, source="t", url="https://example.com", rank=rank)


def _view(*ids: int, matches: int | None = None) -> SessionView:
    board = [_card(n, r) for r, n in enumerate(ids, start=1)]
    fit = matches if matches is not None else len(ids)
    return SessionView(session_id=SID, version=1, board=board, matches=[f"t:{n}" for n in range(fit)])


# The brief: one template per kind of re-rank


@pytest.mark.parametrize(("before", "after", "said"), [
    # first board
    (_view(), _view(1, 2, matches=40), "40 places fit. 1 Test St is on top."),
    # nothing fits
    (_view(1, 2), _view(), WIDEN),
    # a new top, and the old one drops
    (_view(1, 2, 3), _view(2, 3, 1), "2 Test St moves to the top. 1 Test St drops to third."),
    # a new top, and the old one leaves the board
    (_view(1, 2, 3), _view(2, 3, 4), "2 Test St moves to the top. 1 Test St is off the list."),
    # the top holds and another place climbs
    (_view(1, 2, 3), _view(1, 3, 2), "1 Test St stays on top. 3 Test St moves up to second."),
    # the top holds and a new place joins
    (_view(1, 2, 3), _view(1, 4, 2), "1 Test St stays on top. 4 Test St joins at second."),
    # nothing moved
    (_view(1, 2, 3), _view(1, 2, 3), "The list stays the same."),
    # only the count changed
    (_view(1, 2, matches=90), _view(1, 2, matches=12), "12 places fit."),
    # the count changed and so did the top
    (_view(1, 2, matches=90), _view(2, 1, matches=12),
     "12 places fit. 2 Test St moves to the top. 1 Test St drops to second."),
])
def test_each_kind_of_re_rank_has_its_line(before, after, said):
    assert rank_diff(before, after) == said


def test_the_same_address_twice_on_the_board_names_the_unit():
    twins = [_card(1, 1, "140 D'Arcy Street", "5"), _card(2, 2, "140 D'Arcy Street", "7")]
    before = SessionView(session_id=SID, version=1, board=twins)
    after = SessionView(session_id=SID, version=2, board=[twins[1], twins[0]])
    assert rank_diff(before, after) == ("140 D'Arcy Street, unit 7 moves to the top. "
                                        "140 D'Arcy Street, unit 5 drops to second.")


def test_the_acknowledgement_names_the_first_two_changes():
    def said(**delta):
        return acknowledge([PreferencesUpdated(session_id=SID, at=T0, idem_key="k",
                                               delta=PreferenceDelta(**delta))])

    assert said(max_monthly=2600, priorities=["Light", "cost"]) == "Got it: up to $2,600, light first."
    assert said(min_beds=0, pets=["dog"]) == "Got it: studios are fine, with your dog."
    assert said(areas=["Annex", "Bloor West", "Leslieville"], anchors=[{"label": "King and Bay"}]) == (
        "Got it: around Annex, Bloor West and more, near King and Bay.")
    assert said(language="fa") is None   # nothing the renter would hear back


def test_a_priority_nothing_ranks_by_is_not_acknowledged():
    def said(**delta):
        return acknowledge([PreferencesUpdated(session_id=SID, at=T0, idem_key="k",
                                               delta=PreferenceDelta(**delta))])

    assert said(priorities=["buzzer"]) == "I can't rank by buzzer."
    assert said(max_monthly=2600, priorities=["Buzzer", "light"]) == (
        "Got it: up to $2,600, light first. I can't rank by buzzer.")


# The calls: one template per kind of outcome, through the runtime


def _listing(n: int, rent: int) -> Listing:
    return Listing(listing_id=f"t:{n}", source="t", source_id=str(n), url=f"https://example.com/{n}",
                   first_seen=T0, last_seen=T0, address=f"{n} Main St", lat=43.65, lng=-79.38, rent=rent,
                   monthly_cost=rent, beds=1, sqft=700)


def _runtime(*rents: int) -> SessionRuntime:
    return SessionRuntime(Catalogue([_listing(n, rent) for n, rent in enumerate(rents, start=1)], "test"))


def _at(s: int) -> datetime:
    return T0 + timedelta(seconds=s)


def _meta(key: str, s: int = 0) -> dict:
    return {"session_id": SID, "at": _at(s), "idem_key": f"{SID}:{key}"}


def _heard(field: str, value: str, quote: str = "said so") -> Fact:
    return Fact(field=field, value=value, quote=quote, speaker="Agent heard", at=T0)


async def _session(rt: SessionRuntime, plan: list[str]) -> list[str]:
    await rt.start(SessionStarted(**_meta("start"), channel="judge"))
    await rt.emit(CallsRequested(**_meta("plan", 1), listing_ids=plan, may_book=True))
    await rt.emit(SessionEnded(**_meta("end", 2), reason="hangup"))
    calls = [f"c_narr00000{i}" for i in range(1, len(plan) + 1)]
    frozen = CallPlan(session_id=SID, listing_ids=plan, may_book=True, created_at=_at(3))
    await rt.emit(CallsScheduled(**_meta("scheduled", 3), call_ids=calls, plan=frozen))
    return calls


async def _outcome(rt: SessionRuntime, call_id: str, lid: str, end_reason: str = "done", **kw) -> SessionView:
    await rt.emit(CallEnded(**_meta(f"{call_id}:end", 8), call_id=call_id, reason=end_reason))
    out = CallOutcome(call_id=call_id, listing_id=lid, end_reason=end_reason, **kw)
    return await rt.emit(OutcomeReady(**_meta(f"{call_id}:out", 9), outcome=out))


async def test_every_kind_of_outcome_has_its_sentence_and_bookings_come_first():
    rt = _runtime(2000, 2100, 2200, 2300, 2400, 2500, 2600)
    c1, c2, c3, c4, c5 = await _session(rt, ["t:1", "t:2", "t:3", "t:4", "t:5"])
    # t:1 tells facts and conflicts with its listing; t:2 is a red flag; t:3 is rented; t:4 never answers;
    # t:5 books a viewing, and its agent asked a question.
    facts = [_heard("parking_cost", "$180 a month."), _heard("pets", "dogs ok"), _heard("laundry", "shared")]
    conflict = Conflict(field="parking", listed="included", heard="$180 a month", quote="It's one eighty.")
    heard = await _outcome(rt, c1, "t:1", facts=facts, conflicts=[conflict])
    assert heard.agent_says == ("At 1 Main St, parking: $180 a month; pets: dogs ok. "
                                "The listing says parking included, but the agent said $180 a month.")
    await _outcome(rt, c2, "t:2", facts=[_heard("fees", "deposit first")],
                   red_flags=["Wants a deposit by e-transfer before anyone sees the unit."])
    await _outcome(rt, c3, "t:3", facts=[_heard("available", "no", "It's rented.")])
    await _outcome(rt, c4, "t:4", end_reason="no_answer")
    await rt.emit(QuestionNoted(**_meta("q", 7), call_id=c5, question="Would they sign a 12-month lease"))
    await _outcome(rt, c5, "t:5", facts=[_heard("pets", "dogs ok")],
                   viewing=Viewing(starts_at=SATURDAY_2PM, slot_text="Saturday at two"))
    done = await rt.emit(CallsCompleted(**_meta("done", 20), call_ids=[c1, c2, c3, c4, c5]))
    assert done.phase == "done" and done.agent_says == "All 5 calls are done. Here's what I found."
    assert done.results == (
        # a booking, where it moved, and the question its agent asked
        "I booked 5 Main St for Saturday at 2 pm. It moves up to first. "
        "Its agent asked, “Would they sign a 12-month lease?” I said I'd check with you. "
        # facts, a conflict with the listing, and where it moved
        "At 1 Main St, parking: $180 a month; pets: dogs ok. "
        "The listing says parking included, but the agent said $180 a month. It drops to second. "
        # a red flag, a rented place, no answer
        "2 Main St: wants a deposit by e-transfer before anyone sees the unit, so I'd skip it. "
        "3 Main St is already rented. "
        "Nobody picked up at 4 Main St."
    )
    assert rt.state(SID).results == done.results   # written once, for the SMS and the next call's opening


@pytest.mark.parametrize(("field", "listed", "heard", "says"), [
    # the values as the Auditor fills them (agents/auditor/audit.py conflicts())
    ("pets", "no pets", "cats only", "The listing says no pets, but the agent said cats only."),
    ("pets", "allowed", "no pets", "The listing says pets allowed, but the agent said no pets."),
    ("laundry", "in suite", "shared", "The listing says laundry in the unit, but the agent said shared."),
    ("parking", "none", "$100 a month", "The listing says no parking, but the agent said $100 a month."),
    ("rent", "$1,850", "$1,950", "The listing says rent is $1,850, but the agent said $1,950."),
    ("parking_cost", "$100", "$150", "The listing says parking is $100, but the agent said $150."),
    ("monthly_cost", "$2,000 a month", "$2,180 a month",
     "The listing says $2,000 a month, but the agent said $2,180 a month."),
    ("available", "listed as available", "not available",
     "The listing says it's available, but the agent said not available."),
])
async def test_a_conflict_reads_as_plain_words(field, listed, heard, says):
    rt = _runtime(2000)
    (c1,) = await _session(rt, ["t:1"])
    conflict = Conflict(field=field, listed=listed, heard=heard, quote="said so")
    view = await _outcome(rt, c1, "t:1", facts=[_heard(field, heard)], conflicts=[conflict])
    assert view.agent_says.endswith(says)


async def test_voicemail_and_a_call_without_an_outcome():
    rt = _runtime(2000, 2000, 2000)
    c1, c2 = await _session(rt, ["t:1", "t:2"])
    await _outcome(rt, c1, "t:1", end_reason="voicemail")
    done = await rt.emit(CallsCompleted(**_meta("done", 20), call_ids=[c1, c2]))
    assert done.results == "I left a message for 1 Main St's agent. I couldn't reach 2 Main St in time."


async def test_the_brief_speaks_short_lines_that_follow_the_board():
    rt = _runtime(2000, 2100, 2200, 2300, 2400, 2500, 2600)
    start = await rt.start(SessionStarted(**_meta("start"), channel="phone"))
    assert start.agent_says == "Tell me what you're looking for, and the map will follow."
    cheap = await rt.emit(PreferencesUpdated(**_meta("p1", 1),
                                             delta=PreferenceDelta(max_monthly=2200, min_beds=1)))
    # 2,200 plus the Matcher's 10% slack lets in five of the seven
    assert cheap.agent_says == ("Got it: up to $2,200, one bedroom or more. 5 places fit. "
                                "1 Main St stays on top.")
    disliked = await rt.emit(FeedbackGiven(**_meta("f1", 2), listing_id="t:1", reaction="dislike",
                                           reason="too dark", attribute="light"))
    assert disliked.agent_says == "The list stays the same."   # weights move with O-04
    planned = await rt.emit(CallsRequested(**_meta("plan", 3), listing_ids=["t:1", "t:2", "t:3"]))
    assert planned.agent_says == "I'll call 1 Main St, 2 Main St and 3 Main St after you hang up."
    calling = await rt.emit(SessionEnded(**_meta("end", 4), reason="hangup"))
    assert calling.agent_says == "Calling 3 places now. I'll text you what they say."
    for view in (start, cheap, disliked, planned, calling):
        assert len(view.agent_says.split()) <= MAX_WORDS


async def test_a_long_line_drops_whole_sentences_to_stay_speakable():
    rt = _runtime(2000, 2000, 2000)
    await rt.start(SessionStarted(**_meta("start"), channel="phone"))
    many = [f"Neighbourhood number {i} of the city" for i in range(6)]
    delta = PreferenceDelta(areas=many, max_monthly=3000)
    view = await rt.emit(PreferencesUpdated(**_meta("p1", 1), delta=delta))
    assert len(view.agent_says.split()) <= MAX_WORDS and view.agent_says.startswith("Got it: up to $3,000")


async def test_a_closed_brief_says_goodbye():
    rt = _runtime(2000)
    await rt.start(SessionStarted(**_meta("start"), channel="phone"))
    closed = await rt.emit(SessionEnded(**_meta("end", 1), reason="hangup"))
    assert (closed.phase, closed.agent_says) == ("closed", "Bye for now. Call again any time.")
    assert closed.results is None
