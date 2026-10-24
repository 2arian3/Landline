"""The session runtime (O-02): one writer per session, idempotent events, one version bump per commit.

Every test runs twice: with sessions in memory only, and against Postgres in a
throwaway schema (skipped without TEST_DATABASE_URL), where it also checks what
was stored.
"""
import asyncio
import os
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from landline.app import create_app
from landline.config import Settings
from landline.contracts import (
    AnchorResolved,
    CallEnded,
    CallOutcome,
    CallPlan,
    CallsCompleted,
    CallsRequested,
    CallsScheduled,
    CallStateChanged,
    Fact,
    FactRecorded,
    FeedbackGiven,
    Listing,
    ListingFocused,
    OutcomeReady,
    PreferenceDelta,
    PreferencesUpdated,
    SessionEnded,
    SessionStarted,
    Viewing,
)
from landline.db.catalogue import Catalogue
from landline.db.pool import Database
from landline.orchestrator import Refused, SessionRuntime, UnknownSession

URL = os.environ.get("TEST_DATABASE_URL")
T0 = datetime(2026, 10, 23, 23, 0, tzinfo=UTC)
SID = "s_orch000001"


def _listing(i: int, rent: int, beds: float = 1) -> Listing:
    return Listing(listing_id=f"t:{i}", source="t", source_id=str(i), url=f"https://example.com/{i}",
                   first_seen=T0, last_seen=T0, address=f"{i} Test St", lat=43.65, lng=-79.38,
                   rent=rent, monthly_cost=rent, beds=beds, sqft=700)


def _catalogue() -> Catalogue:
    return Catalogue([_listing(i, rent) for i, rent in enumerate((2000, 2100, 2200, 2300, 2400, 2500, 2600),
                                                                  start=1)], "test")


@pytest.fixture(params=["memory", "postgres"])
async def rt(request):
    db = None
    if request.param == "postgres":   # the schema fixture skips the test without a database
        db = await Database.open(URL, schema=request.getfixturevalue("schema")[0], max_size=4)
        async with db.transaction() as conn:   # each test starts from no sessions
            await conn.execute("truncate sessions, events cascade")
    runtime = SessionRuntime(_catalogue(), db)
    yield runtime
    await runtime.close()
    if db:
        await db.close()


def _at(n: int = 0) -> datetime:
    return T0 + timedelta(seconds=n)


async def _started(rt: SessionRuntime, sid: str = SID):
    return await rt.start(SessionStarted(session_id=sid, at=_at(), idem_key=f"{sid}:start", channel="phone",
                                         caller_phone="+14165550100"))


def _feedback(key: str = "fb1", sid: str = SID) -> FeedbackGiven:
    return FeedbackGiven(session_id=sid, at=_at(1), idem_key=f"{sid}:{key}", listing_id="t:2",
                         reaction="dislike", reason="too dark", attribute="light")


async def _calling(rt: SessionRuntime, listing_ids=("t:1", "t:2")) -> list[str]:
    """Plan, hang up and schedule: the session in the calls flow with one queued call per listing."""
    await rt.emit(CallsRequested(session_id=SID, at=_at(2), idem_key=f"{SID}:plan",
                                 listing_ids=list(listing_ids), may_book=True))
    await rt.emit(SessionEnded(session_id=SID, at=_at(3), idem_key=f"{SID}:ended", reason="hangup"))
    call_ids = [f"c_orch00000{n}" for n in range(1, len(listing_ids) + 1)]
    plan = CallPlan(session_id=SID, listing_ids=list(listing_ids), may_book=True, created_at=_at(3))
    await rt.emit(CallsScheduled(session_id=SID, at=_at(3), idem_key=f"{SID}:scheduled", plan=plan,
                                 call_ids=call_ids))
    return call_ids


