"""A restart that lets live calls finish (#167, landline/drain.py): new calls and judges hear "lines busy",
live calls get the drain's time to end on their own, the rest end `failed` once, page streams end cleanly,
and only then does the SIGTERM handler that was there before (uvicorn's) run. No network, no restart."""
import asyncio
import signal
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from fastapi.testclient import TestClient

from landline import drain
from landline.api.twilio import BUSY
from landline.app import create_app
from landline.config import Settings
from landline.contracts import CallPlan, Listing
from landline.db.catalogue import Catalogue
from landline.voice import twilio
from landline.voice.calls import RenterCalls
from landline.voice.dispatch import Dial
from landline.voice.outbound import Outbound, OutboundCalls

NOW = datetime(2026, 10, 24, 18, 0, tzinfo=UTC)
PUBLIC = "https://landline.test"
TOKEN = "test-token"


def _state():
    return SimpleNamespace(calls=RenterCalls(3), outbound=OutboundCalls(),
                           runtime=SimpleNamespace(changed=asyncio.Condition()),
                           sessions=SimpleNamespace(changed=asyncio.Condition()))


async def _nothing(_state):
    return None


def _listing_call() -> Outbound:
    plan = CallPlan(session_id="s_drain00001", listing_ids=["t:1"], created_at=NOW)
    dial = Dial(call_id="c_drain00001", session_id="s_drain00001", listing_id="t:1", to="+16475550101",
                plan=plan, deadline=NOW + timedelta(minutes=5))
    return Outbound(dial=dial, report=_nothing)


async def test_a_live_call_gets_to_end_on_its_own_and_keeps_its_reason():
    state, loop = _state(), asyncio.get_running_loop()
    call = state.calls.open("+14165550100")
    loop.call_later(0.1, state.calls.finalize, call.call_id, "end_session")
    started = loop.time()
    run = drain.Drain(state, limit_s=5, every_s=0.02)
    await run.run()
    assert loop.time() - started < 1                 # done when the call was, not at the limit
    assert call.end_reason == "end_session" and run.closing.is_set()


async def test_calls_still_live_at_the_limit_end_failed_once():
    state = _state()
    renter = state.calls.open("+14165550100")
    listing = _listing_call()
    state.outbound.live[listing.dial.call_id] = listing
    await drain.Drain(state, limit_s=0.1, every_s=0.02).run()
    assert renter.end_reason == "failed" and listing.ended.result() == "failed"
    assert not state.calls.finalize(renter.call_id, "hangup")            # once
    assert not state.outbound.finalize(listing.dial.call_id, "hangup")


def _client(**overrides) -> TestClient:
    settings = Settings(database_url=None, web_url="http://localhost:3000", transport="stub",
                        boson_api_key="test-key", twilio_auth_token=TOKEN, public_url=PUBLIC,
                        judge_audio_ready=True)
    listing = Listing(listing_id="t:1", source="t", source_id="1", url="https://example.com/1",
                      first_seen=NOW, last_seen=NOW, address="1 Main St", lat=43.65, lng=-79.38, rent=2400,
                      monthly_cost=2400, beds=1)
    return TestClient(create_app(replace(settings, **overrides), Catalogue([listing], "test")))


def test_while_draining_a_new_call_and_a_judge_hear_the_lines_are_busy():
    with _client() as c:
        c.app.state.drain.draining = True
        params = {"CallSid": "CA1", "From": "+14165550100", "To": "+16475550199"}
        sig = twilio.signature(TOKEN, f"{PUBLIC}/twiml/renter", params)
        r = c.post("/twiml/renter", data=params, headers={"X-Twilio-Signature": sig})
        assert BUSY in r.text and "<Hangup/>" in r.text and c.app.state.calls.live == {}
        r = c.post("/api/judge/start")
        assert r.status_code == 503 and r.json()["reason"] == "busy" and len(c.app.state.runtime) == 0


def test_a_page_stream_ends_cleanly_when_the_drain_closes_it():
    with _client() as c:
        sid = c.post("/api/judge/start").json()["session_id"]
        with c.stream("GET", f"/api/sessions/{sid}/stream") as r:
            lines = r.iter_lines()
            assert next(lines).startswith("data:")
            c.portal.call(c.app.state.drain.run)   # no live calls: it closes the streams at once
            assert all(not line.startswith("data:") for line in lines)   # and this one ends, no error


async def test_sigterm_drains_first_then_hands_the_signal_on():
    handed = []
    before = signal.signal(signal.SIGTERM, lambda signum, frame: handed.append(signum))   # as uvicorn's
    try:
        run = drain.install(_state())
        signal.raise_signal(signal.SIGTERM)
        for _ in range(200):
            if handed:
                break
            await asyncio.sleep(0.01)
        assert run.draining and run.closing.is_set() and handed == [signal.SIGTERM]
    finally:
        signal.signal(signal.SIGTERM, before)
