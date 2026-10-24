"""The renter's call end to end, without a network, both ways in: the phone (TwiML webhook → Twilio media
stream → fake Higgs) and the browser (judge start → audio socket → fake Higgs)."""
import asyncio
import base64
import json
import logging
import re
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from landline.agents.concierge import tools
from landline.agents.concierge.prompt import (
    CALL_CONNECTED,
    FIRST_CALL,
    INSTRUCTIONS,
    LINK_SENT,
    RETURNING,
    WELCOME_BACK,
)
from landline.api.browser_audio import JUDGE_AUDIO_PATH
from landline.api.twilio import BUSY, NOT_READY
from landline.app import create_app
from landline.config import Settings
from landline.contracts import Listing
from landline.db.catalogue import Catalogue
from landline.voice import bridge, twilio
from landline.voice.calls import CLAIM_WITHIN_S, RenterCalls

PUBLIC = "https://landline.test"
TOKEN = "test-token"
T0 = datetime(2026, 10, 23, tzinfo=UTC)


# How every call starts (bridge.opening): a quoted note, then a bare response.create. Never a response
# object on response.create: its instructions replace the session's (#129).
STARTS = [{"type": "conversation.item.create", "item": {
    "type": "message", "role": "user", "content": [{"type": "input_text", "text": CALL_CONNECTED}]}},
    {"type": "response.create"}]


def _listing(i: int, rent: int, beds: float) -> Listing:
    return Listing(listing_id=f"t:{i}", source="t", source_id=str(i), url=f"https://example.com/{i}",
                   first_seen=T0, last_seen=T0, address=f"{i} Test St", lat=43.65, lng=-79.38,
                   rent=rent, monthly_cost=rent, beds=beds, sqft=700)


def _tool_call(name: str, arguments: dict, tool_call_id: str = "fc_1") -> dict:
    return {"type": "response.function_call_arguments.done", "call_id": tool_call_id, "name": name,
            "arguments": json.dumps(arguments)}


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
    catalogue = Catalogue([_listing(1, 2400, 1), _listing(2, 2900, 2), _listing(3, 3600, 2)], "test")
    return TestClient(create_app(replace(settings, **overrides), catalogue))


def _start(call_id: str, sid: str = "MZ1") -> dict:
    return {"event": "start", "streamSid": sid,
            "start": {"streamSid": sid, "callSid": "CA1", "customParameters": {"call_id": call_id}}}


def _hang_up_and_wait(c: TestClient, ws, session_id: str) -> None:
    """The judge hangs up, and the server finishes ending the call before the test goes on. Leaving the
    `with websocket_connect` block alone isn't enough: Starlette's TestClient sends the disconnect and then
    cancels the server's task at once, so the call could be cut off before it sees the hang-up."""
    ws.close(1000)
    deadline = time.monotonic() + 5
    while session_id in c.app.state.judges.open and time.monotonic() < deadline:   # freed last
        time.sleep(0.01)


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

def test_bridge_passes_audio_both_ways_and_ends_the_phone_session():
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
        assert [t["name"] for t in session["tools"]] == [t["name"] for t in tools.TOOLS]
        assert session["tool_choice"] == "auto"
        assert fake.sent == [
            *STARTS,
            {"type": "input_audio_buffer.append", "audio": "//8="},
        ]
        assert fake.closed
        assert call.call_id not in c.app.state.calls.live
        assert c.app.state.calls.recent[-1].end_reason == "hangup"
        assert c.get("/health").json()["higgs"] == "ok"
        # The call started a phone session in the runtime, and hanging up with no plan closed it.
        assert c.app.state.runtime.state(call.session_id).caller_phone == "+14165550100"
        assert c.app.state.runtime.get(call.session_id).phase == "closed"


def test_a_tool_call_changes_the_session_and_speaks_its_board():
    fake = FakeHiggs(script=[
        _tool_call("update_preferences", {"max_monthly": 3000, "min_beds": 2}),
        {"type": "response.output_audio.delta", "delta": "AAAA"},   # handled after the tool, in order
    ])
    with _client() as c:
        c.app.state.connect_higgs = fake.connect
        call = c.app.state.calls.open(caller="+14165550100")
        with c.websocket_connect("/twilio/media") as ws:
            ws.send_json(_start(call.call_id))
            assert ws.receive_json()["event"] == "media"
            view = c.app.state.runtime.get(call.session_id)
            ws.send_json({"event": "stop", "streamSid": "MZ1"})
        assert (view.preferences.max_monthly, view.preferences.min_beds) == (3000, 2)
        assert [b.listing_id for b in view.board] == ["t:2"]
        assert fake.sent[2:4] == [
            {"type": "conversation.item.create",
             "item": {"type": "function_call_output", "call_id": "fc_1", "output": view.agent_says}},
            {"type": "response.create"},
        ]


