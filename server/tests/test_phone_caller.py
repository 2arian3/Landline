"""The Caller (A-05) on a phone line, end to end with no network: the Dispatcher places the call through the
Twilio runner (fake Twilio), a person answers, and the real Caller tools run on a fake Higgs over a fake
media stream (voice/phone_caller.py)."""
import asyncio
import base64
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
from landline.voice.bridge import Playback
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


def _runtime(n=1):
    return SessionRuntime(Catalogue([
        Listing(listing_id=f"t:{i}", source="t", source_id=str(i), url=f"https://example.com/{i}",
                first_seen=NOW, last_seen=NOW, address=f"{i} Dial St", lat=43.65, lng=-79.38, rent=2000,
                monthly_cost=2000, beds=1)
        for i in range(1, n + 1)], "test"))


def _tool(name, args, fc):
    return {"type": "response.function_call_arguments.done", "name": name, "call_id": fc,
            "arguments": json.dumps(args), "response_id": f"r_{fc}"}


class ClockedHiggs(FakeHiggs):
    """A FakeHiggs whose script can move the playback clock: {"type": "test.clock", "at": seconds}."""

    def __init__(self, script, clock):
        super().__init__(script)
        self.clock = clock

    async def events(self):
        for event in self.script:
            await asyncio.sleep(0)
            if event["type"] == "test.clock":
                self.clock[0] = event["at"]
                continue
            yield event
        await asyncio.sleep(60)


async def _call(script, line, higgs=None):
    """One brief with one picked place, ended; its call runs to the end. Returns the runtime, the
    CallEnded events and fake Higgs."""
    rt, settings, calls = _runtime(), _settings(), OutboundCalls()
    ended = []

    async def on_ended(event, _view):
        ended.append(event)
    rt.on("CallEnded", on_ended)
    higgs, twilio_api = higgs or FakeHiggs(script), FakeTwilio(calls, line)
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


async def test_speech_over_the_opening_lets_it_play_and_after_it_barges_in(monkeypatch):
    """A "Hello?" while the disclosure is coming or still playing doesn't clear it (hard rule 6); once it
    has played, speech barges in as before."""
    monkeypatch.setattr(phone_caller, "HANG_UP_GRACE_S", 0)
    clock = [0.0]
    monkeypatch.setattr(phone_caller, "Playback", lambda rate: Playback(rate, clock=lambda: clock[0]))
    one_second = base64.b64encode(bytes(8000)).decode()   # 8 kHz μ-law
    script = [
        {"type": "response.created"},
        {"type": "response.output_audio.delta", "delta": one_second},
        {"type": "input_audio_buffer.speech_started"},     # while the opening is still coming
        {"type": "response.done", "response": {"id": "r1", "output": [{"type": "message"}]}},
        {"type": "test.clock", "at": 0.5},
        {"type": "input_audio_buffer.speech_started"},     # done, and still playing
        {"type": "test.clock", "at": 2.0},
        {"type": "input_audio_buffer.speech_started"},     # it has played: a barge-in
        {"type": "response.created"},
        _tool("end_call", {"reason": "refused"}, "fc1"),
        {"type": "response.done", "response": {"id": "r2", "output": [{"type": "function_call"}]}},
    ]
    line = FakeLine()
    _, ended, _, _ = await _call(script, line, higgs=ClockedHiggs(script, clock))
    assert line.cleared == 1 and [e.reason for e in ended] == ["refused"]


# The Higgs session opens while Twilio connects the stream (#148)


class Sessions:
    """Higgs sessions, a new one per connect, each playing `script`; counts how many are open at once."""

    def __init__(self, script, log):
        self.script, self.log, self.all, self.open, self.most = script, log, [], 0, 0

    async def connect(self, key, session):
        self.log.append("higgs")
        higgs = FakeHiggs(self.script)
        higgs.session = session
        self.all.append(higgs)
        self.open += 1
        self.most = max(self.most, self.open)
        close = higgs.close

        async def closing():
            if not higgs.closed:
                self.open -= 1
            await close()
        higgs.close = closing
        return higgs


