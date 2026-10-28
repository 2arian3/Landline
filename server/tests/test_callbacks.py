"""A listing agent calling back (A-13b): which calls count as missed, how the agent's choice is matched in
code, the Concierge whenever the lookup fails or is slow, and the Dispatcher answering a callback as a new
call in the renter's session. The routing through the Twilio webhooks is in test_outbound.py; the lookup on
Postgres is at the end (skipped without TEST_DATABASE_URL)."""
import asyncio
import logging
import os
from datetime import UTC, datetime, timedelta

import pytest

from landline.agents.caller.brief import CALLED_BACK, brief, disclosure, instructions
from landline.config import Settings
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
from landline.db.pool import Database
from landline.db.rows import CallRow
from landline.orchestrator import SessionRuntime
from landline.voice import callbacks
from landline.voice.callbacks import Missed, find, lookup, pick
from landline.voice.dispatch import CallResult, Dispatcher

NOW = datetime(2026, 10, 28, 19, 0, tzinfo=UTC)
SID = "s_back000001"
AGENT = "+16475550101"
RENTER = "+14165550100"
ADDRESSES = {"t:1": "412 Markham St", "t:2": "88 Bloor St W", "t:3": "12 Dial St"}


def _catalogue() -> Catalogue:
    return Catalogue([Listing(listing_id=lid, source="t", source_id=lid[2:], url=f"https://example.com/{lid}",
                              first_seen=NOW, last_seen=NOW, address=address, lat=43.65, lng=-79.38,
                              rent=2000, monthly_cost=2000, beds=1) for lid, address in ADDRESSES.items()],
                     "test")


async def _done(rt: SessionRuntime, sid: str = SID, listing_ids=("t:1", "t:2")) -> list[str]:
    """A phone renter's session whose listing calls all reached a voicemail."""
    await rt.start(SessionStarted(session_id=sid, at=NOW, idem_key=f"{sid}:start", channel="phone",
                                  caller_phone=RENTER))
    await rt.emit(CallsRequested(session_id=sid, at=NOW, idem_key=f"{sid}:plan",
                                 listing_ids=list(listing_ids), may_book=True))
    await rt.emit(SessionEnded(session_id=sid, at=NOW, idem_key=f"{sid}:end", reason="hangup"))
    call_ids = [f"c_{sid[2:]}{n}" for n in range(len(listing_ids))]
    plan = CallPlan(session_id=sid, listing_ids=list(listing_ids), may_book=True, created_at=NOW)
    await rt.emit(CallsScheduled(session_id=sid, at=NOW, idem_key=f"{sid}:scheduled", plan=plan,
                                 call_ids=call_ids))
    for cid, lid in zip(call_ids, listing_ids, strict=True):
        await rt.emit(CallEnded(session_id=sid, at=NOW, idem_key=f"{cid}:ended", call_id=cid,
                                reason="voicemail"))
        await rt.emit(OutcomeReady(session_id=sid, at=NOW, idem_key=f"{cid}:outcome",
                                   outcome=CallOutcome(call_id=cid, listing_id=lid, end_reason="voicemail")))
    await rt.emit(CallsCompleted(session_id=sid, at=NOW, idem_key=f"{sid}:completed", call_ids=call_ids))
    return call_ids


def _row(call_id: str, listing_id: str, end_reason: str | None, session_id: str = SID, *,
         answered: bool = False, to: str = AGENT) -> CallRow:
    return CallRow(call_id=call_id, session_id=session_id, listing_id=listing_id, role="listing",
                   transport="voice", twilio_sid=None, state="ended" if end_reason else "live",
                   end_reason=end_reason, created_at=NOW, answered_at=NOW if answered else None,
                   ended_at=NOW if end_reason else None, deadline_at=None, attempt=1, to_e164=to)


class FakeCalls:
    """calls.latest_to, as Postgres answers it: the latest call per session and listing, newest first."""

    def __init__(self, rows=(), fail: Exception | None = None, slow_s: float = 0):
        self.rows, self.fail, self.slow_s = list(rows), fail, slow_s
        self.asked: list[tuple[str, datetime]] = []

    async def latest_to(self, phone, since):
        self.asked.append((phone, since))
        if self.slow_s:
            await asyncio.sleep(self.slow_s)
        if self.fail:
            raise self.fail
        return self.rows


