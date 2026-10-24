"""The renter's call end to end, without a network, both ways in: the phone (TwiML webhook → Twilio media
stream → fake Higgs) and the browser (judge start → audio socket → fake Higgs)."""
import asyncio
import base64
import re
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from landline.agents.concierge.prompt import GREETING
from landline.api.browser_audio import JUDGE_AUDIO_PATH
from landline.api.twilio import BUSY, NOT_READY
from landline.app import create_app
from landline.config import Settings
from landline.db.catalogue import Catalogue
from landline.voice import twilio
from landline.voice.calls import CLAIM_WITHIN_S, RenterCalls

PUBLIC = "https://landline.test"
TOKEN = "test-token"


class FakeHiggs:
    """Stands in for Higgs: keeps the session.update and everything the bridge sends, plays a script of
    events, then stays open until the bridge closes it."""

    def __init__(self, script=(), fail=False):
        self.script = list(script)
        self.fail = fail
        self.session = None
        self.sent = []
        self.closed = False
        self._done = None

    async def connect(self, api_key, session):
        if self.fail:
            raise RuntimeError("Higgs is down")
        self.session = session
        self._done = asyncio.Event()
        return self

    async def send(self, event):
        self.sent.append(event)

    async def events(self):
        for event in self.script:
            yield event
        await self._done.wait()

    async def close(self):
        self.closed = True
        self._done.set()


def _client(**overrides) -> TestClient:
    settings = Settings(database_url=None, web_url="http://localhost:3000", transport="stub",
                        boson_api_key="test-key", twilio_auth_token=TOKEN, public_url=PUBLIC)
    return TestClient(create_app(replace(settings, **overrides), Catalogue([], "test")))


def _start(call_id: str, sid: str = "MZ1") -> dict:
    return {"event": "start", "streamSid": sid,
            "start": {"streamSid": sid, "callSid": "CA1", "customParameters": {"call_id": call_id}}}


def _ring(c: TestClient, params: dict | None = None, token: str = TOKEN):
    params = params or {"CallSid": "CA1", "From": "+14165550100", "To": "+16475550199"}
    sig = twilio.signature(token, f"{PUBLIC}/twiml/renter", params)
    return c.post("/twiml/renter", data=params, headers={"X-Twilio-Signature": sig})


# ── TwiML webhook ───────────────────────────────────────────────────────────

def test_twiml_hands_the_call_to_the_media_stream_with_a_minted_call_id():
    with _client() as c:
        r = _ring(c)
        assert r.status_code == 200 and r.headers["content-type"].startswith("application/xml")
        assert '<Stream url="wss://landline.test/twilio/media">' in r.text
        call_id = re.search(r'<Parameter name="call_id" value="(c_[\w-]{10})"/>', r.text).group(1)
        call = c.app.state.calls.live[call_id]
        assert call.caller == "+14165550100" and re.fullmatch(r"s_[\w-]{10}", call.session_id)


def test_twiml_refuses_requests_twilio_did_not_sign():
    with _client() as c:
        assert _ring(c, token="wrong").status_code == 403
        assert c.post("/twiml/renter", data={"From": "+1"}).status_code == 403
        assert c.app.state.calls.live == {}
    with _client(twilio_auth_token=None) as c:
        assert _ring(c).status_code == 403


def test_twiml_says_so_when_higgs_is_not_configured_or_lines_are_busy():
    with _client(boson_api_key=None) as c:
        r = _ring(c)
        assert NOT_READY in r.text and "<Hangup/>" in r.text
    with _client(higgs_max_sessions=1) as c:
        assert "<Stream" in _ring(c).text
        r = _ring(c)
        assert BUSY in r.text and "<Hangup/>" in r.text


# ── media stream ↔ Higgs ────────────────────────────────────────────────────

def test_bridge_passes_audio_both_ways_with_no_tools():
    fake = FakeHiggs(script=[
        {"type": "response.output_audio.delta", "delta": "AAAA"},
        {"type": "input_audio_buffer.speech_started"},
    ])
    with _client() as c:
        c.app.state.connect_higgs = fake.connect
        call = c.app.state.calls.open(caller="+14165550100")
        with c.websocket_connect("/twilio/media") as ws:
            ws.send_json({"event": "connected", "protocol": "Call"})
            ws.send_json(_start(call.call_id))
            assert ws.receive_json() == {"event": "media", "streamSid": "MZ1", "media": {"payload": "AAAA"}}
            assert ws.receive_json() == {"event": "clear", "streamSid": "MZ1"}
            ws.send_json({"event": "media", "streamSid": "MZ1",
                          "media": {"track": "inbound", "payload": "//8="}})
            ws.send_json({"event": "stop", "streamSid": "MZ1"})
            with pytest.raises(WebSocketDisconnect):
                ws.receive_json()

        session = fake.session["session"]
        assert session["audio"]["input"]["format"] == {"type": "audio/pcmu"}
        assert session["audio"]["output"]["format"] == {"type": "audio/pcmu"}
        assert (session["tools"], session["tool_choice"]) == ([], "none")   # the Concierge only talks for now
        assert fake.sent == [
            {"type": "response.create", "response": {"instructions": GREETING}},
            {"type": "input_audio_buffer.append", "audio": "//8="},
        ]
        assert fake.closed
        assert call.call_id not in c.app.state.calls.live
        assert c.app.state.calls.recent[-1].end_reason == "hangup"
        assert c.get("/health").json()["higgs"] == "ok"