def test_a_line_in_another_script_answered_with_no_tool_is_read_and_saved(monkeypatch):
    """A-04: the Concierge answers a Korean line in Korean but calls no tool; code reads that line for its
    preferences in the background and saves them through update_preferences."""
    korean = "방 두 개짜리 아파트를 찾고 있고, 한 달에 3000달러까지 괜찮아요."

    class Reader:
        asked = []

        async def json(self, system, user, schema, name="result"):
            self.asked.append(user)
            return {"max_monthly": 3000, "min_beds": 2, "pets": [], "areas": [], "must_have": [],
                    "move_in": None, "occupants": None, "language": "ko"}

    reader = Reader()
    monkeypatch.setattr(bridge, "_READER", [reader])
    fake = FakeHiggs(script=[
        {"type": "input_audio_buffer.speech_started", "item_id": "i1", "audio_start_ms": 0},
        {"type": "input_audio_buffer.speech_stopped", "item_id": "i1", "audio_end_ms": 900},
        {"type": "response.created", "response": {"id": "r1"}},
        {"type": "conversation.item.input_audio_transcription.completed", "item_id": "i1",
         "transcript": korean},
        {"type": "response.done", "response": {"id": "r1", "status": "completed",
                                               "output": [{"type": "message"}]}},
        {"type": "response.output_audio.delta", "delta": "AAAA"},
    ])
    with _client() as c:
        c.app.state.connect_higgs = fake.connect
        call = c.app.state.calls.open(caller=None)
        with c.websocket_connect("/twilio/media") as ws:
            ws.send_json(_start(call.call_id))
            while ws.receive_json()["event"] != "media":
                pass
            deadline = time.monotonic() + 5
            while c.app.state.runtime.get(call.session_id).preferences.min_beds is None \
                    and time.monotonic() < deadline:
                time.sleep(0.01)
            ws.send_json({"event": "stop", "streamSid": "MZ1"})
        p = c.app.state.runtime.get(call.session_id).preferences
        assert (p.max_monthly, p.min_beds, p.language) == (3000, 2, "ko") and reader.asked == [korean]


def test_last_session_memory_is_not_appended_to_the_call():
    fake = FakeHiggs()
    remembered = "This renter last called yesterday. What you know: Wants up to $2,600 a month."
    with _client() as c:
        c.app.state.connect_higgs = fake.connect
        # Even if a runtime still has an opening, the call does not append it (#144).
        c.app.state.runtime.opening = lambda sid: remembered
        call = c.app.state.calls.open(caller="+14165550100")
        with c.websocket_connect("/twilio/media") as ws:
            ws.send_json(_start(call.call_id))
            ws.send_json({"event": "stop", "streamSid": "MZ1"})
            with pytest.raises(WebSocketDisconnect):
                ws.receive_json()
        instructions = fake.session["session"]["instructions"]
        assert instructions.endswith(FIRST_CALL)
        assert remembered not in instructions
        assert WELCOME_BACK not in instructions and RETURNING not in instructions
        # No instructions on response.create: they would replace the session's.
        assert fake.sent[:2] == [{"type": "conversation.item.create", "item": {
            "type": "message", "role": "user", "content": [{"type": "input_text", "text": CALL_CONNECTED}]}},
            {"type": "response.create"}]


def test_a_phone_call_s_greeting_never_waits_on_the_caller_s_profile(caplog):
    """The session starts without reading anything about the caller (#144, #149): even a profile lookup that
    would block for seconds is never awaited before the greeting, and the timing lines are logged."""
    caplog.set_level(logging.INFO, logger=bridge.__name__)

    class Stuck:
        async def recall(self, phone):
            await asyncio.sleep(5)

    fake = FakeHiggs(script=[{"type": "response.output_audio.delta", "delta": "AAAA"}])
    with _client() as c:
        c.app.state.connect_higgs = fake.connect
        c.app.state.runtime.profiles = Stuck()
        call = c.app.state.calls.open(caller="+14165550100")
        started = time.monotonic()
        with c.websocket_connect("/twilio/media") as ws:
            ws.send_json(_start(call.call_id))
            assert ws.receive_json()["event"] == "media"   # the greeting's audio is out
            assert time.monotonic() - started < 2
            ws.send_json({"event": "stop", "streamSid": "MZ1"})
    assert fake.sent   # the opening went to Higgs
    logged = [r.getMessage() for r in caplog.records]
    for line in ("stream started", ": Higgs ready, greeting sent", ": first audio out"):
        assert f"call {call.call_id}{' ' if line[0] != ':' else ''}{line}" in logged, line


