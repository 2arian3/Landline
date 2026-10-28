"""Listing calls over Twilio (A-02, outbound), with a fake Twilio and no network: the allowlist inside dial(),
every way a call ends going through finalize once, and the webhooks refusing what Twilio didn't sign."""
import asyncio
import html
import re
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from landline.app import create_app
from landline.config import Settings
from landline.contracts import CallOutcome, CallPlan, CallsRequested, Listing, SessionEnded, SessionStarted
from landline.db.catalogue import Catalogue
from landline.orchestrator import SessionRuntime
from landline.voice import callbacks, twilio
from landline.voice.dispatch import CallResult, Dial, Dispatcher
from landline.voice.outbound import (
    DISCLOSURE,
    RECORDED,
    NotAllowed,
    Outbound,
    OutboundCalls,
    TwilioRunner,
    no_caller,
    opening,
)

NOW = datetime(2026, 10, 24, 15, 0, tzinfo=UTC)   # Saturday 11:00 in Toronto
SID = "s_outb000001"
PUBLIC = "https://landline.test"
TOKEN = "test-token"
AGENT = "+16475550101"


def _settings(**kw) -> Settings:
    base = Settings(database_url=None, web_url="https://landline.example", transport="voice",
                    boson_api_key="test-key", twilio_account_sid="AC1", twilio_auth_token=TOKEN,
                    twilio_phone_number="+12892765257", public_url=PUBLIC, demo_agent_phones=(AGENT,),
                    higgs_max_sessions=3, listing_call_max_s=5, force_business_hours=True)
    return replace(base, **kw)


def _catalogue() -> Catalogue:
    return Catalogue([Listing(listing_id=f"t:{i}", source="t", source_id=str(i), url=f"https://example.com/{i}",
                              first_seen=NOW, last_seen=NOW, address=f"{i} Dial St", lat=43.65, lng=-79.38,
                              rent=2000, monthly_cost=2000, beds=1) for i in range(1, 4)], "test")


class FakeTwilio:
    """Twilio's REST API. Each new call plays `script(call_id)`, the webhooks Twilio would send. A live call
    switched to new TwiML lands in `updated` (answered with `update_status`); one put down, in `hung_up`."""

    def __init__(self, script=None, update_status=200):
        self.script = script
        self.update_status = update_status
        self.created: list[dict] = []
        self.updated: list[tuple[str, str]] = []
        self.hung_up: list[str] = []
        self._tasks: set[asyncio.Task] = set()

    async def handle(self, request: httpx.Request) -> httpx.Response:
        form = {k: v[0] if len(v) == 1 else v for k, v in parse_qs(request.content.decode()).items()}
        if request.url.path.endswith("/Calls.json"):
            self.created.append(form)
            if self.script:
                call_id = form["Url"].rsplit("/", 1)[1]
                task = asyncio.ensure_future(self.script(call_id))
                self._tasks.add(task)
            return httpx.Response(201, json={"sid": f"CA{len(self.created)}"})
        sid = request.url.path.rsplit("/", 1)[1].removesuffix(".json")
        if "Twiml" in form:
            self.updated.append((sid, form["Twiml"]))
            return httpx.Response(self.update_status, json={})
        self.hung_up.append(sid)
        return httpx.Response(200, json={})

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handle))


def _dial(to: str | None = AGENT, call_id: str = "c_test000001") -> Dial:
    plan = CallPlan(session_id=SID, listing_ids=["t:1"], created_at=NOW)
    return Dial(call_id=call_id, session_id=SID, listing_id="t:1", to=to, plan=plan,
                deadline=NOW + timedelta(minutes=5))


async def _nothing(_state):
    return None


# (a) nothing dials outside DEMO_AGENT_PHONES, even when dial() is called directly

@pytest.mark.parametrize("number", ["+14165550000", "", None, "not a number"])
async def test_dial_refuses_a_number_off_the_allowlist_even_when_called_directly(number):
    twilio_api = FakeTwilio()
    runner = TwilioRunner(_settings(), OutboundCalls(), client=twilio_api.client())
    with pytest.raises(NotAllowed):
        await runner.dial(_dial(to=number))
    assert twilio_api.created == []


