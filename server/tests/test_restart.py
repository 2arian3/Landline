"""A listing call cut by a restart (#217). The row is written before the end is logged, and the next
process closes what was left behind: a call past its deadline ends timeout, a call that never dialled is
placed again, the session reaches done, and the renter gets one results text. No phone, no network, no
Postgres: the repositories are the in-memory ones below, the same shape the voice tests already fake."""
import asyncio
import logging
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from landline import drain
from landline.config import Settings
from landline.contracts import CallPlan, CallsRequested, Listing, SessionEnded, SessionStarted
from landline.db.catalogue import Catalogue
from landline.db.rows import CallRow, SessionRow
from landline.db.runtime import StaleVersion
from landline.orchestrator import SessionRuntime
from landline.voice.calls import RenterCalls
from landline.voice.dispatch import CallResult, Dial, Dispatcher
from landline.voice.outbound import Outbound, OutboundCalls

# Saturday 24 Oct 2026, 11:00 in Toronto: inside business hours.
NOW = datetime(2026, 10, 24, 15, 0, tzinfo=UTC)
# 22:00 in Toronto the same day: after calling hours, so a phone call stays queued.
NIGHT = datetime(2026, 10, 25, 2, 0, tzinfo=UTC)
SID = "s_restart001"
PHONE = "+14165550199"
AGENTS = ("+16475550101", "+16475550102")
DEADLINE = timedelta(seconds=300)


class _Person:
    user_id = 1
    name = None
    last_seen_at = None


class _Users:
    async def by_phone(self, _phone, conn=None):
        return _Person()

    async def upsert(self, _phone, **_kw):
        return _Person()

    async def touch(self, _uid, conn=None):
        return None


class _Sink:
    async def upsert(self, *_a, **_kw):
        return None

    async def add(self, *_a, **_kw):
        return None

    async def get(self, *_a, **_kw):
        return None


class MemoryDB:
    """The session, event and call repositories the sweep uses, without Postgres."""

    def __init__(self):
        self.sessions = _Sessions()
        self.events = _Events()
        self.calls = _Calls()
        self.users, self.profiles, self.interactions = _Users(), _Sink(), _Sink()

    @asynccontextmanager
    async def transaction(self):
        yield self


class _Sessions:
    def __init__(self):
        self.rows: dict[str, dict] = {}

    async def create(self, session_id, channel, user_id=None, conn=None):
        self.rows[session_id] = {"session_id": session_id, "user_id": user_id, "channel": channel,
                                 "status": "open", "version": 0, "snapshot": {}, "started_at": NOW,
                                 "ended_at": None}
        return self._row(session_id)

    async def get(self, session_id, conn=None):
        return self._row(session_id) if session_id in self.rows else None

    async def link_user(self, session_id, user_id, conn=None):
        row = self.rows.get(session_id)
        if row is None:
            return False
        row["user_id"] = user_id
        return True

    async def list_active(self, conn=None):
        rows = [self._row(sid) for sid, row in self.rows.items() if row["status"] in ("open", "calling")]
        return sorted(rows, key=lambda r: (r.started_at, r.session_id))

    async def save(self, session_id, *, expected_version, status, snapshot, conn=None):
        row = self.rows[session_id]
        if row["version"] != expected_version:
            raise StaleVersion(session_id)
        row["version"] += 1
        row["status"] = status
        row["snapshot"] = snapshot
        return self._row(session_id)

    async def end(self, session_id, status, conn=None):
        row = self.rows[session_id]
        if row["ended_at"] is not None:
            return False
        row["status"] = status
        row["ended_at"] = NOW
        return True

    def _row(self, session_id) -> SessionRow:
        row = self.rows[session_id]
        return SessionRow(session_id=row["session_id"], user_id=row["user_id"], channel=row["channel"],
                          status=row["status"], version=row["version"], snapshot=row["snapshot"],
                          started_at=row["started_at"], ended_at=row["ended_at"])


