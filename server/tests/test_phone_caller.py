"""The Caller (A-05) on a phone line, end to end with no network: the Dispatcher places the call through the
Twilio runner (fake Twilio), a person answers, and the real Caller tools run on a fake Higgs over a fake
media stream (voice/phone_caller.py)."""
import asyncio
import json
import logging
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import httpx

from landline.config import Settings
from landline.contracts import CallPlan, CallsRequested, Listing, SessionEnded, SessionStarted
from landline.db.catalogue import Catalogue
from landline.orchestrator import SessionRuntime
from landline.voice import phone_caller
from landline.voice.dispatch import Dial, Dispatcher
from landline.voice.outbound import DISCLOSURE, OutboundCalls, TwilioRunner
from landline.voice.phone_caller import PhoneCaller

NOW = datetime(2026, 10, 24, 15, 0, tzinfo=UTC)   # Saturday 11:00 in Toronto
SID = "s_phone00001"
AGENT = "+16475550101"
HEARD = "Yes, it's still available. Parking is 150 a month."


class FakeHiggs:
    """Plays a script of Higgs events once the call has started, then stays open."""

    def __init__(self, script):
        self.script, self.sent, self.session, self.closed = script, [], None, False

    async def connect(self, key, session):
        self.session = session
        return self

    async def send(self, event):
        self.sent.append(event)

    async def events(self):
        for event in self.script:
            await asyncio.sleep(0)
            yield event
        await asyncio.sleep(60)

    async def close(self):
        self.closed = True


class FakeLine:
    """The Twilio media stream: plays `audio` in, then stays quiet, or hangs up after `hang_up_after`."""

    def __init__(self, audio=(), hang_up_after=None):
        self.audio, self.hang_up_after = list(audio), hang_up_after
        self.played, self.cleared = [], 0

    async def receive_audio(self):
        if self.audio:
            return self.audio.pop(0)
        await asyncio.sleep(60 if self.hang_up_after is None else self.hang_up_after)
        return None

    async def send_audio(self, b64):
        self.played.append(b64)

    async def send_clear(self):
        self.cleared += 1


class FakeTwilio:
    """Places the call; a person answers and the media stream connects with `line`."""

    def __init__(self, calls, line):
        self.calls, self.line, self.hung_up = calls, line, []

    async def handle(self, request):
        if request.url.path.endswith("/Calls.json"):
            cid = request.content.decode().split("twiml%2Foutbound%2F", 1)[1].split("&", 1)[0]
            asyncio.ensure_future(self.answer(cid))
            return httpx.Response(201, json={"sid": "CA1"})
        self.hung_up.append("CA1")
        return httpx.Response(200, json={})

    async def answer(self, cid):
        await self.calls.set_state(cid, "ringing")
        call = self.calls.claim(cid)
        await self.calls.set_state(cid, "live")
        call.media.set_result(self.line)


def _settings(**kw):
    return replace(Settings(database_url=None, web_url="https://landline.example", transport="voice",
                            boson_api_key="k", twilio_account_sid="AC1", twilio_auth_token="t",
                            twilio_phone_number="+12892765257", public_url="https://landline.test",
                            demo_agent_phones=(AGENT,), higgs_max_sessions=3, listing_call_max_s=10,
                            force_business_hours=True), **kw)


def _runtime():
    x = Listing(listing_id="t:1", source="t", source_id="1", url="https://example.com/1", first_seen=NOW,
                last_seen=NOW, address="1 Dial St", lat=43.65, lng=-79.38, rent=2000, monthly_cost=2000,
                beds=1)
    return SessionRuntime(Catalogue([x], "test"))


def _tool(name, args, fc):
    return {"type": "response.function_call_arguments.done", "name": name, "call_id": fc,
            "arguments": json.dumps(args), "response_id": f"r_{fc}"}


async def _call(script, line):
    """One brief with one picked place, ended; its call runs to the end. Returns the runtime, the
    CallEnded events and fake Higgs."""
    rt, settings, calls = _runtime(), _settings(), OutboundCalls()
    ended = []

    async def on_ended(event, _view):
        ended.append(event)
    rt.on("CallEnded", on_ended)
    higgs, twilio_api = FakeHiggs(script), FakeTwilio(calls, line)
    runner = TwilioRunner(settings, calls, runtime=rt, converse=PhoneCaller(rt, higgs.connect, "k",
                                                                             clock=lambda: NOW),
                          client=httpx.AsyncClient(transport=httpx.MockTransport(twilio_api.handle)))
    d = Dispatcher(rt, settings, runner=runner, clock=lambda: NOW)
    await rt.start(SessionStarted(session_id=SID, at=NOW, idem_key=f"{SID}:start", channel="phone",
                                  caller_phone="+14165550199"))
    await rt.emit(CallsRequested(session_id=SID, at=NOW, idem_key=f"{SID}:plan", listing_ids=["t:1"]))
    await rt.emit(SessionEnded(session_id=SID, at=NOW, idem_key=f"{SID}:end", reason="hangup"))
    await asyncio.wait_for(d.idle(), 10)
    return rt, ended, higgs, twilio_api