def test_a_first_time_caller_opens_like_a_returning_one_with_no_response_object():
    instructions, start = bridge.opening(None)
    assert instructions == INSTRUCTIONS + FIRST_CALL and WELCOME_BACK not in instructions
    assert "How this call opens" in instructions and "this call is recorded" in instructions
    # No response object on response.create: its instructions replace the session's (#129).
    assert start == STARTS
    assert all("response" not in event for event in start)


class FakeTexter:
    """Stands in for voice.sms.Texter: records each text instead of calling Twilio."""
    ready = True

    def __init__(self):
        self.sent = []

    def send_soon(self, to, from_, body):
        self.sent.append((to, from_, body))

    async def close(self):
        pass


def _outputs(fake: FakeHiggs) -> list[str]:
    return [e["item"]["output"] for e in fake.sent if e["type"] == "conversation.item.create"]


def test_a_phone_call_texts_the_board_link_once_when_it_connects_whatever_the_tools():
    # The Concierge may only plan calls; the link is still texted once.
    fake = FakeHiggs(script=[
        _tool_call("call_listings", {}, "fc_1"),
        _tool_call("call_listings", {"may_book": True}, "fc_2"),
        {"type": "response.output_audio.delta", "delta": "AAAA"},   # both tools ran before this
    ])
    texter = FakeTexter()
    with _client() as c:
        c.app.state.connect_higgs, c.app.state.texter = fake.connect, texter
        call = c.app.state.calls.open(caller="+14165550100", called="+12892765257")
        with c.websocket_connect("/twilio/media") as ws:
            ws.send_json(_start(call.call_id))
            assert ws.receive_json()["event"] == "media"
            ws.send_json({"event": "stop", "streamSid": "MZ1"})
    assert texter.sent == [("+14165550100", "+12892765257",
                            bridge.LINK_TEXT.format(link=f"http://localhost:3000/s/{call.session_id}"))]
    # The Concierge is told in its instructions, so the tools' replies stay plain.
    assert fake.session["session"]["instructions"].endswith(LINK_SENT)
    items = [e["item"] for e in fake.sent if e["type"] == "conversation.item.create"]
    outputs = [i["output"] for i in items if i["type"] == "function_call_output"]
    assert len(outputs) == 2 and all("texted_link" not in out for out in outputs)


def test_a_browser_call_is_never_texted():
    fake = FakeHiggs(script=[
        _tool_call("update_preferences", {"max_monthly": 3000}),
        {"type": "response.output_audio.delta", "delta": base64.b64encode(b"\0\0").decode()},
    ])
    texter = FakeTexter()
    with _client(judge_audio_ready=True) as c:
        c.app.state.connect_higgs, c.app.state.texter = fake.connect, texter
        start = c.post("/api/judge/start").json()
        with c.websocket_connect(start["ws_url"]) as ws:
            ws.receive_bytes()
            _hang_up_and_wait(c, ws, start["session_id"])
    assert texter.sent == [] and LINK_SENT not in fake.session["session"]["instructions"]