class _Events:
    def __init__(self):
        self.keys: set[str] = set()

    async def append(self, event, conn=None):
        if event.idem_key in self.keys:
            return None
        self.keys.add(event.idem_key)
        return event

    async def seen(self, idem_key, conn=None):
        return idem_key in self.keys


class _Calls:
    def __init__(self):
        self.rows: dict[str, dict] = {}

    async def create(self, call_id, *, session_id, role, transport, state, listing_id=None,
                     deadline_at=None, attempt=1, conn=None):
        self.rows[call_id] = {"call_id": call_id, "session_id": session_id, "listing_id": listing_id,
                              "role": role, "transport": transport, "twilio_sid": None, "state": state,
                              "end_reason": None, "created_at": NOW, "answered_at": None, "ended_at": None,
                              "deadline_at": deadline_at, "attempt": attempt}
        return self._row(call_id)

    async def set_state(self, call_id, state, *, twilio_sid=None, answered=False, deadline_at=None,
                        conn=None):
        row = self.rows[call_id]
        if row["ended_at"] is not None:
            return None
        row["state"] = state
        if twilio_sid:
            row["twilio_sid"] = twilio_sid
        if answered and row["answered_at"] is None:
            row["answered_at"] = NOW
        if deadline_at is not None:
            row["deadline_at"] = deadline_at
        return self._row(call_id)

    async def finalize(self, call_id, reason, conn=None):
        row = self.rows[call_id]
        if row["ended_at"] is not None:
            return False
        row["state"], row["end_reason"], row["ended_at"] = "ended", reason, NOW
        return True

    async def for_session(self, session_id, conn=None):
        rows = [self._row(cid) for cid, row in self.rows.items() if row["session_id"] == session_id]
        return sorted(rows, key=lambda r: (r.created_at, r.call_id))

    async def in_state(self, states, conn=None):
        wanted = set(states)
        rows = [self._row(cid) for cid, row in self.rows.items()
                if row["state"] in wanted and row["ended_at"] is None]
        return sorted(rows, key=lambda r: (r.created_at, r.call_id))

    async def past_deadline(self, now=None, conn=None):
        now = now or datetime.now(UTC)
        rows = [self._row(cid) for cid, row in self.rows.items()
                if row["ended_at"] is None and row["deadline_at"] is not None and row["deadline_at"] < now]
        return sorted(rows, key=lambda r: r.deadline_at)

    def _row(self, call_id) -> CallRow:
        row = self.rows[call_id]
        return CallRow(call_id=row["call_id"], session_id=row["session_id"], listing_id=row["listing_id"],
                       role=row["role"], transport=row["transport"], twilio_sid=row["twilio_sid"],
                       state=row["state"], end_reason=row["end_reason"], created_at=row["created_at"],
                       answered_at=row["answered_at"], ended_at=row["ended_at"],
                       deadline_at=row["deadline_at"], attempt=row["attempt"])


class Texter:
    ready = True

    def __init__(self):
        self.sent = []

    def send_soon(self, to, from_, body):
        self.sent.append((to, from_, body))


class Rings:
    """Reports ringing, then waits until the process is gone."""
    transport = "stub"
    uses_phone = False

    def __init__(self):
        self.ringing = asyncio.Event()

    async def place(self, dial, report):
        await report("ringing")
        self.ringing.set()
        await asyncio.Event().wait()


class Dials:
    transport = "sim"
    uses_phone = True

    def __init__(self):
        self.dials = []

    async def place(self, dial, report):
        self.dials.append(dial)
        return CallResult("no_answer")


def _settings(**kw) -> Settings:
    base = Settings(database_url=None, web_url="https://landline.example", transport="stub",
                    twilio_phone_number="+12895122338", demo_agent_phones=AGENTS, higgs_max_sessions=3,
                    listing_call_max_s=DEADLINE.total_seconds())
    return replace(base, **kw)


def _catalogue() -> Catalogue:
    listing = Listing(listing_id="t:1", source="t", source_id="1", url="https://example.com/1",
                      first_seen=NOW, last_seen=NOW, address="1 Dial St", lat=43.65, lng=-79.38,
                      rent=2000, monthly_cost=2000, beds=1)
    return Catalogue([listing], "test")