async def test_place_with_a_number_off_the_allowlist_ends_blocked_without_dialling():
    twilio_api = FakeTwilio()
    runner = TwilioRunner(_settings(), OutboundCalls(), client=twilio_api.client())
    result = await runner.place(_dial(to="+14165550000"), _nothing)
    assert result.end_reason == "blocked" and twilio_api.created == []


async def test_dial_asks_twilio_for_amd_a_status_callback_and_our_own_urls():
    twilio_api = FakeTwilio()
    runner = TwilioRunner(_settings(), OutboundCalls(), client=twilio_api.client())
    assert await runner.dial(_dial(to="(647) 555-0101")) == "CA1"   # normalised, then allowed
    [form] = twilio_api.created
    assert form["To"] == AGENT and form["From"] == "+12892765257"
    assert form["Url"] == f"{PUBLIC}/twiml/outbound/c_test000001"
    assert form["StatusCallback"] == f"{PUBLIC}/twilio/outbound/status/c_test000001"
    assert form["MachineDetection"] == "DetectMessageEnd"   # a machine still waits for the beep
    # Detection runs alongside the call, so the Caller talks from the answer and the verdict comes later.
    assert form["AsyncAmd"] == "true" and form["AsyncAmdStatusCallbackMethod"] == "POST"
    assert form["AsyncAmdStatusCallback"] == f"{PUBLIC}/twilio/outbound/amd/c_test000001"
    # Nobody waits on it now, so its waits keep Twilio's defaults, which tell machines from people best.
    assert not {"MachineDetectionSilenceTimeout", "MachineDetectionSpeechEndThreshold",
                "MachineDetectionSpeechThreshold"} & set(form)
    assert set(form["StatusCallbackEvent"]) == {"initiated", "ringing", "answered", "completed"}


# (b) busy, no-answer, failed and machine-answered each end via finalize exactly once

async def _run_plan(script, n_listings=1, converse=no_caller):
    """A brief with a plan ends; the Dispatcher calls through the Twilio runner and fake Twilio plays
    `script` for each call. Returns the runtime, the CallEnded events, and fake Twilio."""
    rt = SessionRuntime(_catalogue())
    ended = []

    async def on_ended(event, _view):
        ended.append(event)
    rt.on("CallEnded", on_ended)
    settings = _settings()
    calls = OutboundCalls()
    twilio_api = FakeTwilio(lambda cid: script(calls, cid))
    calls.runner = runner = TwilioRunner(settings, calls, runtime=rt, client=twilio_api.client(),
                                         converse=converse)
    d = Dispatcher(rt, settings, runner=runner, clock=lambda: NOW)
    await rt.start(SessionStarted(session_id=SID, at=NOW, idem_key=f"{SID}:start", channel="phone",
                                  caller_phone="+14165550199"))
    await rt.emit(CallsRequested(session_id=SID, at=NOW, idem_key=f"{SID}:plan",
                                 listing_ids=[f"t:{i}" for i in range(1, n_listings + 1)]))
    await rt.emit(SessionEnded(session_id=SID, at=NOW, idem_key=f"{SID}:end", reason="hangup"))
    await d.idle()
    return rt, ended, twilio_api, calls


def _status(status):
    """Twilio rings, then posts this final status, then (as it does) posts it again."""
    async def script(calls: OutboundCalls, cid: str):
        await calls.set_state(cid, "ringing")
        for _ in range(2):
            if (reason := calls.end_reason(cid, status)) is not None:
                calls.finalize(cid, reason)
    return script


@pytest.mark.parametrize(("status", "reason"), [("busy", "busy"), ("no-answer", "no_answer"),
                                                ("failed", "failed"), ("completed", "no_answer")])