def test_bridge_refuses_a_stream_with_an_unknown_call_id():
    fake = FakeHiggs()
    with _client() as c:
        c.app.state.connect_higgs = fake.connect
        with c.websocket_connect("/twilio/media") as ws:
            ws.send_json(_start("c_notminted"))
            with pytest.raises(WebSocketDisconnect) as closed:
                ws.receive_json()
        assert closed.value.code == 1008
        assert fake.session is None


def test_bridge_ends_the_call_at_its_deadline():
    fake = FakeHiggs()
    with _client(renter_call_max_s=0.2) as c:
        c.app.state.connect_higgs = fake.connect
        call = c.app.state.calls.open(caller=None)
        with c.websocket_connect("/twilio/media") as ws:
            ws.send_json(_start(call.call_id))
            with pytest.raises(WebSocketDisconnect):
                ws.receive_json()
        assert c.app.state.calls.recent[-1].end_reason == "deadline"
        assert fake.closed and c.app.state.calls.live == {}


def test_bridge_frees_the_line_when_higgs_fails():
    with _client() as c:
        c.app.state.connect_higgs = FakeHiggs(fail=True).connect
        call = c.app.state.calls.open(caller=None)
        with c.websocket_connect("/twilio/media") as ws:
            ws.send_json(_start(call.call_id))
            with pytest.raises(WebSocketDisconnect):
                ws.receive_json()
        assert c.app.state.calls.recent[-1].end_reason == "higgs_error"
        assert c.get("/health").json()["higgs"].startswith("error")


# ── browser audio socket ↔ Higgs ────────────────────────────────────────────

def test_browser_call_streams_pcm_both_ways_and_frees_the_judge_line():
    spoken = b"\x01\x00\x02\x00"   # PCM16 the Concierge says
    fake = FakeHiggs(script=[
        {"type": "response.output_audio.delta", "delta": base64.b64encode(spoken).decode()},
        {"type": "input_audio_buffer.speech_started"},
    ])
    with _client(judge_audio_ready=True) as c:
        c.app.state.connect_higgs = fake.connect
        start = c.post("/api/judge/start").json()
        with c.websocket_connect(start["ws_url"]) as ws:
            assert ws.receive_bytes() == spoken
            assert ws.receive_json() == {"type": "clear"}
            ws.send_bytes(b"\x03\x00\x04\x00")   # the judge's microphone
        session = fake.session["session"]
        pcm = {"type": "audio/pcm", "rate": 24000}
        assert session["audio"]["input"]["format"] == pcm and session["audio"]["output"]["format"] == pcm
        assert fake.sent == [
            {"type": "response.create", "response": {"instructions": GREETING}},
            {"type": "input_audio_buffer.append", "audio": base64.b64encode(b"\x03\x00\x04\x00").decode()},
        ]
        ended = c.app.state.calls.recent[-1]
        assert (ended.session_id, ended.end_reason) == (start["session_id"], "hangup")
        assert start["session_id"] not in c.app.state.judges.open and fake.closed


def test_browser_socket_refuses_a_session_without_a_judge_line():
    fake = FakeHiggs()
    with _client(judge_audio_ready=True) as c:
        c.app.state.connect_higgs = fake.connect
        with c.websocket_connect("/api/judge/s_notstarted/audio") as ws:
            with pytest.raises(WebSocketDisconnect) as closed:
                ws.receive_bytes()
        assert closed.value.code == 1008 and fake.session is None


def test_judge_start_hands_out_the_socket_this_route_serves():
    assert Settings(database_url=None, web_url="", transport="stub").judge_ws_path == JUDGE_AUDIO_PATH


# ── pieces ──────────────────────────────────────────────────────────────────

def test_a_call_is_claimed_once_and_ends_once():
    now = [0.0]
    calls = RenterCalls(cap=2, clock=lambda: now[0])
    a = calls.open(caller="+1")
    assert calls.claim(a.call_id) is a and calls.claim(a.call_id) is None
    assert calls.claim("c_unknown") is None
    assert calls.finalize(a.call_id, "hangup") is True
    assert calls.finalize(a.call_id, "deadline") is False
    assert a.end_reason == "hangup"


def test_lines_are_capped_and_unclaimed_calls_free_their_line():
    now = [0.0]
    calls = RenterCalls(cap=1, clock=lambda: now[0])
    first = calls.open(caller=None)
    assert calls.open(caller=None) is None
    now[0] = CLAIM_WITHIN_S + 1
    assert calls.open(caller=None) is not None
    assert first.end_reason == "never_connected"


def test_signature_round_trip_and_tampering():
    url, params = f"{PUBLIC}/twiml/renter", {"From": "+14165550100", "CallSid": "CA1"}
    sig = twilio.signature(TOKEN, url, params)
    assert twilio.valid_signature(TOKEN, url, params, sig)
    assert not twilio.valid_signature(TOKEN, url, {**params, "From": "+19995550000"}, sig)
    assert not twilio.valid_signature(TOKEN, url, params, "")
