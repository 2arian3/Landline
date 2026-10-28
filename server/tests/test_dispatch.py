"""The Dispatcher (A-06) with fake calls: no phone, no Higgs. The four cases from BACKLOG.md, then the plan's
viewing windows, the allowlist, business hours and the deadline."""
import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from landline.agents.caller.brief import brief, instructions
from landline.config import Settings
from landline.contracts import (
    CallOutcome,
    CallsRequested,
    Listing,
    PreferenceDelta,
    PreferencesUpdated,
    SessionEnded,
    SessionStarted,
    Viewing,
)
from landline.contracts.tools import Availability
from landline.db.catalogue import Catalogue
from landline.orchestrator import SessionRuntime
from landline.voice.dispatch import (
    TORONTO,
    CallResult,
    Dial,
    Dispatcher,
    freeze,
    results_text,
    until_open,
    window,
)
from landline.voice.routing import ByChannel

# Saturday 24 Oct 2026, 11:00 in Toronto: inside business hours.
NOW = datetime(2026, 10, 24, 15, 0, tzinfo=UTC)
SID = "s_disp000001"
PHONE = "+14165550199"
AGENTS = ("+16475550101", "+16475550102")


def _settings(**kw) -> Settings:
    base = Settings(database_url=None, web_url="https://landline.example", transport="stub",
                    twilio_phone_number="+12895122338", demo_agent_phones=AGENTS, higgs_max_sessions=3,
                    listing_call_max_s=5)
    return replace(base, **kw)


def _runtime(n: int = 6) -> SessionRuntime:
    listings = [Listing(listing_id=f"t:{i}", source="t", source_id=str(i), url=f"https://example.com/{i}",
                        first_seen=NOW, last_seen=NOW, address=f"{i} Dial St", lat=43.65, lng=-79.38,
                        rent=2000 + i, monthly_cost=2000 + i, beds=1) for i in range(1, n + 1)]
    return SessionRuntime(Catalogue(listings, "test"))


class FakeTexter:
    ready = True

    def __init__(self):
        self.sent = []

    def send_soon(self, to, from_, body):
        self.sent.append((to, from_, body))


class FakeRunner:
    """Answers every call after a short wait, counting how many are on the line at once."""
    transport = "sim"

    def __init__(self, uses_phone=True, results=None, hold_s=0.02):
        self.uses_phone, self.results, self.hold_s = uses_phone, results or {}, hold_s
        self.dials: list[Dial] = []
        self.live = self.most = 0
        self.busy: set[str] = set()   # phones on a call right now
        self.rang_busy = False        # a phone rang while it was already on a call

    async def place(self, dial, report):
        self.dials.append(dial)
        if dial.to is not None:
            self.rang_busy |= dial.to in self.busy
            self.busy.add(dial.to)
        self.live += 1
        self.most = max(self.most, self.live)
        try:
            await report("ringing")
            what = self.results.get(dial.listing_id, "done")
            if what == "hang":
                await asyncio.sleep(60)   # never answers: the deadline ends it
            await asyncio.sleep(self.hold_s)
            if what == "fail":
                raise RuntimeError("the line dropped")
            if what == "no_answer":
                return CallResult("no_answer")
            await report("live")
            viewing = Viewing(starts_at=NOW + timedelta(days=1), slot_text="Sunday at eleven")
            booked = viewing if what == "book" else None
            return CallResult("done", CallOutcome(call_id=dial.call_id, listing_id=dial.listing_id,
                                                  end_reason="done", viewing=booked))
        finally:
            self.live -= 1
            self.busy.discard(dial.to)


async def _brief(rt: SessionRuntime, plan: list[str], channel: str = "phone", **request) -> None:
    await rt.start(SessionStarted(session_id=SID, at=NOW, idem_key=f"{SID}:start", channel=channel,
                                  caller_phone=PHONE if channel == "phone" else None))
    await rt.emit(CallsRequested(session_id=SID, at=NOW, idem_key=f"{SID}:plan", listing_ids=plan, **request))


async def _hang_up(rt: SessionRuntime, key: str = "end") -> None:
    await rt.emit(SessionEnded(session_id=SID, at=NOW, idem_key=f"{SID}:{key}", reason="hangup"))