async def test_each_twilio_end_finalizes_the_call_once(status, reason):
    rt, ended, _, calls = await _run_plan(_status(status))
    assert [e.reason for e in ended] == [reason]
    assert rt.state(SID).outcomes["t:1"].end_reason == reason
    assert rt.get(SID).phase == "done" and calls.live == {}


async def test_a_machine_gets_the_voicemail_and_the_call_ends_once_as_voicemail():
    async def machine(calls: OutboundCalls, cid: str):
        await calls.set_state(cid, "ringing")
        twiml = calls.runner.answered_twiml(cid, "machine_end_beep", "wss://x")
        await calls.set_state(cid, "voicemail")
        assert "<Say>" in twiml and DISCLOSURE.split(".")[0] in twiml and "1 Dial St" in twiml
        assert "Hi, this is Landline." in twiml and "You can call us back at this number." in twiml   # A-13a
        assert "try again later" not in twiml
        for _ in range(2):
            calls.finalize(cid, calls.end_reason(cid, "completed"))
    rt, ended, twilio_api, calls = await _run_plan(machine)
    assert [e.reason for e in ended] == ["voicemail"]
    assert rt.state(SID).outcomes["t:1"].end_reason == "voicemail"


async def test_a_machine_found_mid_call_gets_the_voicemail_and_ends_voicemail_once():
    stream: asyncio.Queue = asyncio.Queue()   # what Twilio sends the Caller; None is the stream ending
    stopped = asyncio.Event()

    class Stream:
        async def receive_audio(self):
            return await stream.get()

    async def converse(dial, transport, hang_up):
        while await transport.receive_audio() is not None:
            pass
        stopped.set()   # the Caller stops, and closes its Higgs session, as the stream ends
        await hang_up("hangup")   # its talk is over, but the voicemail plays on
        return CallResult("hangup", CallOutcome(call_id=dial.call_id, listing_id=dial.listing_id,
                                                end_reason="hangup"))
    converse.takes_hang_up = True

    async def machine(calls: OutboundCalls, cid: str):
        await calls.set_state(cid, "ringing")
        assert "<Stream" in calls.runner.answered_twiml(cid, "", "wss://x")   # no AnsweredBy: talk at once
        call = calls.claim(cid)
        await calls.set_state(cid, "live")
        call.media.set_result(Stream())
        await calls.runner.detected(cid, "machine_end_beep")   # the beep, while the Caller talks
        await stream.put(None)                                   # the new TwiML ends the stream
        await stopped.wait()
        for _ in range(2):                                       # the voicemail hangs up
            calls.finalize(cid, calls.end_reason(cid, "completed"))
        await calls.runner.detected(cid, "machine_end_beep")   # a verdict after the end: nothing
    rt, ended, twilio_api, calls = await _run_plan(machine, converse=converse)
    assert [e.reason for e in ended] == ["voicemail"]
    assert rt.state(SID).outcomes["t:1"].end_reason == "voicemail"   # not what the Caller made of it
    [(sid, twiml)] = twilio_api.updated
    assert sid == "CA1" and "<Say>" in twiml and "<Hangup/>" in twiml and "1 Dial St" in twiml
    assert DISCLOSURE.split(".")[0] in twiml


async def test_a_voicemail_stays_one_when_the_stream_starts_late():
    said = []

    async def report(state):
        said.append(state)
    calls = OutboundCalls()
    calls.live["c_test000001"] = call = Outbound(dial=_dial(), report=report)
    for state in ("ringing", "voicemail", "live", "ringing"):
        await calls.set_state("c_test000001", state)
    assert said == ["ringing", "voicemail"] and call.state == "voicemail"


async def test_a_voicemail_twilio_wont_play_hangs_up_instead():
    twilio_api = FakeTwilio(update_status=500)
    calls = OutboundCalls()
    runner = TwilioRunner(_settings(), calls, client=twilio_api.client())
    calls.live["c_test000001"] = call = Outbound(dial=_dial(), report=_nothing, twilio_sid="CA1")
    await runner.detected("c_test000001", "machine_end_silence")
    assert len(twilio_api.updated) == 1 and twilio_api.hung_up == ["CA1"]
    assert call.state == "voicemail" and not call.done   # Twilio's final status still ends it, as voicemail
    assert calls.end_reason("c_test000001", "completed") == "voicemail"