def _with_calls(rt: SessionRuntime, calls: FakeCalls) -> SessionRuntime:
    rt.db = type("Db", (), {"calls": calls})()   # only the lookup reads it; the sessions are in memory
    return rt


def _choices() -> list[Missed]:
    return [Missed(f"c_{n}", SID, lid, ADDRESSES[lid]) for n, lid in enumerate(("t:1", "t:2", "t:3"))]


# Which calls count


async def test_a_call_that_reached_no_one_makes_its_number_a_callback_for_that_listing():
    rt = SessionRuntime(_catalogue())
    c1, c2 = await _done(rt)
    calls = FakeCalls([_row(c1, "t:1", "voicemail")])
    found = await lookup(_with_calls(rt, calls), "(647) 555-0101", NOW)
    assert found == [Missed(c1, SID, "t:1", "412 Markham St")]
    assert calls.asked == [(AGENT, NOW - timedelta(days=2))]   # by E.164, over the last two days


@pytest.mark.parametrize("reason", ["voicemail", "no_answer", "busy"])
async def test_voicemail_no_answer_and_busy_all_count_as_missed(reason):
    rt = SessionRuntime(_catalogue())
    c1, _ = await _done(rt)
    assert [m.listing_id for m in await lookup(_with_calls(rt, FakeCalls([_row(c1, "t:1", reason)])), AGENT,
                                               NOW)] == ["t:1"]


@pytest.mark.parametrize("reason", ["done", "hangup", "refused", "wrong_number", "failed", "timeout", None])
async def test_a_number_whose_latest_call_reached_someone_or_is_still_on_is_not_a_callback(reason):
    rt = SessionRuntime(_catalogue())
    c1, _ = await _done(rt)
    reached = _row(c1, "t:1", reason, answered=True)
    assert await lookup(_with_calls(rt, FakeCalls([reached])), AGENT, NOW) == []


async def test_a_callback_that_dropped_before_anyone_spoke_still_leaves_the_listing_missed():
    rt = SessionRuntime(_catalogue())
    await _done(rt)
    dropped = _row("c_dropped001", "t:1", "failed")   # never answered: its stream never came
    assert [m.listing_id for m in await lookup(_with_calls(rt, FakeCalls([dropped])), AGENT, NOW)] == ["t:1"]


async def test_a_renter_calling_about_their_own_sessions_calls_is_the_renter():
    rt = SessionRuntime(_catalogue())
    c1, _ = await _done(rt)   # the renter is RENTER; in a rehearsal their phone was dialled as an agent too
    rung = _row(c1, "t:1", "no_answer", to=RENTER)
    assert await lookup(_with_calls(rt, FakeCalls([rung])), RENTER, NOW) == []


async def test_an_unknown_number_a_renter_and_a_session_gone_with_a_restart_are_not_callbacks():
    rt = SessionRuntime(_catalogue())
    c1, _ = await _done(rt)
    assert await lookup(_with_calls(rt, FakeCalls([])), "+14165559999", NOW) == []   # nothing to it
    assert await lookup(_with_calls(rt, FakeCalls([])), None, NOW) == []
    gone = _row("c_old0000001", "t:1", "voicemail", session_id="s_gone000001")   # not in this runtime
    outside = _row(c1, "t:3", "voicemail")                                       # not in the session's plan
    assert await lookup(_with_calls(rt, FakeCalls([gone, outside])), AGENT, NOW) == []
    assert await lookup(SessionRuntime(_catalogue()), AGENT, NOW) == []          # no database: never one


async def test_several_missed_listings_come_newest_first_and_at_most_max_choices():
    rt = SessionRuntime(_catalogue())
    c1, c2 = await _done(rt)
    rows = [_row(c2, "t:2", "no_answer"), _row(c1, "t:1", "voicemail")]
    found = await lookup(_with_calls(rt, FakeCalls(rows)), AGENT, NOW)
    assert [m.listing_id for m in found] == ["t:2", "t:1"]
    many = [_row(c1, "t:1", "voicemail")] * (callbacks.MAX_CHOICES + 2)
    assert len(await lookup(_with_calls(rt, FakeCalls(many)), AGENT, NOW)) == callbacks.MAX_CHOICES