def _dispatcher(rt, runner, texter=None, *, clock, sleep=asyncio.sleep) -> Dispatcher:
    return Dispatcher(rt, _settings(), runner=runner, texter=texter, clock=clock, sleep=sleep)


async def _plan(rt: SessionRuntime) -> None:
    await rt.start(SessionStarted(session_id=SID, at=NOW, idem_key=f"{SID}:start", channel="phone",
                                  caller_phone=PHONE))
    await rt.emit(CallsRequested(session_id=SID, at=NOW, idem_key=f"{SID}:plan", listing_ids=["t:1"]))
    await rt.emit(SessionEnded(session_id=SID, at=NOW, idem_key=f"{SID}:end", reason="hangup"))


async def _die(dispatcher: Dispatcher, runtime: SessionRuntime) -> None:
    """The process is gone: its tasks are cancelled where they stand, and nothing finishes the call."""
    for task in list(dispatcher._tasks):
        task.cancel()
    await asyncio.gather(*list(dispatcher._tasks), return_exceptions=True)
    await dispatcher.close()
    await runtime.close()


def _only(db: MemoryDB) -> dict:
    return next(iter(db.calls.rows.values()))


async def test_a_restart_while_a_call_is_ringing_ends_it_and_texts_once():
    db, clock = MemoryDB(), {"t": NOW}
    rt, texter, runner = SessionRuntime(_catalogue(), db), Texter(), Rings()
    first = _dispatcher(rt, runner, texter, clock=lambda: clock["t"])
    await _plan(rt)
    await runner.ringing.wait()
    row = _only(db)
    assert (row["state"], row["end_reason"], row["ended_at"]) == ("ringing", None, None)
    assert row["deadline_at"] == NOW + DEADLINE
    assert db.sessions.rows[SID]["status"] == "calling"
    await _die(first, rt)   # the process dies before the end is written

    clock["t"] = NOW + DEADLINE + timedelta(seconds=1)   # the deadline passed while it was down
    rt2, texter2 = SessionRuntime(_catalogue(), db), Texter()
    again = _dispatcher(rt2, Rings(), texter2, clock=lambda: clock["t"])
    await again.recover()
    await again.idle()
    await again.recover()   # a second start must not text again
    await again.idle()

    row = _only(db)
    assert (row["state"], row["end_reason"]) == ("ended", "timeout") and row["ended_at"] is not None
    assert rt2.get(SID).phase == "done" and db.sessions.rows[SID]["status"] == "done"
    [(to, sender, body)] = texter2.sent
    assert (to, sender) == (PHONE, "+12895122338")
    assert body.startswith("Landline: The call to 1 Dial St dropped before I learned anything.")
    assert body.endswith(f"Map: https://landline.example/s/{SID}")
    assert texter.sent == []   # the process that died never sent one


async def test_a_row_already_ended_by_the_crash_still_texts_once():
    """The end was stored, then the process died before CallsCompleted. The next start still texts once."""
    db, clock = MemoryDB(), {"t": NOW}
    rt, runner = SessionRuntime(_catalogue(), db), Rings()
    first = _dispatcher(rt, runner, clock=lambda: clock["t"])
    await _plan(rt)
    await runner.ringing.wait()
    await _die(first, rt)
    assert await db.calls.finalize(next(iter(db.calls.rows)), "timeout") is True   # written, then lost

    clock["t"] = NOW + DEADLINE + timedelta(seconds=1)
    rt2, texter = SessionRuntime(_catalogue(), db), Texter()
    again = _dispatcher(rt2, Rings(), texter, clock=lambda: clock["t"])
    await again.recover()
    await again.idle()
    assert _only(db)["end_reason"] == "timeout" and rt2.get(SID).phase == "done" and len(texter.sent) == 1