async def test_a_call_that_never_ends_is_cut_at_the_deadline_and_hung_up():
    async def hang(calls, cid):
        await calls.set_state(cid, "ringing")   # rings forever: no final status ever comes
    rt = SessionRuntime(_catalogue())
    settings, calls = _settings(listing_call_max_s=0.1), OutboundCalls()
    twilio_api = FakeTwilio(lambda cid: hang(calls, cid))
    runner = TwilioRunner(settings, calls, runtime=rt, client=twilio_api.client())
    calls.runner = runner
    result = await asyncio.wait_for(asyncio.shield(_place_with_deadline(runner, 0.1)), 3)
    assert result == "timeout" and twilio_api.hung_up == ["CA1"] and calls.live == {}


async def test_a_call_cut_before_anyone_answers_leaves_no_task_behind():
    async def converse(dial, transport, higgs=None):
        return CallResult("done")

    async def prepare(dial):
        return object()
    converse.prepare = prepare   # a Caller that opens Higgs early, so place() waits for the answer
    before = asyncio.all_tasks()
    runner = TwilioRunner(_settings(), OutboundCalls(), client=FakeTwilio().client(), converse=converse)
    assert await _place_with_deadline(runner, 0.05) == "timeout"   # nobody answers, no status comes
    await asyncio.sleep(0)
    assert [t for t in asyncio.all_tasks() - before if not t.done()] == []


async def _place_with_deadline(runner, seconds):
    try:
        async with asyncio.timeout(seconds):
            await runner.place(_dial(), _nothing)
    except TimeoutError:
        return "timeout"
    return "returned"


async def test_a_conversation_ends_once_and_a_late_completed_status_changes_nothing():
    states = []

    async def report(state):
        states.append(state)

    calls = OutboundCalls()

    async def converse(dial, transport):
        return CallResult("done")

    async def answered(cid):
        await calls.set_state(cid, "ringing")
        call = calls.claim(cid)
        await calls.set_state(cid, "live")
        call.media.set_result(object())
    twilio_api = FakeTwilio(answered)
    runner = TwilioRunner(_settings(), calls, client=twilio_api.client(), converse=converse)
    result = await runner.place(_dial(), report)
    assert result.end_reason == "done" and states == ["ringing", "live"]
    assert calls.finalize("c_test000001", "hangup") is False   # Twilio's late "completed"


def test_the_opening_says_the_disclosure_first_and_keeps_the_instructions():
    text, start = opening("Ask about parking.")
    assert text.startswith("Ask about parking.") and f'exactly: "Hi, this is Landline. {DISCLOSURE}"' in text
    assert DISCLOSURE == "I'm an AI assistant calling for a renter."   # hard rule 6's words, whole
    # With the brief's own line (the address in it) and the audio stored, those are the first words.
    line = "Hi, this is Landline. I'm an AI assistant calling for a renter about 1 Dial St."
    named, _ = opening("Ask about parking.", recorded=True, first=line)
    assert f'exactly: "{line} {RECORDED}" Then ask if they have a minute.' in named
    # A quoted note, then a plain response.create: no response object, whose instructions would replace
    # the session's, disclosure and all (#129).
    assert start[0]["item"]["content"][0]["text"] == "[The call was just answered.]"
    assert start[-1] == {"type": "response.create"}
    assert all("response" not in event for event in start)


# The webhooks, through the app

class _App(TestClient):
    """Ends any listing call still open before shutdown, so a failed assertion can't hang the test."""

    def __exit__(self, *exc):
        calls = self.app.state.outbound
        for cid in list(calls.live):
            self.portal.call(lambda cid=cid: _finalize(calls, cid))
        return super().__exit__(*exc)


