"""listing_status (A-14): "What happened with Markham?", answered by code from what this session's calls
stored. A phone caller's earlier sessions are never read (the fake database here records any read). What
it says is checked on rows shaped like the database's, through the same builder (api/me.py `listings`).
Every reply is spoken: at most 25 words."""
import asyncio
import json
from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest

from landline.agents.concierge import status, tools
from landline.api.me import HeardFact, MyListing, listings
from landline.contracts import (
    CallOutcome,
    CallPlan,
    CallsRequested,
    CallsScheduled,
    CallStateChanged,
    Card,
    Fact,
    Listing,
    OutcomeReady,
    SessionEnded,
    SessionStarted,
)
from landline.db.catalogue import Catalogue
from landline.orchestrator import SessionRuntime
from landline.orchestrator.memory import Known

T0 = datetime(2026, 10, 27, 20, 0, tzinfo=UTC)   # Tuesday, 4 pm in Toronto: the earlier session
NOW = datetime(2026, 10, 29, 22, 0, tzinfo=UTC)  # Thursday: the second call
PHONE = "+12015550100"
SID = "s_status0001"


def _listing(i: int, address: str) -> Listing:
    return Listing(listing_id=f"t:{i}", source="t", source_id=str(i), url=f"https://example.com/{i}",
                   first_seen=T0, last_seen=T0, address=address, lat=43.66, lng=-79.40 - i / 100,
                   rent=2500, monthly_cost=2500, beds=1, sqft=600)


ADDRESSES = ["112 Markham St", "88 Erskine Ave", "322 Dupont St", "40 Bloor St W", "15 Leslie St"]
CATALOGUE = Catalogue([_listing(i, address) for i, address in enumerate(ADDRESSES, start=1)], "test")


def _fact(field: str, value: str, quote: str) -> dict:
    return {"field": field, "value": value, "quote": quote, "speaker": "Agent heard",
            "at": "2026-10-27T20:05:00Z"}


EARLIER = {
    "state": {"outcomes": {
        "t:1": {"call_id": "c_1", "listing_id": "t:1", "end_reason": "done",
                "facts": [_fact("parking_cost", "$150 a month", "It's 150 a month for parking."),
                          _fact("pets", "dogs allowed", "Dogs are fine.")],
                "viewing": {"starts_at": "2026-10-31T18:00:00Z", "slot_text": "Saturday at two"}},
        "t:2": {"call_id": "c_2", "listing_id": "t:2", "end_reason": "voicemail", "facts": []},
        "t:4": {"call_id": "c_4", "listing_id": "t:4", "end_reason": "done",
                "facts": [_fact("outdoor_space", "a balcony", "There's a balcony off the bedroom."),
                          _fact("laundry", "in the unit", "Washer and dryer in the unit."),
                          _fact("parking_cost", "$200 a month", "Parking's two hundred."),
                          _fact("pets", "cats only", "Cats only, sorry."),
                          _fact("utilities_included", "heat and water", "Heat and water are in."),
                          _fact("lease_months", "12 months", "It's a one year lease.")]},
    }},
    "view": {"board": [{"listing_id": "t:1", "viewing": {"status": "confirmed"}}]},
}


def _row(cid: str, lid: str, **kw) -> dict:
    return {"call_id": cid, "session_id": "s_earlier01", "role": "listing", "listing_id": lid,
            "state": "ended", "end_reason": "done", "created_at": T0, "answered_at": None, **kw}


CALLS = [_row("c_1", "t:1"), _row("c_2", "t:2", end_reason="voicemail", state="voicemail"),
         _row("c_4", "t:4")]


class History:
    """The three reads listing_status makes: the user by the call's number, their sessions, their calls."""

    def __init__(self, fail: bool = False) -> None:
        self.asked: list[str] = []
        self.fail = fail
        self.users = SimpleNamespace(by_phone=self._user)
        self.admin = SimpleNamespace(sessions=self._sessions, calls=self._calls)

    async def _user(self, phone: str):
        self.asked.append(phone)
        if self.fail:
            raise ConnectionError("database down")
        return SimpleNamespace(user_id=7) if phone == PHONE else None

    async def _sessions(self, user_id: int):
        return [{"session_id": "s_earlier01", "snapshot": EARLIER}, {"session_id": SID, "snapshot": {}}]

    async def _calls(self, session_ids: list[str]):
        assert SID not in session_ids   # this session is read from the runtime, not twice
        return [c for c in CALLS if c["session_id"] in session_ids]


class Memory:
    """O-09b's shared memory: what other calls heard, from a read that's done, or never finishes."""

    def __init__(self, known: list[Known] | None) -> None:
        self.known = known

    def prefetch(self, listing_id: str):
        read = asyncio.get_running_loop().create_future()
        if self.known is not None:
            read.set_result(self.known)
        return read


async def _ctx(channel: str, history: History | None = None, texts: list[str] | None = None):
    runtime = SessionRuntime(CATALOGUE)
    await runtime.start(SessionStarted(session_id=SID, at=NOW, idem_key=f"{SID}:started", channel=channel,
                                       caller_phone=PHONE if channel == "phone" else None))
    runtime.db = history
    return tools.ToolContext(session_id=SID, call_id="c_renter001", runtime=runtime,
                             text=texts.append if texts is not None else None)