class Answering(FakeTwilio):
    """A person answers: Twilio fetches the TwiML (answered_twiml marks the call answered), then connects
    the stream a moment later, or the person hangs up before it does."""

    def __init__(self, calls, line, log, stream=True):
        super().__init__(calls, line)
        self.log, self.stream = log, stream

    async def answer(self, cid):
        await self.calls.set_state(cid, "ringing")
        self.calls.get(cid).answered.set()
        await asyncio.sleep(0.05)   # Twilio connects the stream
        if not self.stream:
            self.calls.finalize(cid, "hangup")
            return
        call = self.calls.claim(cid)
        await self.calls.set_state(cid, "live")
        self.log.append("stream")
        call.media.set_result(self.line)


ENDS = [{"type": "response.created"}, {"type": "response.output_audio.delta", "delta": "AAAA"},
        {"type": "response.done", "response": {"id": "r1", "output": [{"type": "message"}]}},
        {"type": "response.created"}, _tool("end_call", {"reason": "refused"}, "fc1"),
        {"type": "response.done", "response": {"id": "r2", "output": [{"type": "function_call"}]}}]


async def _answered(stream=True, places=1, **settings):
    """One brief whose picked places are each answered by a person; returns the CallEnded events, the Higgs
    sessions and the order things happened in."""
    rt, calls, log, ended = _runtime(places), OutboundCalls(), [], []

    async def on_ended(event, _view):
        ended.append(event)
    rt.on("CallEnded", on_ended)
    sessions, twilio_api = Sessions(ENDS, log), Answering(calls, FakeLine(), log, stream)
    runner = TwilioRunner(_settings(**settings), calls, runtime=rt,
                          converse=PhoneCaller(rt, sessions.connect, "k", clock=lambda: NOW),
                          client=httpx.AsyncClient(transport=httpx.MockTransport(twilio_api.handle)))
    d = Dispatcher(rt, _settings(**settings), runner=runner, clock=lambda: NOW)
    await rt.start(SessionStarted(session_id=SID, at=NOW, idem_key=f"{SID}:start", channel="phone",
                                  caller_phone="+14165550199"))
    await rt.emit(CallsRequested(session_id=SID, at=NOW, idem_key=f"{SID}:plan",
                                 listing_ids=[f"t:{i}" for i in range(1, places + 1)]))
    await rt.emit(SessionEnded(session_id=SID, at=NOW, idem_key=f"{SID}:end", reason="hangup"))
    await asyncio.wait_for(d.idle(), 10)
    return ended, sessions, log


async def test_the_higgs_session_opens_while_twilio_connects_the_stream_and_the_call_uses_it(monkeypatch):
    monkeypatch.setattr(phone_caller, "HANG_UP_GRACE_S", 0)
    ended, sessions, log = await _answered()
    assert log == ["higgs", "stream"]   # opened before the stream came, and only once
    [higgs] = sessions.all
    assert higgs.sent[:2][-1] == {"type": "response.create"} and higgs.closed and sessions.open == 0
    assert [e.reason for e in ended] == ["refused"]


async def test_an_early_session_no_stream_came_for_is_closed():
    ended, sessions, log = await _answered(stream=False)   # the person hung up before the stream
    assert log == ["higgs"] and [h.closed for h in sessions.all] == [True] and sessions.open == 0
    assert len(ended) == 1


async def test_an_early_session_still_opening_when_the_call_ends_is_stopped(monkeypatch):
    async def slow(self, key, session):   # Higgs takes longer to open than the person stays on the line
        await asyncio.sleep(5)
        return await Sessions.connect(self, key, session)
    monkeypatch.setattr(Sessions, "connect", slow)
    started = asyncio.get_running_loop().time()
    ended, sessions, _ = await _answered(stream=False)
    assert sessions.all == [] and sessions.open == 0 and len(ended) == 1   # stopped, never opened
    assert asyncio.get_running_loop().time() - started < 2


async def test_early_sessions_stay_inside_the_dispatchers_limit(monkeypatch):
    monkeypatch.setattr(phone_caller, "HANG_UP_GRACE_S", 0)
    two_phones = (AGENT, "+16475550199")
    ended, sessions, _ = await _answered(places=2, call_concurrency=1, demo_agent_phones=two_phones)
    assert len(sessions.all) == 2 and sessions.most == 1 and sessions.open == 0   # one at a time, all closed
    assert sorted(e.reason for e in ended) == ["refused", "refused"]


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