async def _finalize(calls, cid):
    calls.finalize(cid, "failed")


def _client(**kw) -> TestClient:
    return _App(create_app(_settings(**kw), _catalogue()))


def _post(c: TestClient, path: str, params: dict, token: str = TOKEN):
    sig = twilio.signature(token, f"{PUBLIC}{path}", params)
    return c.post(path, data=params, headers={"X-Twilio-Signature": sig})


def _place_in_app(c: TestClient, twilio_api: FakeTwilio, converse=None):
    """Starts a listing call inside the app's loop, as the Dispatcher would, with fake Twilio."""
    runner = c.app.state.outbound.runner
    runner.client = twilio_api.client()
    if converse:
        runner.converse = converse
    states: list[str] = []

    async def report(state):
        states.append(state)
    future = c.portal.start_task_soon(runner.place, _dial(), report)
    for _ in range(500):
        if twilio_api.created:
            break
        time.sleep(0.01)
    return future, states


@pytest.mark.parametrize("path", ["/twilio/outbound/status/c_test000001", "/twiml/outbound/c_test000001",
                                  "/twilio/outbound/amd/c_test000001"])
def test_an_outbound_webhook_with_a_bad_signature_is_refused(path):
    twilio_api = FakeTwilio()
    with _client() as c:
        future, states = _place_in_app(c, twilio_api)
        params = {"CallSid": "CA1", "CallStatus": "busy", "AnsweredBy": "human"}
        assert _post(c, path, params, token="wrong-token").status_code == 403
        assert c.post(path, data=params).status_code == 403              # no signature at all
        assert c.app.state.outbound.get("c_test000001") is not None      # nothing ended it
        assert _post(c, "/twilio/outbound/status/c_test000001", params).status_code == 204
        assert future.result(timeout=3).end_reason == "busy"


def test_a_signed_ringing_then_no_answer_ends_the_call_once():
    twilio_api = FakeTwilio()
    with _client() as c:
        future, states = _place_in_app(c, twilio_api)
        path = "/twilio/outbound/status/c_test000001"
        assert _post(c, path, {"CallSid": "CA1", "CallStatus": "ringing"}).status_code == 204
        for _ in range(2):
            assert _post(c, path, {"CallSid": "CA1", "CallStatus": "no-answer"}).status_code == 204
        assert future.result(timeout=3).end_reason == "no_answer" and states == ["ringing"]


def test_a_machine_answer_gets_the_voicemail_twiml():
    twilio_api = FakeTwilio()
    with _client() as c:
        future, states = _place_in_app(c, twilio_api)
        r = _post(c, "/twiml/outbound/c_test000001", {"CallSid": "CA1", "AnsweredBy": "machine_end_beep"})
        assert "<Say>" in r.text and "<Hangup/>" in r.text and "1 Dial St" in r.text
        _post(c, "/twilio/outbound/status/c_test000001", {"CallSid": "CA1", "CallStatus": "completed"})
        assert future.result(timeout=3).end_reason == "voicemail" and states == ["voicemail"]


def test_a_person_answers_the_stream_connects_and_the_caller_talks():
    heard = []

    async def converse(dial, transport):
        heard.append(await transport.receive_audio())
        await transport.send_audio("AAAA")
        return CallResult("done")

    twilio_api = FakeTwilio()
    with _client() as c:
        future, states = _place_in_app(c, twilio_api, converse)
        r = _post(c, "/twiml/outbound/c_test000001", {"CallSid": "CA1", "AnsweredBy": "human"})
        assert "<Stream" in r.text and '<Parameter name="call_id" value="c_test000001"/>' in r.text
        assert "wss://landline.test/twilio/outbound/media" in r.text
        with c.websocket_connect("/twilio/outbound/media") as ws:
            ws.send_json({"event": "connected"})
            ws.send_json({"event": "start", "streamSid": "MZ1",
                          "start": {"streamSid": "MZ1", "customParameters": {"call_id": "c_test000001"}}})
            ws.send_json({"event": "media", "media": {"payload": "/w=="}})
            assert ws.receive_json() == {"event": "media", "streamSid": "MZ1", "media": {"payload": "AAAA"}}
            assert future.result(timeout=3).end_reason == "done"
        assert heard == ["/w=="] and states == ["live"]
        assert twilio_api.hung_up == ["CA1"]   # the line is put down once the Caller is done