def _dispatcher(rt, runner=None, texter=None, **settings) -> Dispatcher:
    return Dispatcher(rt, _settings(**settings), runner=runner, texter=texter, clock=lambda: NOW)


# The four cases in BACKLOG.md


async def test_nothing_dials_while_the_renter_is_still_talking():
    rt, runner = _runtime(), FakeRunner()
    d = _dispatcher(rt, runner)
    await _brief(rt, ["t:1", "t:2"])
    await d.idle()
    assert runner.dials == [] and rt.get(SID).calls == []   # a plan, but the brief is still open
    await _hang_up(rt)
    await d.idle()
    assert [x.listing_id for x in runner.dials] == ["t:1", "t:2"]


async def test_n_places_make_n_calls_and_never_more_than_the_cap_at_once():
    rt, runner = _runtime(), FakeRunner()
    d = _dispatcher(rt, runner)   # 3 Higgs lines, one kept for renters: 2 calls at once
    await _brief(rt, ["t:1", "t:2", "t:3", "t:4", "t:5"])
    await _hang_up(rt)
    await d.idle()
    assert d.cap == 2 and runner.most == 2
    assert sorted(x.listing_id for x in runner.dials) == ["t:1", "t:2", "t:3", "t:4", "t:5"]
    assert {x.to for x in runner.dials} <= set(AGENTS)            # only allowlisted numbers
    view = rt.get(SID)
    assert view.phase == "done" and [c.state for c in view.calls] == ["ended"] * 5


async def test_a_no_answer_and_a_timeout_still_complete_and_text_once():
    rt, texter = _runtime(), FakeTexter()
    runner = FakeRunner(results={"t:1": "book", "t:2": "no_answer", "t:3": "hang"})
    d = _dispatcher(rt, runner, texter, listing_call_max_s=0.1)
    sunday = Availability(day="sunday", from_="10:00", to="16:00")
    await _brief(rt, ["t:1", "t:2", "t:3"], availability=[sunday], may_book=True)
    await _hang_up(rt)
    await d.idle()
    state = rt.state(SID)
    assert rt.get(SID).phase == "done"
    assert {lid: o.end_reason for lid, o in state.outcomes.items()} == {"t:1": "done", "t:2": "no_answer",
                                                                       "t:3": "timeout"}
    [(to, sender, body)] = texter.sent   # exactly one text, to the caller, from Landline's number
    assert (to, sender) == (PHONE, "+12895122338")
    assert body.startswith("Landline: I booked 1 Dial St for Sunday at") and body.endswith(
        f"Map: https://landline.example/s/{SID}")


async def test_a_duplicate_hang_up_schedules_nothing_twice():
    rt, runner = _runtime(), FakeRunner()
    d = _dispatcher(rt, runner)
    await _brief(rt, ["t:1", "t:2"])
    await _hang_up(rt)
    await d.idle()
    view = rt.get(SID)
    await d.on_session_ended(SessionEnded(session_id=SID, at=NOW, idem_key=f"{SID}:again", reason="closed"),
                             view)   # as if the event came again
    await _hang_up(rt, "end-retry")
    await d.idle()
    assert len(runner.dials) == 2 and len(rt.get(SID).calls) == 2


# The rest


async def test_the_plan_is_frozen_with_toronto_windows_and_only_shareable_answers():
    rt = _runtime()
    d = _dispatcher(rt)   # no runner: the plan is still frozen, then its calls end blocked
    await _brief(rt, ["t:2", "t:1"], extra_questions=["Is there a dishwasher?"], may_book=True,
                 availability=[Availability(day="saturday", from_="13:00", to="17:00"),
                               Availability(day="saturday", from_="09:00", to="10:00")])
    await _hang_up(rt)
    await d.idle()
    state = rt.state(SID)
    plan = state.plan
    assert (plan.listing_ids, plan.questions, plan.may_book) == (
        ["t:2", "t:1"], ["Is there a dishwasher?"], True)
    # Saturday 13:00–17:00 is later today; 09:00–10:00 has passed, so it means next Saturday.
    starts = [(w.start.astimezone(TORONTO).strftime("%a %d %H:%M")) for w in plan.windows]
    assert starts == ["Sat 24 13:00", "Sat 31 09:00"]
    assert plan.shareable.model_dump() == {"name": None, "move_in": None, "occupants": None, "pets": []}
    assert [c.state for c in state.calls.values()] == ["ended", "ended"]