@pytest.fixture
async def phone():
    texts: list[str] = []
    ctx = await _ctx("phone", History(), texts)
    ctx.texts = texts
    yield ctx
    ctx.runtime.db = None
    await ctx.runtime.close()


async def _ask(ctx, ref: str, text_rest: bool = False) -> str:
    args = {"listing_ref": ref, **({"text_rest": True} if text_rest else {})}
    reply = await tools.handle("listing_status", json.dumps(args), "fc_1", ctx)
    assert len(reply.split()) <= 25, reply
    return reply


@pytest.fixture(autouse=True)
def _thursday(monkeypatch):
    real = tools.datetime

    class Clock(real):
        @classmethod
        def now(cls, tz=None):
            return NOW if tz is None or tz == UTC else NOW.astimezone(tz)
    monkeypatch.setattr(tools, "datetime", Clock)


PLACE = status._place_of(CATALOGUE)


def _found(ref: str, view):
    """What resolve finds among the stored calls in EARLIER and CALLS, as if this session had made them."""
    return status.resolve(ref, view, listings([{"session_id": "s_earlier01", "snapshot": EARLIER}], CALLS,
                                              PLACE), PLACE)


def _said(ref: str, view) -> str:
    found = _found(ref, view)
    said = found if isinstance(found, str) else status.spoken(found, NOW, can_text=True)
    assert len(said.split()) <= 25, said
    return said


async def test_a_phone_callers_earlier_sessions_are_never_read(phone):
    """The Concierge knows nothing about a renter from earlier calls: their sessions aren't read, so a
    place this call hasn't got anything on says so."""
    assert await _ask(phone, "Markham") == "112 Markham St: Landline hasn't called it for you yet."
    assert await _ask(phone, "Spadina") == status.NOT_FOUND == "I don't have anything on that one yet."
    assert await _ask(phone, "my viewing") == status.NOT_FOUND
    assert phone.runtime.db.asked == []


def test_a_listings_facts_and_viewing_in_one_reply(phone):
    assert _said("Markham", _view(phone)) == ("112 Markham St: Viewing Saturday at 2 pm, confirmed. On "
                                              "Tuesday the agent said parking: $150 a month; pets: dogs "
                                              "allowed.")


def test_a_call_that_went_to_voicemail_says_nothing_was_heard(phone):
    assert _said("what happened with Erskine", _view(phone)) == \
        "88 Erskine Ave: On Tuesday the call went to voicemail, so nothing was heard yet."


async def test_a_place_none_of_their_calls_reached(phone):
    assert _said("Spadina", _view(phone)) == status.NOT_FOUND
    on_the_board = await _ask(phone, "Dupont")
    assert on_the_board == "322 Dupont St: Landline hasn't called it for you yet."


def test_words_about_the_place_and_my_viewing_find_it(phone):
    balcony = _said("the one with the balcony", _view(phone))
    assert balcony.startswith("40 Bloor St W: On Tuesday the agent said outdoor space: a balcony;")
    viewing = _said("my viewing", _view(phone))
    assert viewing.startswith("112 Markham St: Viewing Saturday at 2 pm, confirmed.")


def test_more_than_fits_is_offered_by_text_with_every_quote(phone):
    said = _said("Bloor", _view(phone))
    assert said.endswith(status.OFFER) and "lease" not in said
    sent = status.texted(_found("Bloor", _view(phone)), NOW)
    assert sent.startswith("Landline: what our calls heard about 40 Bloor St W.")
    for quote in ("There's a balcony off the bedroom.", "Cats only, sorry.", "It's a one year lease."):
        assert f"“{quote}”" in sent   # every fact with the agent's own words


async def test_text_rest_sends_to_the_callers_own_number(phone):
    assert phone.texts == []   # nothing is sent until they say yes
    sent_it = await _ask(phone, "Dupont", text_rest=True)
    assert sent_it == "Texted you everything Landline heard about 322 Dupont St."
    assert [t.splitlines()[0] for t in phone.texts] == ["Landline: what our calls heard about 322 Dupont St."]


def _view(ctx):
    return ctx.runtime.get(SID)


async def test_a_judge_session_answers_from_itself_only():
    history = History()
    ctx = await _ctx("judge")
    rt = ctx.runtime
    meta = {"session_id": SID, "at": NOW}
    await rt.emit(CallsRequested(**meta, idem_key="req", listing_ids=["t:3"]))
    await rt.emit(SessionEnded(**meta, idem_key="end", reason="hangup"))
    plan = CallPlan(session_id=SID, listing_ids=["t:3"], created_at=NOW)
    await rt.emit(CallsScheduled(**meta, idem_key="sched", plan=plan, call_ids=["c_3"]))
    await rt.emit(CallStateChanged(**meta, idem_key="live", call_id="c_3", state="live"))
    fact = Fact(field="laundry", value="in the building", quote="Laundry's in the basement.",
                speaker="Agent heard", at=NOW)
    await rt.emit(OutcomeReady(**meta, idem_key="out", outcome=CallOutcome(call_id="c_3", listing_id="t:3",
                                                                           end_reason="done", facts=[fact])))
    rt.db = history
    try:
        assert await _ask(ctx, "Dupont") == "322 Dupont St: Today the agent said laundry: in the building."
        assert await _ask(ctx, "Markham") == "112 Markham St: Landline hasn't called it for you yet."
        assert await _ask(ctx, "Erskine", text_rest=True) == status.NO_TEXT   # no number to text
        assert history.asked == []   # no phone number: earlier sessions are never read
    finally:
        rt.db = None
        await rt.close()