def _outcome(call_id: str, listing_id: str, **kw) -> OutcomeReady:
    fact = Fact(field="parking_cost", value="$180 a month", quote="Parking is one eighty.",
                speaker="Agent heard", at=_at(9))
    out = CallOutcome(call_id=call_id, listing_id=listing_id, end_reason="done", facts=[fact], **kw)
    return OutcomeReady(session_id=SID, at=_at(10), idem_key=f"{SID}:outcome:{call_id}", outcome=out)


def _card(view, listing_id: str):
    return next(c for c in view.board if c.listing_id == listing_id)


async def _stored(rt: SessionRuntime) -> tuple[list[str], int, str]:
    """What Postgres holds for SID: event types in order, the session's version and status."""
    row = await rt.db.sessions.get(SID)
    assert row.snapshot["view"]["version"] == row.version == rt.get(SID).version
    return [e.type for e in await rt.db.events.list(SID)], row.version, row.status


async def test_a_session_starts_once_at_version_1_and_unknown_ids_are_refused(rt):
    view = await _started(rt)
    assert (view.version, view.phase, len(view.board)) == (1, "open", 6)
    assert await _started(rt) == view   # a retried start changes nothing
    assert rt.state(SID).caller_phone == "+14165550100" and len(rt) == 1
    with pytest.raises(UnknownSession):   # no session springs up from an unknown id
        await rt.emit(_feedback(sid="s_nobody0001"))
    if rt.db:
        assert await _stored(rt) == (["SessionStarted"], 1, "open")


async def test_each_commit_bumps_the_version_once_and_wakes_the_stream(rt):
    await _started(rt)

    async def woken():
        async with rt.changed:
            await rt.changed.wait()
        return rt.get(SID).version

    waiter = asyncio.create_task(woken())
    await asyncio.sleep(0)
    view = await rt.emit(PreferencesUpdated(session_id=SID, at=_at(1), idem_key=f"{SID}:p1",
                                            delta=PreferenceDelta(max_monthly=2000)))
    assert view.version == 2 and await asyncio.wait_for(waiter, 1) == 2
    assert [c.listing_id for c in view.board] == ["t:1", "t:2", "t:3"]   # within 2000 × 1.1
    async with rt.session_tx(SID) as tx:   # two events, one commit
        await tx.apply(ListingFocused(session_id=SID, at=_at(2), idem_key=f"{SID}:f1", listing_id="t:2",
                                      origin="ui"))
        await tx.apply(_feedback())
    assert rt.get(SID).version == 3
    # An event that changes nothing stores nothing and keeps the version.
    same = await rt.emit(ListingFocused(session_id=SID, at=_at(3), idem_key=f"{SID}:f2", listing_id="t:2",
                                        origin="voice"))
    assert same.version == 3
    if rt.db:
        assert await _stored(rt) == (["SessionStarted", "PreferencesUpdated", "ListingFocused",
                                      "FeedbackGiven"], 3, "open")


async def test_a_duplicate_event_is_a_no_op(rt):
    await _started(rt)
    first = await rt.emit(_feedback())
    again = await rt.emit(_feedback())
    assert first.version == again.version == 2
    assert _card(again, "t:2").reaction == "too dark"
    if rt.db:
        assert await _stored(rt) == (["SessionStarted", "FeedbackGiven"], 2, "open")


async def test_a_feedback_re_rank_racing_an_outcome_keeps_the_outcome(rt):
    """Realest bug #5: a preference re-rank read the board, awaited, and wrote it back over a call result."""
    await _started(rt)
    call_ids = await _calling(rt)
    before = rt.get(SID).version

    async def slow_feedback():
        async with rt.session_tx(SID) as tx:
            await tx.apply(_feedback())
            await asyncio.sleep(0.05)   # say, an agent call inside the transaction

    feedback = asyncio.create_task(slow_feedback())
    await asyncio.sleep(0.01)           # the feedback holds the session now
    await asyncio.gather(feedback, rt.emit(_outcome(call_ids[0], "t:1")))
    view = rt.get(SID)
    assert view.version == before + 2
    assert _card(view, "t:2").reaction == "too dark"
    assert _card(view, "t:1").status == "verified" and _card(view, "t:1").facts[0].field == "parking_cost"
    if rt.db:
        types, version, _ = await _stored(rt)
        assert types[-2:] == ["FeedbackGiven", "OutcomeReady"] and version == view.version