AMD = "/twilio/outbound/amd/c_test000001"
START = {"event": "start", "streamSid": "MZ1",
         "start": {"streamSid": "MZ1", "customParameters": {"call_id": "c_test000001"}}}


def _wait_for(check):
    for _ in range(300):
        if check():
            return
        time.sleep(0.01)
    raise AssertionError("timed out")


def test_the_caller_talks_from_the_answer_and_a_machine_found_later_gets_the_voicemail():
    stopped = []

    async def converse(dial, transport):
        await transport.send_audio("AAAA")   # straight away: nothing waited on machine detection
        while await transport.receive_audio() is not None:
            pass
        stopped.append(dial.call_id)
        return CallResult("hangup")

    twilio_api = FakeTwilio()
    with _client() as c:
        future, states = _place_in_app(c, twilio_api, converse)
        r = _post(c, "/twiml/outbound/c_test000001", {"CallSid": "CA1"})   # AsyncAmd: no AnsweredBy yet
        assert "<Stream" in r.text
        with c.websocket_connect("/twilio/outbound/media") as ws:
            ws.send_json(START)
            assert ws.receive_json()["media"]["payload"] == "AAAA"
            params = {"CallSid": "CA1", "AnsweredBy": "machine_end_beep", "MachineDetectionDuration": "4100"}
            assert _post(c, AMD, params).status_code == 204
            [(sid, twiml)] = twilio_api.updated
            assert sid == "CA1" and "<Say>" in twiml and "1 Dial St" in twiml
            ws.send_json({"event": "stop"})   # Twilio ends the stream as it switches to the voicemail
            _wait_for(lambda: stopped)
            assert not future.done() and twilio_api.hung_up == []   # the message plays out
            completed = {"CallSid": "CA1", "CallStatus": "completed"}
            for _ in range(2):
                _post(c, "/twilio/outbound/status/c_test000001", completed)
            result = future.result(timeout=3)
        assert result.end_reason == "voicemail" and result.outcome is None and states == ["live", "voicemail"]
        assert _post(c, AMD, params).status_code == 204 and len(twilio_api.updated) == 1   # after the end


def test_a_person_found_by_machine_detection_changes_nothing():
    async def converse(dial, transport):
        await transport.receive_audio()
        await transport.send_audio("AAAA")
        await transport.receive_audio()
        return CallResult("done")

    twilio_api = FakeTwilio()
    with _client() as c:
        future, states = _place_in_app(c, twilio_api, converse)
        _post(c, "/twiml/outbound/c_test000001", {"CallSid": "CA1"})
        with c.websocket_connect("/twilio/outbound/media") as ws:
            ws.send_json(START)
            ws.send_json({"event": "media", "media": {"payload": "/w=="}})
            assert ws.receive_json()["media"]["payload"] == "AAAA"
            for answered_by in ("human", "unknown"):
                assert _post(c, AMD, {"CallSid": "CA1", "AnsweredBy": answered_by}).status_code == 204
            ws.send_json({"event": "media", "media": {"payload": "/w=="}})
            assert future.result(timeout=3).end_reason == "done"
        assert twilio_api.updated == [] and states == ["live"]


def test_a_stream_for_an_unknown_call_is_refused():
    with _client() as c, c.websocket_connect("/twilio/outbound/media") as ws:
        ws.send_json({"event": "start", "streamSid": "MZ1",
                      "start": {"streamSid": "MZ1", "customParameters": {"call_id": "c_nobody00000"}}})
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_json()
        assert closed.value.code == 1008