async def test_a_phone_call_records_what_the_agent_said_and_ends_once_after_the_goodbye(monkeypatch, caplog):
    monkeypatch.setattr(phone_caller, "HANG_UP_GRACE_S", 0)
    caplog.set_level(logging.INFO, logger=phone_caller.__name__)
    script = [
        {"type": "response.created"},
        {"type": "response.output_audio.delta", "delta": "AAAA"},
        {"type": "response.output_audio_transcript.done", "transcript": DISCLOSURE},
        {"type": "response.done", "response": {"id": "r1", "output": [{"type": "message"}]}},
        {"type": "input_audio_buffer.speech_started"},
        {"type": "conversation.item.input_audio_transcription.completed", "transcript": HEARD},
        {"type": "response.created"},
        _tool("record_fact", {"field": "parking_cost", "value": "$150 a month",
                               "quote": "Parking is 150 a month"}, "fc1"),
        # never said, so not recorded
        _tool("record_fact", {"field": "pets", "value": "allowed", "quote": "Dogs are fine"}, "fc2"),
        {"type": "response.done", "response": {"id": "r2", "output": [{"type": "function_call"}]}},
        {"type": "response.created"},
        {"type": "response.output_audio.delta", "delta": "BBBB"},
        _tool("end_call", {"reason": "refused"}, "fc3"),
        {"type": "response.done", "response": {"id": "r3", "output": [{"type": "function_call"}]}},
    ]
    rt, ended, higgs, twilio_api = await _call(script, FakeLine(["/w=="]))
    assert [e.reason for e in ended] == ["refused"]                       # one end, the Caller's
    out = rt.state(SID).outcomes["t:1"]
    assert [(f.field, f.quote) for f in out.facts] == [("parking_cost", "Parking is 150 a month")]
    assert higgs.session["session"]["audio"]["input"]["format"] == {"type": "audio/pcmu"}
    assert any(t["name"] == "record_fact" for t in higgs.session["session"]["tools"])
    # The disclosure is in the session instructions, and no response.create carries a response object.
    assert f'exactly: "{DISCLOSURE}"' in higgs.session["session"]["instructions"]
    assert [e["type"] for e in higgs.sent[:2]] == ["conversation.item.create", "response.create"]
    assert all("response" not in e for e in higgs.sent if e["type"] == "response.create")
    assert {"type": "input_audio_buffer.append", "audio": "/w=="} in higgs.sent
    assert higgs.closed and twilio_api.hung_up == ["CA1"]                 # Twilio's line is put down too
    assert rt.get(SID).phase == "done"
    # The steps after a pickup are in the log, in order, the first audio once (#148).
    said = [r.getMessage().split(": ")[-1] for r in caplog.records]
    assert [s for s in said if s in ("Higgs ready, opening sent", "first audio out")] == [
        "Higgs ready, opening sent", "first audio out"]


async def test_the_agent_hanging_up_ends_the_call_with_what_was_heard():
    script = [{"type": "conversation.item.input_audio_transcription.completed", "transcript": HEARD},
              _tool("record_fact", {"field": "available", "value": "yes", "quote": "it's still available"},
                    "fc1")]
    rt, ended, higgs, _ = await _call(script, FakeLine(hang_up_after=0.1))
    assert [e.reason for e in ended] == ["hangup"]
    assert [f.field for f in rt.state(SID).outcomes["t:1"].facts] == ["available"]


async def test_a_call_still_talking_at_its_deadline_returns_its_outcome_first():
    rt, calls = _runtime(), OutboundCalls()
    higgs = FakeHiggs([{"type": "conversation.item.input_audio_transcription.completed",
                        "transcript": HEARD}])
    run = PhoneCaller(rt, higgs.connect, "k", clock=lambda: NOW)
    plan = CallPlan(session_id=SID, listing_ids=["t:1"], created_at=NOW)
    dial = Dial(call_id="c_phone00001", session_id=SID, listing_id="t:1", to=AGENT, plan=plan,
                deadline=NOW + timedelta(seconds=6))   # 1 s left after the margin
    result = await asyncio.wait_for(run(dial, FakeLine()), 5)
    assert result.end_reason == "timeout" and result.outcome.end_reason == "timeout" and higgs.closed
    assert calls.live == {}