async def test_a_refused_event_leaves_nothing_behind(rt):
    await _started(rt)
    with pytest.raises(Refused):
        await rt.emit(CallsRequested(session_id=SID, at=_at(1), idem_key=f"{SID}:bad", listing_ids=["nope"]))
    await rt.emit(SessionEnded(session_id=SID, at=_at(2), idem_key=f"{SID}:ended", reason="hangup"))
    with pytest.raises(Refused):        # a closed brief takes no plan
        await rt.emit(CallsRequested(session_id=SID, at=_at(3), idem_key=f"{SID}:late", listing_ids=["t:1"]))
    view = rt.get(SID)
    assert (view.phase, view.plan, view.version) == ("closed", [], 2)
    # A failure inside a transaction rolls back every event applied in it.
    with pytest.raises(RuntimeError):
        async with rt.session_tx(SID) as tx:
            await tx.apply(_feedback())
            raise RuntimeError("the agent timed out")
    assert rt.get(SID).version == 2 and _card(rt.get(SID), "t:2").reaction is None
    if rt.db:
        row = await rt.db.sessions.get(SID)
        assert await _stored(rt) == (["SessionStarted", "SessionEnded"], 2, "closed") and row.ended_at


async def test_the_calls_walk_the_board_from_queued_to_done(rt):
    await _started(rt)
    ended = []

    async def on_ended(event, view):
        ended.append((event.reason, view.phase))

    rt.on("SessionEnded", on_ended)
    c1, c2 = await _calling(rt, ("t:5", "t:6"))
    await asyncio.sleep(0)
    assert ended == [("hangup", "calling")]   # what the Dispatcher listens for
    view = rt.get(SID)
    assert (view.phase, view.plan) == ("calling", ["t:5", "t:6"])
    assert [c.state for c in view.calls] == ["queued", "queued"]

    await rt.emit(CallStateChanged(session_id=SID, at=_at(4), idem_key=f"{SID}:{c1}:live", call_id=c1,
                                   state="live"))
    await rt.emit(FactRecorded(session_id=SID, at=_at(5), idem_key=f"{SID}:{c1}:fact0", call_id=c1,
                               field="pets", value="dogs ok", quote="Dogs are fine.", turn_idx=3))
    live = rt.get(SID)
    assert _card(live, "t:5").status == "calling" and live.calls[0].started_at == _at(4)
    assert [f.quote for f in _card(live, "t:5").facts] == ["Dogs are fine."]

    await rt.emit(CallEnded(session_id=SID, at=_at(6), idem_key=f"{SID}:{c1}:end", call_id=c1, reason="done"))
    late = await rt.emit(CallStateChanged(session_id=SID, at=_at(7), idem_key=f"{SID}:{c1}:late", call_id=c1,
                                          state="ringing"))
    assert late.calls[0].state == "ended"   # an ended call never changes again
    booked = Viewing(starts_at=datetime(2026, 10, 24, 18, 0, tzinfo=UTC), slot_text="Saturday at two")
    await rt.emit(_outcome(c1, "t:5", viewing=booked))
    gone = Fact(field="available", value="no", quote="It's rented.", speaker="Agent heard", at=_at(8))
    rented = CallOutcome(call_id=c2, listing_id="t:6", end_reason="done", facts=[gone])
    await rt.emit(OutcomeReady(session_id=SID, at=_at(11), idem_key=f"{SID}:outcome:{c2}", outcome=rented))
    wrong = _outcome(c2, "t:1").model_copy(update={"idem_key": f"{SID}:outcome:wrong"})
    with pytest.raises(Refused):   # an outcome's listing must be its call's
        await rt.emit(wrong)
    done = await rt.emit(CallsCompleted(session_id=SID, at=_at(12), idem_key=f"{SID}:completed",
                                        call_ids=[c1, c2]))
    assert done.phase == "done"
    assert done.board[0].listing_id == "t:5" and done.board[0].status == "booked"   # a booking pins
    assert _card(done, "t:6").status == "dead"   # filtered out of the ranking, still shown with its status
    if rt.db:
        types, _, status = await _stored(rt)
        assert status == "done" and (await rt.db.sessions.get(SID)).ended_at is not None
        assert types.count("OutcomeReady") == 2 and types[-1] == "CallsCompleted"