def test_the_twilio_runner_is_installed_only_for_transport_voice():
    with _client() as c:
        assert c.app.state.dispatcher.runner.phone is c.app.state.outbound.runner is not None
    with _client(transport="stub") as c:
        assert c.app.state.dispatcher.runner.phone is None and c.app.state.outbound.runner is None
        r = _post(c, "/twiml/outbound/c_test000001", {"CallSid": "CA1", "AnsweredBy": "human"})
        assert r.text.endswith("<Response><Hangup/></Response>")


# A listing agent calling back (A-13b), through the webhooks. Which calls count, and the lookup itself, are
# in test_callbacks.py; here it's replaced by the listings it would find.

OURS = "+12892765257"
RENTER = "+14165550123"


def _session_in_app(c: TestClient) -> None:
    """A phone renter's session whose plan is done, with no calls placed (its calls end blocked)."""
    d = c.app.state.dispatcher
    runner, d.runner = d.runner, None

    async def brief():
        rt = c.app.state.runtime
        await rt.start(SessionStarted(session_id=SID, at=NOW, idem_key=f"{SID}:start", channel="phone",
                                      caller_phone=RENTER))
        await rt.emit(CallsRequested(session_id=SID, at=NOW, idem_key=f"{SID}:plan",
                                     listing_ids=["t:1", "t:2"], may_book=True))
        await rt.emit(SessionEnded(session_id=SID, at=NOW, idem_key=f"{SID}:end", reason="hangup"))
        await d.idle()
    c.portal.call(brief)
    d.runner = runner
    assert c.app.state.runtime.get(SID).phase == "done"


def _missed(monkeypatch, listings) -> None:
    """The lookup finds these listings' calls missed, for the agent's number only."""
    async def lookup(runtime, phone, now):
        return [callbacks.Missed(f"c_missed{lid[2:]}", SID, lid, address) for lid, address in listings
                if phone == AGENT]
    monkeypatch.setattr(callbacks, "lookup", lookup)


def test_a_listing_agent_calling_back_reaches_the_caller_for_that_listing(monkeypatch):
    talked = []

    async def converse(dial, transport):
        talked.append((dial.listing_id, dial.callback, dial.to))
        return CallResult("done", CallOutcome(call_id=dial.call_id, listing_id=dial.listing_id,
                                              end_reason="done"))

    twilio_api = FakeTwilio()
    with _client() as c:
        _session_in_app(c)
        _missed(monkeypatch, [("t:1", "1 Dial St")])
        runner = c.app.state.outbound.runner
        runner.client, runner.converse = twilio_api.client(), converse
        r = _post(c, "/twiml/renter", {"CallSid": "CA9", "From": AGENT, "To": OURS})
        assert "wss://landline.test/twilio/outbound/media" in r.text   # the Caller's stream
        call_id = re.search(r'name="call_id" value="([^"]+)"', r.text).group(1)
        with c.websocket_connect("/twilio/outbound/media") as ws:
            ws.send_json({"event": "connected"})
            ws.send_json({"event": "start", "streamSid": "MZ1",
                          "start": {"streamSid": "MZ1", "customParameters": {"call_id": call_id}}})
            _wait_for(lambda: twilio_api.hung_up)
        c.portal.call(c.app.state.dispatcher.idle)
        assert talked == [("t:1", True, AGENT)] and twilio_api.hung_up == ["CA9"]   # their call, put down
        assert twilio_api.created == []                                              # nothing was dialled
        back = next(x for x in c.app.state.runtime.get(SID).calls if x.call_id == call_id)
        assert (back.listing_id, back.state, back.outcome.end_reason) == ("t:1", "ended", "done")


def _action(r) -> str:
    return html.unescape(re.search(r'action="([^"]+)"', r.text).group(1))