async def test_a_restart_places_a_call_that_never_dialled():
    db = MemoryDB()
    held = asyncio.Event()

    async def sleep(_seconds):
        held.set()
        await asyncio.Event().wait()   # waiting for morning when the process dies

    rt = SessionRuntime(_catalogue(), db)
    first = _dispatcher(rt, Dials(), clock=lambda: NIGHT, sleep=sleep)
    await _plan(rt)
    await held.wait()
    assert (_only(db)["state"], _only(db)["deadline_at"], _only(db)["ended_at"]) == ("queued", None, None)
    await _die(first, rt)

    rt2, texter, runner = SessionRuntime(_catalogue(), db), Texter(), Dials()
    again = _dispatcher(rt2, runner, texter, clock=lambda: NOW)
    await again.recover()
    await again.idle()
    assert [d.listing_id for d in runner.dials] == ["t:1"]
    assert rt2.get(SID).phase == "done" and len(texter.sent) == 1
    assert _only(db)["state"] == "ended"


async def test_the_end_is_written_before_it_is_logged(caplog):
    order = []

    class Calls:
        async def finalize(self, call_id, reason):
            order.append("write")
            assert "ended:" not in caplog.text
            return True

    calls = OutboundCalls()
    plan = CallPlan(session_id=SID, listing_ids=["t:1"], created_at=NOW)
    dial = Dial(call_id="c_restart01", session_id=SID, listing_id="t:1", to=AGENTS[0], plan=plan,
                deadline=NOW + DEADLINE)
    calls.live[dial.call_id] = Outbound(dial=dial, report=_nothing)
    with caplog.at_level(logging.INFO, logger="landline.voice.outbound"):
        assert await calls.end(dial.call_id, "timeout", SimpleNamespace(calls=Calls())) is True
    assert order == ["write"]
    assert f"listing call {dial.call_id} ended: timeout" in caplog.text
    assert await calls.end(dial.call_id, "hangup", SimpleNamespace(calls=Calls())) is False   # once


async def test_a_failed_write_is_not_logged_as_an_end(caplog):
    class Calls:
        async def finalize(self, call_id, reason):
            raise RuntimeError("pool is closed")

    calls = OutboundCalls()
    plan = CallPlan(session_id=SID, listing_ids=["t:1"], created_at=NOW)
    dial = Dial(call_id="c_restart02", session_id=SID, listing_id="t:1", to=AGENTS[0], plan=plan,
                deadline=NOW + DEADLINE)
    calls.live[dial.call_id] = Outbound(dial=dial, report=_nothing)
    with caplog.at_level(logging.INFO, logger="landline.voice.outbound"):
        assert await calls.end(dial.call_id, "timeout", SimpleNamespace(calls=Calls())) is True
    assert "ended: timeout" not in caplog.text
    assert "was not saved" in caplog.text


async def _nothing(_state):
    return None


async def test_the_drain_writes_a_listing_call_before_it_logs_the_end(caplog):
    order = []

    class Calls:
        async def finalize(self, call_id, reason):
            order.append((call_id, reason))
            assert "ended:" not in caplog.text
            return True

    state = SimpleNamespace(calls=RenterCalls(3), outbound=OutboundCalls(),
                            runtime=SimpleNamespace(db=SimpleNamespace(calls=Calls()),
                                                    changed=asyncio.Condition()),
                            sessions=SimpleNamespace(changed=asyncio.Condition()))
    plan = CallPlan(session_id=SID, listing_ids=["t:1"], created_at=NOW)
    dial = Dial(call_id="c_restart03", session_id=SID, listing_id="t:1", to=AGENTS[0], plan=plan,
                deadline=NOW + DEADLINE)
    state.outbound.live[dial.call_id] = Outbound(dial=dial, report=_nothing)
    with caplog.at_level(logging.INFO, logger="landline.voice.outbound"):
        await drain.Drain(state, limit_s=0, every_s=0.01).run()
    assert order == [("c_restart03", "failed")]
    assert "listing call c_restart03 ended: failed" in caplog.text
    assert not state.outbound.finalize(dial.call_id, "hangup")   # still exactly once