# The renter's call never waits on the lookup


async def test_a_lookup_that_raises_sends_the_call_to_the_concierge_and_is_logged(caplog):
    rt = SessionRuntime(_catalogue())
    await _done(rt)
    with caplog.at_level(logging.ERROR, logger="landline.voice.callbacks"):
        assert await find(_with_calls(rt, FakeCalls(fail=ConnectionError("db down"))), AGENT, NOW) == []
    assert "goes to the Concierge" in caplog.text


async def test_a_slow_lookup_sends_the_call_to_the_concierge(monkeypatch, caplog):
    monkeypatch.setattr(callbacks, "LOOKUP_S", 0.05)
    rt = SessionRuntime(_catalogue())
    c1, _ = await _done(rt)
    slow = FakeCalls([_row(c1, "t:1", "voicemail")], slow_s=5)
    with caplog.at_level(logging.ERROR, logger="landline.voice.callbacks"):
        assert await asyncio.wait_for(find(_with_calls(rt, slow), AGENT, NOW), 1) == []
    assert "goes to the Concierge" in caplog.text


# Which listing: code matches the key or the address


def test_the_agent_picks_by_key_or_by_saying_the_address():
    a, b, c = _choices()
    assert pick([a, b, c], digits="2") is b
    assert pick([a, b, c], speech="It's about 412 Markham.") is a
    assert pick([a, b, c], speech="the Bloor one") is b
    assert pick([a, b, c], speech="twelve dial street, 12 Dial") is c


@pytest.mark.parametrize(("digits", "speech"), [("", ""), ("9", ""), ("0", ""), ("#", ""),
                                                ("", "the other one"), ("", "Street")])
def test_no_key_a_wrong_key_or_words_naming_none_pick_nothing(digits, speech):
    assert pick(_choices(), digits=digits, speech=speech) is None


@pytest.mark.parametrize("said", ["two", "Two.", "number 2", "2", "press two"])
def test_a_key_said_instead_of_pressed_is_the_key_not_a_house_number(said):
    choices = [Missed("c_1", SID, "t:1", "2 Bloor St"), Missed("c_2", SID, "t:2", "45 King St")]
    assert pick(choices, speech=said) is choices[1]
    assert pick(choices, speech="45") is choices[1] and pick(choices, speech="2 Bloor") is choices[0]


def test_two_units_in_one_building_are_read_out_with_their_units():
    assert callbacks._place("88 Bloor St E", "unit 4") == "88 Bloor St E, unit 4"
    assert callbacks._place("88 Bloor St E", None) == "88 Bloor St E"


def test_two_addresses_on_one_street_are_told_apart_by_the_number_or_not_at_all():
    on_dial = [Missed("c_1", SID, "t:1", "12 Dial St"), Missed("c_2", SID, "t:2", "34 Dial St")]
    assert pick(on_dial, speech="34 Dial") is on_dial[1]
    assert pick(on_dial, speech="the one on Dial") is None   # never a guess


def test_the_question_says_the_disclosure_and_each_address_with_its_key():
    said = callbacks.ask(_choices()[:2])
    assert said.startswith(
        "Hi, this is Landline. I'm an AI assistant calling for a renter. Thanks for calling back.")
    assert said.endswith("press 1 for 412 Markham St, 2 for 88 Bloor St W.")
    assert callbacks.ask(_choices()[:2], again=True).startswith("Sorry, I didn't catch that.")


def test_on_a_callback_the_caller_answers_with_thanks_and_knows_they_called_back():
    listing = _catalogue().by_id["t:1"]
    plan = CallPlan(session_id=SID, listing_ids=["t:1"], created_at=NOW)
    back, out = brief(listing, plan, callback=True), brief(listing, plan)
    said = "Hi, this is Landline. I'm an AI assistant calling for a renter about 412 Markham St."
    assert (disclosure(back), disclosure(out)) == (f"{said} Thanks for calling back.", said)
    assert instructions(back).endswith(CALLED_BACK) and CALLED_BACK not in instructions(out)


# The Dispatcher answers it as a new call in the renter's session