# What the renter shares (user_profiles.shareable, ticked on /me): the Dispatcher looks it up, freeze() masks.


async def _answered(rt: SessionRuntime):
    """A briefed session: the renter said when they move in, how many they are and that they have a dog."""
    await _brief(rt, ["t:1"])
    await rt.emit(PreferencesUpdated(session_id=SID, at=NOW, idem_key=f"{SID}:prefs", delta=PreferenceDelta(
        move_in="December 1", occupants=2, pets=["dog"])))
    return rt.state(SID)


async def test_by_default_the_renter_shares_move_in_occupants_and_pets_and_never_their_name():
    rt = _runtime()
    state = await _answered(rt)
    shared = {"name": None, "move_in": "December 1", "occupants": 2, "pets": ["dog"]}
    assert freeze(state, NOW).shareable.model_dump() == shared
    assert freeze(state, NOW, name="Sara").shareable.model_dump() == shared   # a known name isn't ticked
    allowed, name = await _dispatcher(rt)._shareable(state)   # no database: the default
    assert (allowed, name) == ({"move_in", "occupants", "pets"}, None)


async def test_unticked_pets_never_reach_the_caller():
    rt = _runtime()
    state = await _answered(rt)
    plan = freeze(state, NOW, allowed={"move_in", "occupants"})
    assert plan.shareable.model_dump() == {"name": None, "move_in": "December 1", "occupants": 2, "pets": []}
    said = instructions(brief(rt.catalogue.by_id["t:1"], plan, now=NOW))
    assert "dog" not in said and "pets" not in said   # not told to the landlord, and not asked


async def test_a_ticked_name_is_shared_only_when_it_is_known():
    rt = _runtime()
    state = await _answered(rt)
    assert freeze(state, NOW, allowed={"name"}).shareable.model_dump() == {
        "name": None, "move_in": None, "occupants": None, "pets": []}
    plan = freeze(state, NOW, allowed={"name", "pets"}, name="Sara")
    assert plan.shareable.model_dump() == {
        "name": "Sara", "move_in": None, "occupants": None, "pets": ["dog"]}
    said = instructions(brief(rt.catalogue.by_id["t:1"], plan, now=NOW))
    assert "Their name is Sara and they have a dog." in said


async def test_if_what_the_renter_shares_cant_be_read_nothing_is_shared():
    class Down:
        async def by_phone(self, phone):
            raise ConnectionError("the database is down")

    rt = _runtime()
    state = await _answered(rt)
    rt.db = type("Db", (), {"users": Down()})()   # only the lookup sees it; the session is already stored
    assert await _dispatcher(rt)._shareable(state) == (frozenset(), None)


async def test_a_phone_session_with_no_caller_ends_its_calls_and_never_says_they_are_running():
    # Production with TRANSPORT=stub: a phone renter's session gets no runner (voice/routing.py).
    rt, texter, sim = _runtime(), FakeTexter(), FakeRunner(uses_phone=False)
    d = _dispatcher(rt, ByChannel(rt, phone=None, sim=sim), texter)
    await _brief(rt, ["t:1", "t:2"])
    await _hang_up(rt)
    await d.idle()
    state, view = rt.state(SID), rt.get(SID)
    assert sim.dials == [] and view.phase == "done"   # not left in `calling`
    assert {lid: o.end_reason for lid, o in state.outcomes.items()} == {"t:1": "blocked", "t:2": "blocked"}
    assert view.results == "I couldn't place the call to 1 Dial St. I couldn't place the call to 2 Dial St."
    assert "now" not in view.agent_says   # not "Calling 2 places now"
    [(to, _, body)] = texter.sent   # the text the renter was promised says no call was made
    assert to == PHONE and body.startswith("Landline: I couldn't place the call to 1 Dial St.")


async def test_a_number_off_the_allowlist_is_never_dialled():
    rt, runner = _runtime(), FakeRunner()
    d = _dispatcher(rt, runner, demo_agent_phones=())   # nothing allowlisted
    await _brief(rt, ["t:1"])
    await _hang_up(rt)
    await d.idle()
    assert runner.dials == [] and rt.state(SID).outcomes["t:1"].end_reason == "blocked"
    assert rt.get(SID).phase == "done" and rt.get(SID).results == "I couldn't place the call to 1 Dial St."