async def test_a_database_that_fails_leaves_this_session_to_answer():
    ctx = await _ctx("phone", History(fail=True))
    try:
        assert await _ask(ctx, "Markham") == "112 Markham St: Landline hasn't called it for you yet."
    finally:
        ctx.runtime.db = None
        await ctx.runtime.close()


async def test_other_calls_add_what_this_renter_didnt_hear_with_when_and_never_who(phone, monkeypatch):
    monday = date(2026, 10, 26)
    laundry = Known(field="laundry", value="in the building", quote="Coin laundry downstairs.", day=monday)
    parking = Known(field="parking_cost", value="$90 a month", quote="Ninety for parking.", day=monday)
    phone.runtime.memory = Memory([laundry])   # listing memory (O-09b), not the renter's earlier calls
    said = await _ask(phone, "Erskine")
    assert said == ("88 Erskine Ave: Landline hasn't called it for you yet. "
                    "Another Landline call on Monday heard laundry: in the building.")
    phone.runtime.memory = Memory([laundry, parking])
    found = _found("Markham", _view(phone))   # it heard parking itself, so theirs stands; the rest is by text
    found.others = await status._others(phone.runtime, found)
    said = status.spoken(found, NOW, can_text=True)
    assert "$90" not in said and said.endswith(status.OFFER)
    sent = status.texted(found, NOW)
    assert "on Monday heard: laundry: in the building (“Coin laundry downstairs.”)." in sent
    assert "Ninety" not in sent
    monkeypatch.setattr(status, "OTHERS_WAIT_S", 0.01)
    phone.runtime.memory = Memory(None)   # a read still going: the reply doesn't wait for it
    assert await _ask(phone, "Erskine") == "88 Erskine Ave: Landline hasn't called it for you yet."


@pytest.mark.parametrize("ref", ["Markham", "Erskine", "Bloor", "the one with the balcony", "my viewing", "2",
                                 "Spadina", "Dupont"])
async def test_every_reply_is_25_words_or_fewer(phone, ref):
    phone.runtime.memory = Memory([Known(field="ac", value="central air in every room of the unit",
                                         quote="Central air everywhere.", day=date(2026, 10, 28))])
    await _ask(phone, ref)   # _ask checks the count


async def test_the_eval_cases_earlier_calls_are_never_told():
    """The Concierge eval's returning caller (evals/fixtures/concierge/returning-asks-what-happened.json),
    offline: the earlier calls the driver seeds are never read, so nothing about them is said."""
    from evals.sim.cases import FIXTURES, load
    from evals.sim.driver import SIM_PHONE, EarlierCalls
    from landline.db.catalogue import _from_fixture

    case = load(FIXTURES / "concierge" / "returning-asks-what-happened.json")
    runtime = SessionRuntime(Catalogue(_from_fixture(FIXTURES / "listings.json"), "eval"))
    await runtime.start(SessionStarted(session_id=SID, at=NOW, idem_key=f"{SID}:started", channel="phone",
                                       caller_phone=SIM_PHONE))
    ctx = tools.ToolContext(session_id=SID, call_id="c_renter001", runtime=runtime,
                            history=EarlierCalls(case.returning.calls, NOW))
    try:
        for ref in ("the place on Logan", "Danforth", "my viewing"):
            said = await _ask(ctx, ref)
            assert "Viewing" not in said and "agent said" not in said and "voicemail" not in said, said
    finally:
        await runtime.close()


def test_a_rank_is_the_boards_card_even_when_a_called_place_heard_the_same_word():
    """"The second one" is card 2, not the called place whose agent said "second floor"."""
    view = SimpleNamespace(board=[_card(2, 1, "88 Erskine Ave"), _card(3, 2, "322 Dupont St")],
                           focus_listing_id=None)
    fact = HeardFact(field="floor", value="second floor, bay window", quote="Second floor, bay window.")
    heard = MyListing(listing_id="t:4", address="40 Bloor St W", session_id="s_e", call_id="c_4",
                      called_at=T0, ended="talked", facts=[fact])
    place = status._place_of(CATALOGUE)
    for ref in ("the second one", "2"):
        assert status.resolve(ref, view, [heard], place).listing_id == "t:3", ref
    assert status.resolve("the one with the bay window", view, [heard], place).listing_id == "t:4"


def _card(i: int, rank: int, address: str) -> Card:
    return Card(listing_id=f"t:{i}", address=address, rent=2500, monthly_cost=2500, beds=1, source="t",
                url="https://example.com", rank=rank)