def test_twiml_remembers_the_number_the_renter_dialled():
    with _client() as c:
        call_id = re.search(r'value="(c_[\w-]{10})"', _ring(c).text).group(1)
        assert c.app.state.calls.live[call_id].called == "+16475550199"


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
            _hang_up_and_wait(c, ws, start["session_id"])
        session = fake.session["session"]
        pcm = {"type": "audio/pcm", "rate": 24000}
        assert session["audio"]["input"]["format"] == pcm and session["audio"]["output"]["format"] == pcm
        assert fake.sent == [
            *STARTS,
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


def test_end_session_hangs_up_once_the_goodbye_has_played(monkeypatch):
    monkeypatch.setattr(bridge, "HANG_UP_GRACE_S", 0.01)
    goodbye = base64.b64encode(b"\x00\x00" * 2400).decode()   # 0.1 s of 24 kHz PCM16
    fake = FakeHiggs(script=[
        {"type": "response.output_audio.delta", "delta": goodbye},
        _tool_call("end_session", {}, "fc_bye"),
        {"type": "response.done"},
    ])
    with _client(judge_audio_ready=True) as c:
        c.app.state.connect_higgs = fake.connect
        start = c.post("/api/judge/start").json()
        with c.websocket_connect(start["ws_url"]) as ws:
            assert len(ws.receive_bytes()) == 4800
            with pytest.raises(WebSocketDisconnect):   # the server hangs up; the judge didn't
                ws.receive_bytes()
        assert c.app.state.calls.recent[-1].end_reason == "end_session"
        assert c.app.state.runtime.get(start["session_id"]).phase == "closed"
        # The goodbye was already said: no new response after end_session's answer.
        assert fake.sent[-1] == {"type": "conversation.item.create", "item": {
            "type": "function_call_output", "call_id": "fc_bye", "output": "Goodbye."}}


def test_end_session_with_nothing_said_asks_for_a_goodbye_before_hanging_up(monkeypatch):
    monkeypatch.setattr(bridge, "HANG_UP_GRACE_S", 0.01)
    goodbye = base64.b64encode(b"\x00\x00" * 2400).decode()
    fake = FakeHiggs(script=[
        {"type": "response.created", "response": {"id": "r1"}},
        {**_tool_call("end_session", {}, "fc_bye"), "response_id": "r1"},
        {"type": "response.done", "response": {"id": "r1", "output": [{"type": "function_call"}]}},
        {"type": "response.created", "response": {"id": "r2"}},
        {"type": "response.output_audio.delta", "delta": goodbye},
        {"type": "response.done", "response": {"id": "r2", "output": [{"type": "message"}]}},
    ])
    with _client(judge_audio_ready=True) as c:
        c.app.state.connect_higgs = fake.connect
        start = c.post("/api/judge/start").json()
        with c.websocket_connect(start["ws_url"]) as ws:
            assert len(ws.receive_bytes()) == 4800   # the goodbye played before the hang-up
            with pytest.raises(WebSocketDisconnect):
                ws.receive_bytes()
        assert c.app.state.calls.recent[-1].end_reason == "end_session"
        # The goodbye is asked for in the tool's answer, so the Concierge keeps its own instructions.
        assert fake.sent[-2:] == [{"type": "conversation.item.create", "item": {
            "type": "function_call_output", "call_id": "fc_bye", "output": tools.SAY_GOODBYE}},
            {"type": "response.create"}]


class SpyLog:
    """Stands in for the runtime's CallLog (orchestrator/calllog.py): keeps what the bridge reports."""

    def __init__(self):
        self.log = []

    def started(self, call_id, session_id, transport, deadline_at=None):
        self.log.append(("started", transport, deadline_at))

    def said(self, call_id, speaker, text, *, start_ms=None, end_ms=None):
        self.log.append(("said", speaker, text, start_ms, end_ms))
        return sum(1 for e in self.log if e[0] == "said") - 1

    def placed(self, call_id, idx, start_ms, end_ms):
        self.log.append(("placed", idx, start_ms, end_ms))

    def audio(self, call_id, track, start_ms, audio, *, fmt, rate):
        self.log.append(("audio", track, start_ms, len(audio), fmt, rate))

    def ended(self, call_id, reason):
        self.log.append(("ended", reason))

    async def flushed(self, call_id):
        """The bridge waits here until the call's writes have landed. This spy's already have."""

    def of(self, kind):
        return [e for e in self.log if e[0] == kind]


def _recorded_call(c, spy) -> None:
    """A phone call: the renter says a line, the Concierge answers it, the renter sends 40 ms and hangs up."""
    c.app.state.runtime.calls = spy
    call = c.app.state.calls.open(caller="+14165550100")
    with c.websocket_connect("/twilio/media") as ws:
        ws.send_json(_start(call.call_id))
        assert ws.receive_json()["event"] == "clear"    # speech_started
        assert ws.receive_json()["event"] == "media"    # the answer's 20 ms
        time.sleep(0.05)                                # it has played
        for _ in range(2):
            ws.send_json({"event": "media", "streamSid": "MZ1",
                          "media": {"payload": base64.b64encode(b"\xff" * 160).decode()}})
        ws.send_json({"event": "stop", "streamSid": "MZ1"})
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()


RECORDED = [
    {"type": "input_audio_buffer.speech_started", "item_id": "u1", "audio_start_ms": 0},
    {"type": "input_audio_buffer.speech_stopped", "item_id": "u1", "audio_end_ms": 40},
    {"type": "conversation.item.input_audio_transcription.completed", "item_id": "u1",
     "transcript": "A one-bedroom."},
    {"type": "response.output_audio.delta", "item_id": "a1",
     "delta": base64.b64encode(b"\x7f" * 160).decode()},
    {"type": "response.output_audio_transcript.done", "item_id": "a1", "transcript": "Got it."},
]


def test_a_call_is_recorded_with_each_line_at_its_place_in_the_audio(monkeypatch):
    monkeypatch.setattr(bridge, "KEEP_AUDIO_EVERY_S", 0.01)   # store during the call too, not only at the end
    spy = SpyLog()
    with _client() as c:
        c.app.state.connect_higgs = FakeHiggs(script=RECORDED).connect
        _recorded_call(c, spy)
    [(_, transport, deadline)] = spy.of("started")
    assert transport == "voice" and deadline > datetime.now(UTC) + timedelta(seconds=800)   # 15 minutes
    renter, agent = spy.of("said")
    assert renter == ("said", "renter", "A one-bedroom.", 0, 40)          # Higgs's own numbers
    assert agent[1:3] == ("agent", "Got it.") and agent[4] - agent[3] == 20   # 160 bytes of 8 kHz
    audio = spy.of("audio")
    assert {(fmt, rate) for *_, fmt, rate in audio} == {("pcmu", 8000)}
    heard = [e for e in audio if e[1] == "renter"]
    assert sum(e[3] for e in heard) == 320 and heard[0][2] == 0   # 40 ms, from the start of the call
    said = [(e[2], e[3]) for e in audio if e[1] == "agent"]   # maybe split across stores, never apart
    assert said[0][0] == agent[3] and sum(n for _, n in said) == 160   # from where its line says it starts
    assert all(b[0] == a[0] + a[1] // 8 for a, b in zip(said, said[1:], strict=False))
    assert spy.of("placed") == []                   # it played to the end
    assert spy.log[-1] == ("ended", "hangup")       # everything is stored before the call ends


def test_with_recording_off_the_lines_keep_their_place_but_no_audio_is_stored():
    spy = SpyLog()
    with _client(record_audio=False) as c:
        c.app.state.connect_higgs = FakeHiggs(script=RECORDED).connect
        _recorded_call(c, spy)
    assert spy.of("audio") == []
    assert spy.of("said")[0] == ("said", "renter", "A one-bedroom.", 0, 40)


def test_playback_clock_counts_audio_sent_and_resets_on_barge_in():
    now = [10.0]
    clock = bridge.Playback(48000, clock=lambda: now[0])
    clock.sent(base64.b64encode(b"\x00" * 48000).decode())   # one second of 24 kHz PCM16
    clock.sent(base64.b64encode(b"\x00" * 24000).decode())
    assert clock.remaining() == pytest.approx(1.5)
    now[0] += 1.0
    assert clock.remaining() == pytest.approx(0.5)
    clock.clear()
    assert clock.remaining() == 0


def test_an_open_socket_holds_its_judge_line_past_the_connect_window():
    spoken = base64.b64encode(b"\x01\x00").decode()
    fake = FakeHiggs(script=[{"type": "response.output_audio.delta", "delta": spoken}])
    with _client(judge_audio_ready=True, higgs_max_sessions=1) as c:
        c.app.state.connect_higgs = fake.connect
        now = [0.0]
        c.app.state.judges.clock = lambda: now[0]
        start = c.post("/api/judge/start").json()
        now[0] = 5.0
        with c.websocket_connect(start["ws_url"]) as ws:
            ws.receive_bytes()   # the socket is up, so it holds the line
            now[0] = 60.0        # well past the connect window
            assert c.post("/api/judge/start").json()["reason"] == "busy"


def test_a_socket_after_the_connect_window_is_refused():
    fake = FakeHiggs()
    with _client(judge_audio_ready=True, higgs_max_sessions=1) as c:
        c.app.state.connect_higgs = fake.connect
        now = [0.0]
        c.app.state.judges.clock = lambda: now[0]
        start = c.post("/api/judge/start").json()
        now[0] = 16.0   # the line freed at 15 s and may belong to another judge by now
        with c.websocket_connect(start["ws_url"]) as ws:
            with pytest.raises(WebSocketDisconnect) as closed:
                ws.receive_bytes()
        assert closed.value.code == 1008 and fake.session is None
        assert start["session_id"] not in c.app.state.judges.open


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