class AnsweringRunner:
    """Answers a callback (dial.callback) and settles the listing with a fact."""
    transport, uses_phone, answers_callbacks = "voice", True, True

    def __init__(self):
        self.placed: list[tuple] = []

    async def place(self, dial, report):
        self.placed.append(dial)
        await report("live")
        fact = Fact(field="pets", value="dogs ok", quote="Dogs are fine.", speaker="Agent heard", at=NOW)
        return CallResult("done", CallOutcome(call_id=dial.call_id, listing_id=dial.listing_id,
                                              end_reason="done", facts=[fact]))


class FakeTexter:
    ready = True

    def __init__(self):
        self.sent = []

    def send_soon(self, to, from_, body):
        self.sent.append((to, from_, body))


def _dispatcher(rt, runner, texter=None) -> Dispatcher:
    settings = Settings(database_url=None, web_url="https://landline.example", transport="voice",
                        twilio_phone_number="+12895122338", demo_agent_phones=(AGENT,), listing_call_max_s=5)
    return Dispatcher(rt, settings, runner=runner, texter=texter, clock=lambda: NOW)


async def test_a_callback_reaches_the_caller_for_the_right_listing_as_a_new_call_in_the_session():
    rt, runner, texter = SessionRuntime(_catalogue()), AnsweringRunner(), FakeTexter()
    c1, c2 = await _done(rt)
    d = _dispatcher(rt, runner, texter)   # after the plan's calls, which it would otherwise place itself
    cid = await d.callback(Missed(c2, SID, "t:2", "88 Bloor St W"), "(647) 555-0101", "CA42")
    await d.idle()
    [dial] = runner.placed
    assert (dial.call_id, dial.listing_id, dial.to, dial.twilio_sid) == (cid, "t:2", AGENT, "CA42")
    assert dial.callback
    assert dial.plan == rt.state(SID).plan and dial.deadline == NOW + timedelta(seconds=5)
    assert cid not in (c1, c2) and cid.startswith("c_")   # a new call, its id minted by code
    view = rt.get(SID)
    back = next(c for c in view.calls if c.call_id == cid)
    assert (back.listing_id, back.state, back.outcome.end_reason) == ("t:2", "ended", "done")
    assert view.phase == "done" and texter.sent == []   # the results aren't texted again (A-13c updates)
    card = next(c for c in view.board if c.listing_id == "t:2")
    assert [f.quote for f in card.facts] == ["Dogs are fine."]


async def test_the_agents_phone_rings_for_no_listing_call_while_they_are_on_the_callback():
    rt = SessionRuntime(_catalogue())
    await _done(rt)
    free_during = []

    class Watches(AnsweringRunner):
        async def place(self, dial, report):
            free_during.append(list(d._phones._queue))
            return await super().place(dial, report)

    d = _dispatcher(rt, Watches())
    assert list(d._phones._queue) == [AGENT]
    await d.callback(Missed("c_x", SID, "t:1", "412 Markham St"), AGENT, "CA1")
    await d.idle()
    assert free_during == [[]] and list(d._phones._queue) == [AGENT]   # held, then free again


async def test_a_callback_nothing_here_can_answer_goes_to_the_concierge():
    rt = SessionRuntime(_catalogue())
    await _done(rt)

    class NoCallbacks(AnsweringRunner):
        answers_callbacks = False

    for runner in (NoCallbacks(), None):
        d = _dispatcher(rt, runner)
        assert d.callback_runner(SID) is None
        assert await d.callback(Missed("c_x", SID, "t:1", "412 Markham St"), AGENT, "CA1") is None
    d = _dispatcher(rt, AnsweringRunner())
    assert await d.callback(Missed("c_x", SID, "t:3", "12 Dial St"), AGENT, "CA1") is None   # not in the plan


async def test_a_callback_whose_line_never_came_up_ends_without_touching_the_card():
    class Drops(AnsweringRunner):
        async def place(self, dial, report):
            return CallResult("failed")   # the stream never came: never live

    rt = SessionRuntime(_catalogue())
    c1, _ = await _done(rt)
    d = _dispatcher(rt, Drops())
    cid = await d.callback(Missed(c1, SID, "t:1", "412 Markham St"), AGENT, "CA1")
    await d.idle()
    view = rt.get(SID)
    back = next(c for c in view.calls if c.call_id == cid)
    assert (back.state, back.outcome) == ("ended", None)   # ended once, with no outcome of its own
    assert rt.state(SID).outcomes["t:1"].call_id == c1     # the card still says what the missed call did