def test_two_missed_listings_ask_which_and_code_picks_by_key_or_address(monkeypatch):
    with _client() as c:
        _session_in_app(c)
        _missed(monkeypatch, [("t:1", "1 Dial St"), ("t:2", "2 Dial St")])
        started = []

        async def callback(missed, phone, twilio_sid):
            started.append(missed.listing_id)
            return f"c_back00000{len(started)}"
        c.app.state.dispatcher.callback = callback
        params = {"CallSid": "CA9", "From": AGENT, "To": OURS}
        r = _post(c, "/twiml/renter", params)
        assert "<Gather" in r.text and started == []
        assert "I'm an AI assistant calling for a renter." in r.text
        assert "press 1 for 1 Dial St, 2 for 2 Dial St." in r.text
        action = _action(r)
        assert action == "/twiml/callback?try=1&calls=c_missed1,c_missed2"   # what was read out, signed
        r = _post(c, action, {**params, "Digits": "2"})
        assert started == ["t:2"] and 'value="c_back000001"' in r.text and "/twilio/outbound/media" in r.text
        _post(c, action, {**params, "SpeechResult": "The one at 1 Dial Street."})
        assert started == ["t:2", "t:1"]
        _missed(monkeypatch, [("t:2", "2 Dial St"), ("t:1", "1 Dial St")])   # found in another order now
        _post(c, action, {**params, "Digits": "1"})
        assert started == ["t:2", "t:1", "t:1"]   # 1 is still what they heard first
        r = _post(c, action, params)   # nothing pressed or said: asked once more
        assert "<Gather" in r.text and "Sorry, I didn't catch that." in r.text and "try=2" in _action(r)
        r = _post(c, _action(r), {**params, "Digits": "7"})   # still none: the Concierge, as for anyone
        assert "wss://landline.test/twilio/media" in r.text and len(started) == 3


def test_a_renter_and_a_failed_lookup_both_get_the_concierge(monkeypatch):
    with _client() as c:
        _session_in_app(c)
        r = _post(c, "/twiml/renter", {"CallSid": "CA1", "From": RENTER, "To": OURS})   # no database here
        assert "wss://landline.test/twilio/media" in r.text and "outbound" not in r.text

        async def broken(runtime, phone, now):
            raise ConnectionError("the database is down")
        monkeypatch.setattr(callbacks, "lookup", broken)
        r = _post(c, "/twiml/renter", {"CallSid": "CA2", "From": AGENT, "To": OURS})
        assert "wss://landline.test/twilio/media" in r.text and "outbound" not in r.text
        r = _post(c, "/twiml/callback?try=1", {"CallSid": "CA2", "From": AGENT, "To": OURS, "Digits": "1"})
        assert "wss://landline.test/twilio/media" in r.text   # mid-question too: the Concierge, never a guess


def test_a_callback_webhook_twilio_didnt_sign_is_refused():
    with _client() as c:
        params = {"CallSid": "CA1", "From": AGENT, "Digits": "1"}
        r = _post(c, "/twiml/callback?try=1", params, token="wrong")
        assert r.status_code == 403


def test_without_a_runner_that_answers_callbacks_no_call_waits_on_the_lookup(monkeypatch):
    looked = []

    async def lookup(runtime, phone, now):
        looked.append(phone)
        return []
    monkeypatch.setattr(callbacks, "lookup", lookup)
    with _client(transport="sim") as c:   # no phone runner: nothing could answer a callback
        r = _post(c, "/twiml/renter", {"CallSid": "CA1", "From": AGENT, "To": OURS})
        assert "wss://landline.test/twilio/media" in r.text and looked == []


async def test_a_callback_whose_stream_never_comes_ends_failed_and_is_put_down(monkeypatch):
    monkeypatch.setattr("landline.voice.outbound.STREAM_WITHIN_S", 0.05)
    calls, twilio_api = OutboundCalls(), FakeTwilio()
    runner = TwilioRunner(_settings(), calls, client=twilio_api.client())
    dial = replace(_dial(), callback=True, twilio_sid="CA5")
    result = await asyncio.wait_for(runner.place(dial, _nothing), 2)
    assert result.end_reason == "failed" and twilio_api.created == [] and twilio_api.hung_up == ["CA5"]
