"""Hard rule 6 per session (voice/routing.py): a judge's calls go to the sim landlords whatever TRANSPORT
says, and only a phone renter's go over Twilio, only with TRANSPORT=voice. Through the real app, with a fake
Twilio and a fake sim runner; no network. Then the sim's turns reaching call_turns for captions."""
import asyncio
from dataclasses import replace
from datetime import UTC, datetime

import httpx
from fastapi.testclient import TestClient

from landline.app import create_app
from landline.config import Settings
from landline.contracts import CallsRequested, Listing, SessionEnded, SessionStarted
from landline.db.catalogue import Catalogue
from landline.voice.dispatch import CallResult
from landline.voice.outbound import Turns
from landline.voice.routing import ByChannel
from landline.voice.sim import SimTrace, Turn

AT = datetime(2026, 10, 24, 15, 0, tzinfo=UTC)
AGENT = "+16475550101"


def _settings(**kw) -> Settings:
    return replace(Settings(database_url=None, web_url="http://localhost:3000", transport="voice",
                            judge_audio_ready=True, boson_api_key="k", twilio_account_sid="AC1",
                            twilio_auth_token="t", twilio_phone_number="+12892765257",
                            public_url="https://landline.test", demo_agent_phones=(AGENT,),
                            force_business_hours=True, listing_call_max_s=0.5), **kw)


def _catalogue() -> Catalogue:
    return Catalogue([Listing(listing_id=f"t:{i}", source="t", source_id=str(i), url=f"https://example.com/{i}",
                              first_seen=AT, last_seen=AT, address=f"{i} Route St", lat=43.65, lng=-79.38,
                              rent=1800 + i, monthly_cost=1800 + i, beds=1) for i in (1, 2, 3)], "test")


class FakeSim:
    transport, uses_phone = "sim", False

    def __init__(self):
        self.dials = []

    async def place(self, dial, report):
        self.dials.append(dial)
        await report("live")
        return CallResult("done")


class FakeTwilio:
    def __init__(self):
        self.created = []

    async def handle(self, request):
        if request.url.path.endswith("/Calls.json"):
            self.created.append(request.content.decode())
            return httpx.Response(201, json={"sid": f"CA{len(self.created)}"})
        return httpx.Response(200, json={})


def _app(**kw):
    c = TestClient(create_app(_settings(**kw), _catalogue()))
    c.__enter__()
    router = c.app.state.dispatcher.runner
    assert isinstance(router, ByChannel)
    sim, twilio_api = FakeSim(), FakeTwilio()
    router.sim = sim
    if router.phone is not None:
        router.phone.client = httpx.AsyncClient(transport=httpx.MockTransport(twilio_api.handle))
    return c, sim, twilio_api


def _finish(c):
    c.portal.call(c.app.state.dispatcher.idle)


def _judge_brief_with_three_picks(c) -> str:
    sid = c.post("/api/judge/start").json()["session_id"]
    c.post(f"/api/sessions/{sid}/preferences", json={"max_monthly": 2500})
    assert len(c.post(f"/api/sessions/{sid}/calls").json()["plan"]) == 3
    assert c.post(f"/api/sessions/{sid}/hang-up").json()["phase"] == "calling"   # the judge's Done
    return sid


def _phone_brief(c, sid="s_route00001") -> str:
    rt = c.app.state.runtime

    async def brief():
        await rt.start(SessionStarted(session_id=sid, at=AT, idem_key=f"{sid}:start", channel="phone",
                                      caller_phone="+14165550199"))
        await rt.emit(CallsRequested(session_id=sid, at=AT, idem_key=f"{sid}:plan",
                                     listing_ids=["t:1", "t:2"]))
        await rt.emit(SessionEnded(session_id=sid, at=AT, idem_key=f"{sid}:end", reason="hangup"))
    c.portal.call(brief)
    return sid


def test_a_judge_with_three_picks_gets_three_sim_calls_and_no_dial_even_with_transport_voice():
    c, sim, twilio_api = _app(transport="voice")
    try:
        sid = _judge_brief_with_three_picks(c)
        _finish(c)
        assert sorted(d.listing_id for d in sim.dials) == ["t:1", "t:2", "t:3"]
        assert all(d.to is None for d in sim.dials)                # no number handed out, nothing to dial
        assert twilio_api.created == []
        assert c.get(f"/api/sessions/{sid}").json()["phase"] == "done"
    finally:
        c.__exit__(None, None, None)


def test_a_phone_renter_with_transport_voice_is_dialled_over_twilio():
    c, sim, twilio_api = _app(transport="voice")
    try:
        _phone_brief(c)
        _finish(c)
        assert len(twilio_api.created) == 2
        assert all("To=%2B16475550101" in body for body in twilio_api.created)
        assert sim.dials == []
    finally:
        c.__exit__(None, None, None)


def test_a_phone_renter_with_transport_sim_never_dials():
    c, sim, twilio_api = _app(transport="sim")
    try:
        assert c.app.state.dispatcher.runner.phone is None
        _phone_brief(c)
        _finish(c)
        assert sorted(d.listing_id for d in sim.dials) == ["t:1", "t:2"] and twilio_api.created == []
    finally:
        c.__exit__(None, None, None)


def test_with_transport_stub_a_phone_renters_calls_stay_queued_and_a_judges_still_run_on_the_sim():
    c, sim, twilio_api = _app(transport="stub")
    try:
        sid = _phone_brief(c)
        _finish(c)
        assert sim.dials == [] and twilio_api.created == []
        assert {x["state"] for x in c.get(f"/api/sessions/{sid}").json()["calls"]} == {"queued"}
        _judge_brief_with_three_picks(c)
        _finish(c)
        assert len(sim.dials) == 3
    finally:
        c.__exit__(None, None, None)


async def test_a_sim_calls_lines_go_to_call_turns_for_captions():
    rows = []

    class FakeTurns:
        async def append(self, call_id, idx, speaker, text):
            await asyncio.sleep(0)
            rows.append((call_id, idx, speaker, text))

    db = type("DB", (), {"turns": FakeTurns()})()
    trace = SimTrace(call_id="c_sim0000001", listing_id="t:1", persona="linda-honest",
                     captions=Turns(db, "c_sim0000001"))
    trace.add(Turn("landlord", "Hi, Linda speaking."))
    trace.add(Turn("caller", "I'm an AI assistant calling for a renter."))
    trace.add(Turn("landlord", "  "))
    await trace.captions.flush()
    assert rows == [("c_sim0000001", 0, "landlord", "Hi, Linda speaking."),
                    ("c_sim0000001", 1, "agent", "I'm an AI assistant calling for a renter.")]
    assert len(trace.turns) == 3