async def test_a_failing_subscriber_does_not_undo_the_commit(rt):
    await _started(rt)

    async def broken(event, view):
        raise RuntimeError("dispatcher down")

    rt.on("FeedbackGiven", broken)
    view = await rt.emit(_feedback())
    await asyncio.sleep(0)
    assert view.version == 2 and rt.get(SID).version == 2


async def test_preferences_are_deltas_and_anchors_keep_their_place(rt):
    await _started(rt)
    await rt.emit(PreferencesUpdated(session_id=SID, at=_at(1), idem_key=f"{SID}:p1", delta=PreferenceDelta(
        max_monthly=2500, anchors=[{"label": "King and Bay"}])))
    await rt.emit(AnchorResolved(session_id=SID, at=_at(2), idem_key=f"{SID}:a1", label="king and bay",
                                 lat=43.6487, lng=-79.3816))
    again = PreferenceDelta(min_beds=1, anchors=[{"label": "King and Bay"}])
    view = await rt.emit(PreferencesUpdated(session_id=SID, at=_at(3), idem_key=f"{SID}:p2", delta=again))
    p = view.preferences
    assert (p.max_monthly, p.min_beds) == (2500, 1)
    assert [(a.label, a.lat) for a in p.anchors] == [("King and Bay", 43.6487)]


def test_the_api_sends_a_judge_session_through_the_runtime():
    settings = Settings(database_url=None, web_url="http://localhost:3000", transport="stub",
                        judge_audio_ready=True)
    with TestClient(create_app(settings, _catalogue())) as c:
        sid = c.post("/api/judge/start").json()["session_id"]
        rt = c.app.state.runtime
        assert rt.has(sid) and rt.state(sid).channel == "judge"
        assert c.get(f"/api/sessions/{sid}").json()["version"] == 1
        prefs = c.post(f"/api/sessions/{sid}/preferences", json={"max_monthly": 2000}).json()
        assert prefs["version"] == 2 and [b["listing_id"] for b in prefs["board"]] == ["t:1", "t:2", "t:3"]
        focused = c.post(f"/api/sessions/{sid}/focus", json={"listing_id": "t:2"}).json()
        assert focused["focus_listing_id"] == "t:2"
        fb = c.post(f"/api/sessions/{sid}/feedback", json={"listing_id": "t:2", "reaction": "dislike",
                                                           "reason": "too dark"}).json()
        assert _card_json(fb, "t:2")["reaction"] == "too dark" and fb["version"] == 4
        assert c.post(f"/api/sessions/{sid}/calls").json()["plan"] == ["t:1", "t:2", "t:3"]
        assert c.post(f"/api/sessions/{sid}/hang-up").json()["phase"] == "calling"
        assert c.post(f"/api/sessions/{sid}/calls").status_code == 409       # the brief is over
        assert c.post(f"/api/sessions/{sid}/reset").status_code == 409       # demo sessions only
        # 6, then 7 once the Dispatcher (A-06) has frozen the plan and queued the calls in the background.
        assert c.get(f"/api/sessions/{sid}").json()["version"] in (6, 7)
        assert c.get("/health").json()["sessions"] == 1
        # Any other id is a demo session, which the demo store keeps.
        assert c.get("/api/sessions/demo-abc").json()["version"] == 1 and not rt.has("demo-abc")


def _card_json(view: dict, listing_id: str) -> dict:
    return next(b for b in view["board"] if b["listing_id"] == listing_id)