# A teammate's phone rings for one listing call at a time


async def test_three_calls_on_two_phones_never_ring_a_busy_phone():
    rt, runner = _runtime(), FakeRunner()
    d = _dispatcher(rt, runner, call_concurrency=3)   # three lines, but two phones
    await _brief(rt, ["t:1", "t:2", "t:3"])
    await _hang_up(rt)
    await d.idle()
    assert len(runner.dials) == 3 and not runner.rang_busy and runner.most == 2
    first_two = {x.to for x in runner.dials[:2]}
    assert first_two == set(AGENTS) and runner.dials[2].to in first_two   # the third waited for a free one
    assert rt.get(SID).phase == "done"


async def test_a_failed_call_gives_its_phone_back():
    rt, runner = _runtime(), FakeRunner(results={"t:1": "fail"})
    d = _dispatcher(rt, runner, demo_agent_phones=AGENTS[:1])   # one phone
    await _brief(rt, ["t:1", "t:2"])
    await _hang_up(rt)
    await d.idle()
    assert [x.to for x in runner.dials] == [AGENTS[0]] * 2 and not runner.rang_busy
    outcomes = rt.state(SID).outcomes
    assert {lid: o.end_reason for lid, o in outcomes.items()} == {"t:1": "failed", "t:2": "done"}


async def test_sim_calls_take_no_phone():
    rt, runner = _runtime(), FakeRunner(uses_phone=False)
    d = _dispatcher(rt, runner, call_concurrency=3, demo_agent_phones=())
    await _brief(rt, ["t:1", "t:2", "t:3"])
    await _hang_up(rt)
    await d.idle()
    assert [x.to for x in runner.dials] == [None] * 3 and runner.most == 3


async def test_phone_calls_wait_for_business_hours_and_sim_calls_do_not():
    late = datetime(2026, 10, 24, 3, 30, tzinfo=UTC)   # 23:30 Friday in Toronto
    assert until_open(late) == 9.5 * 3600 and until_open(NOW) == 0
    rt, runner, slept = _runtime(), FakeRunner(), []
    now = [late]

    async def sleep(s):
        slept.append(s)
        now[0] += timedelta(seconds=s)

    d = Dispatcher(rt, _settings(), runner=runner, clock=lambda: now[0], sleep=sleep)
    await _brief(rt, ["t:1"])
    await _hang_up(rt)
    await d.idle()
    assert slept == [9.5 * 3600] and len(runner.dials) == 1   # it waited for 9:00, then dialled
    # A sim call (no phone network) runs at any hour.
    rt2, sim = _runtime(), FakeRunner(uses_phone=False)
    d2 = Dispatcher(rt2, _settings(), runner=sim, clock=lambda: late, sleep=sleep)
    await _brief(rt2, ["t:1"], channel="judge")
    await _hang_up(rt2)
    await d2.idle()
    assert len(sim.dials) == 1 and sim.dials[0].to is None and slept == [9.5 * 3600]


async def test_a_judge_session_is_never_texted():
    rt, texter = _runtime(), FakeTexter()
    d = _dispatcher(rt, FakeRunner(uses_phone=False), texter)
    await _brief(rt, ["t:1"], channel="judge")
    await _hang_up(rt)
    await d.idle()
    assert rt.get(SID).phase == "done" and texter.sent == []


@pytest.mark.parametrize(("day", "start"), [("today", "Sat 24 18:00"), ("tomorrow", "Sun 25 10:00"),
                                            ("monday", "Mon 26 10:00")])
def test_viewing_days_become_toronto_windows(day, start):
    w = window(Availability(day=day, from_="18:00" if day == "today" else "10:00", to="19:00"), NOW)
    assert w.start.astimezone(TORONTO).strftime("%a %d %H:%M") == start


def test_a_window_that_cannot_happen_is_dropped():
    assert window(Availability(day="today", from_="08:00", to="09:00"), NOW) is None   # already over
    assert window(Availability(day="monday", from_="17:00", to="13:00"), NOW) is None  # ends before it starts


def test_a_long_result_text_still_ends_with_the_link():
    body = results_text("word " * 600, "https://landline.example/s/x")
    assert len(body) <= 1500 and body.endswith("Map: https://landline.example/s/x")