async def test_a_callback_that_runs_past_its_deadline_ends_once_as_timeout():
    class Hangs(AnsweringRunner):
        async def place(self, dial, report):
            await report("live")
            await asyncio.sleep(60)

    rt = SessionRuntime(_catalogue())
    await _done(rt)
    d = _dispatcher(rt, Hangs())
    d.settings = Settings(database_url=None, web_url="https://landline.example", transport="voice",
                          demo_agent_phones=(AGENT,), listing_call_max_s=0.05)
    cid = await d.callback(Missed("c_x", SID, "t:1", "412 Markham St"), AGENT, "CA1")
    await d.idle()
    back = next(c for c in rt.get(SID).calls if c.call_id == cid)
    assert (back.state, back.outcome.end_reason) == ("ended", "timeout")


# The lookup on Postgres: the last two days, the latest call per listing


@pytest.fixture
async def db(schema):
    database = await Database.open(os.environ["TEST_DATABASE_URL"], schema=schema[0], max_size=4)
    async with database.transaction() as conn:
        await conn.execute("truncate sessions, events, listings cascade")
        for lid, address in ADDRESSES.items():
            await conn.execute(
                "insert into listings (listing_id, source, source_id, url, first_seen, last_seen, status, "
                "address, rent, monthly_cost, beds, geom) values (%s, 't', %s, %s, %s, %s, 'active', %s, "
                "2000, 2000, 1, ST_SetSRID(ST_MakePoint(-79.38, 43.65), 4326)::geography)",
                (lid, lid[2:], f"https://example.com/{lid}", NOW, NOW, address))
    yield database
    await database.close()


async def _dialled(db, call_id: str, listing_id: str, reason: str | None, *, days_ago: float = 0,
                   to: str = AGENT, answered: bool = False) -> None:
    await db.calls.create(call_id, session_id=SID, role="listing", transport="voice", state="queued",
                          listing_id=listing_id, to_e164=to)
    if answered:
        await db.calls.set_state(call_id, "live", answered=True)
    if reason:
        await db.calls.finalize(call_id, reason)
    async with db.transaction() as conn:
        await conn.execute("update calls set created_at = now() - make_interval(secs => %s) "
                           "where call_id = %s", (days_ago * 86400, call_id))


async def test_on_postgres_only_a_missed_call_from_the_last_two_days_counts(db):
    rt = SessionRuntime(_catalogue(), db)
    await _done(rt, listing_ids=("t:1", "t:2", "t:3"))
    now = datetime.now(UTC)
    await _dialled(db, "c_pg00000001", "t:1", "voicemail", days_ago=0.5)          # missed: a callback
    await _dialled(db, "c_pg00000002", "t:2", "no_answer", days_ago=3)            # older than two days
    await _dialled(db, "c_pg00000003", "t:3", "voicemail", days_ago=1)            # missed, then reached:
    await _dialled(db, "c_pg00000004", "t:3", "done", days_ago=0.2, answered=True)   # the latest counts
    await _dialled(db, "c_pg00000005", "t:2", "voicemail", to="+16475550102")     # another number
    assert await lookup(rt, AGENT, now) == [Missed("c_pg00000001", SID, "t:1", "412 Markham St")]
    assert await lookup(rt, "+16475550199", now) == []                             # a number never called
    assert (await db.calls.get("c_pg00000005")).to_e164 == "+16475550102"


async def test_on_postgres_a_dropped_callback_keeps_the_listing_missed_and_the_renter_is_the_renter(db):
    rt = SessionRuntime(_catalogue(), db)
    await _done(rt, listing_ids=("t:1", "t:2"))
    now = datetime.now(UTC)
    await _dialled(db, "c_pg00000011", "t:1", "voicemail", days_ago=0.5)
    await _dialled(db, "c_pg00000012", "t:1", "failed", days_ago=0.1)            # its callback's line dropped
    await _dialled(db, "c_pg00000013", "t:2", "no_answer", to=RENTER)            # the renter's phone, rung
    assert [m.listing_id for m in await lookup(rt, AGENT, now)] == ["t:1"]       # still a callback
    assert await lookup(rt, RENTER, now) == []                                   # the renter: the Concierge
