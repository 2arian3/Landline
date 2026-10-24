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
from landline.voice.outbound import DISCLOSURE, RECORDED, OutboundCalls, TwilioRunner
from landline.voice.phone_caller import PhoneCaller
from landline.voice.recorder import CallRecorder

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
    # The disclosure, then that the call is recorded, is in the session instructions, and no
    # response.create carries a response object.
    assert f'exactly: "{DISCLOSURE} {RECORDED}"' in higgs.session["session"]["instructions"]
    assert [e["type"] for e in higgs.sent[:2]] == ["conversation.item.create", "response.create"]
    assert all("response" not in e for e in higgs.sent if e["type"] == "response.create")
    assert {"type": "input_audio_buffer.append", "audio": "/w=="} in higgs.sent
    assert higgs.closed and twilio_api.hung_up == ["CA1"]                 # Twilio's line is put down too
    assert rt.get(SID).phase == "done"
    # The steps after a pickup are in the log, in order, the first audio once (#148).
    said = [r.getMessage().split(": ")[-1] for r in caplog.records]
    assert [s for s in said if s in ("Higgs ready, opening sent", "first audio out")] == [
        "Higgs ready, opening sent", "first audio out"]


REFUSED = [
    {"type": "response.created"},
    {"type": "response.output_audio.delta", "delta": "AAAA"},
    {"type": "response.output_audio_transcript.done", "transcript": DISCLOSURE},
    {"type": "response.done", "response": {"id": "r1", "output": [{"type": "message"}]}},
    {"type": "input_audio_buffer.speech_started"},
    {"type": "input_audio_buffer.speech_stopped"},
    {"type": "conversation.item.input_audio_transcription.completed", "transcript": HEARD},
    {"type": "response.created"},
    _tool("record_fact", {"field": "parking_cost", "value": "$150 a month",
                           "quote": "Parking is 150 a month"}, "fc1"),
    {"type": "response.done", "response": {"id": "r2", "output": [{"type": "function_call"}]}},
    {"type": "response.created"},
    _tool("end_call", {"reason": "refused"}, "fc2"),
    {"type": "response.done", "response": {"id": "r3", "output": [{"type": "function_call"}]}},
]


async def test_the_line_goes_down_as_the_talk_ends_before_the_auditor_runs(monkeypatch):
    """The team's phone test: after the goodbye the listing agent heard silence while the Auditor ran."""
    monkeypatch.setattr(phone_caller, "HANG_UP_GRACE_S", 0)
    order = []
    hang_up, outcome = TwilioRunner._hang_up, phone_caller.auditor.outcome

    async def hung_up(self, sid):
        order.append("hang up")
        await hang_up(self, sid)

    async def audited(record, **kw):
        order.append("audit")
        return await outcome(record, **kw)
    monkeypatch.setattr(TwilioRunner, "_hang_up", hung_up)
    monkeypatch.setattr(phone_caller.auditor, "outcome", audited)
    rt, ended, higgs, twilio_api = await _call(REFUSED, FakeLine(["/w=="]))
    assert order == ["hang up", "audit"] and twilio_api.hung_up == ["CA1"]   # once, first
    assert [e.reason for e in ended] == ["refused"] and higgs.closed          # the Caller's end, not "hangup"
    facts = rt.state(SID).outcomes["t:1"].facts
    assert [(f.field, f.quote) for f in facts] == [("parking_cost", "Parking is 150 a month")]


async def test_a_hang_up_that_fails_still_ends_the_call_once_with_its_outcome(monkeypatch, caplog):
    monkeypatch.setattr(phone_caller, "HANG_UP_GRACE_S", 0)

    async def unreachable(self, sid):
        raise httpx.ConnectError("Twilio is unreachable")
    monkeypatch.setattr(TwilioRunner, "_hang_up", unreachable)
    rt, ended, higgs, _ = await _call(REFUSED, FakeLine(["/w=="]))
    assert [e.reason for e in ended] == ["refused"] and higgs.closed
    facts = rt.state(SID).outcomes["t:1"].facts
    assert [(f.field, f.quote) for f in facts] == [("parking_cost", "Parking is 150 a month")]
    assert any("hanging up failed (ConnectError" in r.getMessage() for r in caplog.records)


class BrokenRecorder:
    """A recorder whose every call fails."""

    def __init__(self, *args, **kwargs):
        pass

    def __getattr__(self, name):
        def broken(*args, **kwargs):
            raise RuntimeError(f"recorder.{name} broke")
        return broken


async def test_a_recorder_that_fails_never_ends_the_call(monkeypatch, caplog):
    monkeypatch.setattr(phone_caller, "HANG_UP_GRACE_S", 0)
    monkeypatch.setattr(phone_caller, "CallRecorder", BrokenRecorder)
    caplog.set_level(logging.ERROR, logger=phone_caller.__name__)
    script = [
        {"type": "response.created"},
        {"type": "response.output_audio.delta", "delta": "AAAA"},
        {"type": "response.output_audio_transcript.done", "transcript": DISCLOSURE},
        {"type": "response.done", "response": {"id": "r1", "output": [{"type": "message"}]}},
        {"type": "input_audio_buffer.speech_started"},
        {"type": "input_audio_buffer.speech_stopped"},
        {"type": "conversation.item.input_audio_transcription.completed", "transcript": HEARD},
        {"type": "response.created"},
        _tool("record_fact", {"field": "parking_cost", "value": "$150 a month",
                               "quote": "Parking is 150 a month"}, "fc1"),
        {"type": "response.done", "response": {"id": "r2", "output": [{"type": "function_call"}]}},
        {"type": "response.created"},
        _tool("end_call", {"reason": "refused"}, "fc2"),
        {"type": "response.done", "response": {"id": "r3", "output": [{"type": "function_call"}]}},
    ]
    rt, ended, _, _ = await _call(script, FakeLine(["/w=="]))
    assert [e.reason for e in ended] == ["refused"]   # the Caller's own end, never "failed"
    facts = rt.state(SID).outcomes["t:1"].facts
    assert [(f.field, f.quote) for f in facts] == [("parking_cost", "Parking is 150 a month")]
    failed = [r for r in caplog.records if "the recorder failed" in r.getMessage()]
    assert len(failed) == 1   # logged once, then the recorder stays off


async def test_a_goodbye_with_no_end_call_still_ends_the_call_once_after_it_has_played(monkeypatch):
    """The team's phone test: the Caller said goodbye and never called end_call, and the line stayed open."""
    monkeypatch.setattr(phone_caller, "END_AFTER_S", 0)
    script = [
        {"type": "response.created"},
        {"type": "response.output_audio.delta", "delta": "AAAA"},
        {"type": "response.output_audio_transcript.done", "transcript": DISCLOSURE},
        {"type": "response.done", "response": {"id": "r1", "output": [{"type": "message"}]}},
        {"type": "conversation.item.input_audio_transcription.completed",
         "transcript": "Sorry, I have to run. Bye."},
        {"type": "response.created"},
        {"type": "response.output_audio.delta", "delta": "BBBB"},
        {"type": "response.output_audio_transcript.done", "transcript": "No problem, thanks. Goodbye."},
        {"type": "response.done", "response": {"id": "r2", "output": [{"type": "message"}]}},
    ]
    line = FakeLine()
    rt, ended, higgs, twilio_api = await _call(script, line)
    assert [e.reason for e in ended] == ["done"]                          # one end, in code
    assert twilio_api.hung_up == ["CA1"] and higgs.closed
    assert line.played == ["AAAA", "BBBB"]                                # the goodbye went out first


class PausingHiggs(FakeHiggs):
    """A FakeHiggs whose script can wait in real time: {"type": "test.wait", "s": seconds}."""

    async def events(self):
        for event in self.script:
            await asyncio.sleep(event["s"] if event["type"] == "test.wait" else 0)
            if event["type"] != "test.wait":
                yield event
        await asyncio.sleep(60)


async def test_a_call_over_after_a_goodbye_still_lets_them_finish_and_answers_before_it_ends(monkeypatch):
    """The landlord speaks again after the Caller's goodbye: the call doesn't end while they speak, or before
    the Caller's answer has played."""
    monkeypatch.setattr(phone_caller, "END_AFTER_S", 0.05)
    script = [
        {"type": "response.created"},
        {"type": "response.output_audio.delta", "delta": "AAAA"},
        {"type": "response.output_audio_transcript.done", "transcript": DISCLOSURE},
        {"type": "response.done", "response": {"id": "r1", "output": [{"type": "message"}]}},
        {"type": "conversation.item.input_audio_transcription.completed", "transcript": "Got to run. Bye."},
        {"type": "response.created"},
        {"type": "response.output_audio.delta", "delta": "BBBB"},
        {"type": "response.output_audio_transcript.done", "transcript": "No problem, thanks. Goodbye."},
        {"type": "response.done", "response": {"id": "r2", "output": [{"type": "message"}]}},
        {"type": "input_audio_buffer.speech_started"},
        {"type": "test.wait", "s": 0.3},   # they speak for longer than END_AFTER_S
        {"type": "input_audio_buffer.speech_stopped"},
        {"type": "response.created"},
        {"type": "test.wait", "s": 0.2},   # the answer is on its way
        {"type": "response.output_audio.delta", "delta": "CCCC"},
        {"type": "response.done", "response": {"id": "r3", "output": [{"type": "message"}]}},
    ]
    line = FakeLine()
    rt, ended, higgs, _ = await _call(script, line, PausingHiggs(script))
    assert line.played == ["AAAA", "BBBB", "CCCC"] and [e.reason for e in ended] == ["done"]


FIRST_TURN = [
    {"type": "response.created"},
    {"type": "response.output_audio.delta", "delta": "AAAA"},
    {"type": "response.output_audio_transcript.done", "transcript": DISCLOSURE},
    {"type": "response.done", "response": {"id": "r1", "output": [{"type": "message"}]}},
]
GOODBYE = [   # what a Caller told to wrap up says: a goodbye, here with no end_call
    {"type": "response.created"},
    {"type": "response.output_audio.delta", "delta": "BBBB"},
    {"type": "response.output_audio_transcript.done", "transcript": "Thanks so much for your time. Goodbye."},
    {"type": "response.done", "response": {"id": "r9", "output": [{"type": "message"}]}},
]


class WrappingHiggs(PausingHiggs):
    """Plays its script, then answers the wrap-up note with GOODBYE. `timeline` holds the script's
    {"type": "test.mark"} events and the wrap-up, in order."""

    def __init__(self, script):
        super().__init__(script)
        self.timeline, self.wrapped = [], asyncio.Event()

    async def send(self, event):
        await super().send(event)
        if _text(event) == phone_caller.WRAP_UP:
            self.timeline.append("wrap up")
            self.wrapped.set()

    async def events(self):
        for event in self.script:
            await asyncio.sleep(event["s"] if event["type"] == "test.wait" else 0)
            if event["type"] == "test.mark":
                self.timeline.append(event["what"])
            elif event["type"] != "test.wait":
                yield event
        await self.wrapped.wait()
        for event in GOODBYE:
            await asyncio.sleep(0)
            yield event
        await asyncio.sleep(60)


def _text(event):
    return ((event.get("item") or {}).get("content") or [{}])[0].get("text")


def _quick_wrap_up(monkeypatch, has_it=True):
    monkeypatch.setattr(phone_caller, "WRAP_UP_S", 0.1)
    monkeypatch.setattr(phone_caller, "QUIET_EVERY_S", 0.02)
    monkeypatch.setattr(phone_caller, "END_AFTER_S", 0.05)
    if has_it:
        monkeypatch.setattr(phone_caller, "_has_it", lambda ctx: True)   # the checklist is covered


async def test_a_caller_with_what_it_came_for_on_a_quiet_line_wraps_up_once_and_ends_done(monkeypatch):
    """The team's phone test: the listing agent went quiet after the last answer, and the call stayed open
    until someone said bye."""
    _quick_wrap_up(monkeypatch)
    higgs, line = WrappingHiggs(FIRST_TURN), FakeLine()
    rt, ended, _, twilio_api = await _call([], line, higgs)
    assert [e for e in higgs.sent if _text(e) == phone_caller.WRAP_UP] == [
        {"type": "conversation.item.create", "item": {"type": "message", "role": "user", "content": [
            {"type": "input_text", "text": phone_caller.WRAP_UP}]}}]
    at = next(i for i, e in enumerate(higgs.sent) if _text(e) == phone_caller.WRAP_UP)
    assert higgs.sent[at + 1] == {"type": "response.create"}   # a plain one, never a response object
    assert line.played == ["AAAA", "BBBB"] and [e.reason for e in ended] == ["done"]
    assert twilio_api.hung_up == ["CA1"]


async def test_a_listing_agent_still_talking_holds_the_wrap_up_until_the_line_is_quiet(monkeypatch):
    _quick_wrap_up(monkeypatch)
    higgs = WrappingHiggs([*FIRST_TURN, {"type": "input_audio_buffer.speech_started"},
                           {"type": "test.wait", "s": 0.3},   # longer than WRAP_UP_S
                           {"type": "test.mark", "what": "they stop"},
                           {"type": "input_audio_buffer.speech_stopped"}])
    rt, ended, _, _ = await _call([], FakeLine(), higgs)
    assert higgs.timeline == ["they stop", "wrap up"] and [e.reason for e in ended] == ["done"]


async def test_a_quiet_line_mid_checklist_is_never_told_to_wrap_up(monkeypatch):
    _quick_wrap_up(monkeypatch, has_it=False)   # the brief still has its checklist open
    higgs = FakeHiggs(FIRST_TURN)
    rt, ended, _, _ = await _call([], FakeLine(hang_up_after=0.4), higgs)
    assert not any(_text(e) == phone_caller.WRAP_UP for e in higgs.sent)
    assert [e.reason for e in ended] == ["hangup"]


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


# ── recording (A-10) ─────────────────────────────────────────────────────────
# In call_turns and call_audio, `landlord` is the listing agent on the line and `agent` is the Caller.

class FakeTurnsRepo:
    def __init__(self):
        self.rows = {}   # idx -> (speaker, text, audio_start_ms, audio_end_ms)

    async def append(self, call_id, idx, speaker, text, audio_start_ms=None, audio_end_ms=None):
        await asyncio.sleep(0)   # a slow write: a move must still land after it
        self.rows[idx] = (speaker, text, audio_start_ms, audio_end_ms)

    async def place(self, call_id, idx, audio_start_ms, audio_end_ms):
        assert idx in self.rows, "a line was moved before it was stored"
        self.rows[idx] = (*self.rows[idx][:2], audio_start_ms, audio_end_ms)


class FakeAudioRepo:
    def __init__(self):
        self.chunks = []   # (track, seq, start_ms, format, rate, audio)

    async def append(self, call_id, track, seq, start_ms, fmt, rate, audio):
        self.chunks.append((track, seq, start_ms, fmt, rate, audio))


class WithDb:
    """The runtime, with a fake database for what the call writes."""

    def __init__(self, rt):
        self._rt = rt
        self.db = type("DB", (), {"turns": FakeTurnsRepo(), "audio": FakeAudioRepo()})()

    def __getattr__(self, name):
        return getattr(self._rt, name)


FRAME = bytes(range(160))       # 20 ms of μ-law from the listing agent
OPENING = bytes([0x55]) * 800   # 100 ms of the Caller
ASK = bytes([0x66]) * 800       # 100 ms more
ASKED = "Great. And does it come with parking"
HELLO, SORRY = "Hi, this is Linda.", "Sorry, one second."


def _b64(data):
    return base64.b64encode(data).decode()


def _at(seconds):
    return {"type": "test.clock", "at": seconds}


def _theirs(item_id, start_ms, end_ms, text):
    """A line of the listing agent's, `start_ms` to `end_ms` into their audio as Higgs counts it."""
    return [{"type": "input_audio_buffer.speech_started", "item_id": item_id, "audio_start_ms": start_ms},
            {"type": "input_audio_buffer.speech_stopped", "item_id": item_id, "audio_end_ms": end_ms},
            {"type": "conversation.item.input_audio_transcription.completed", "transcript": text,
             "item_id": item_id}]


def _callers(item_id, audio, text, response_id):
    return [{"type": "response.created"},
            {"type": "response.output_audio.delta", "delta": _b64(audio), "item_id": item_id},
            {"type": "response.output_audio_transcript.done", "transcript": text, "item_id": item_id},
            {"type": "response.done", "response": {"id": response_id, "output": [{"type": "message"}]}}]


async def _recorded_call(monkeypatch, record_audio=True):
    """The Caller opens (100 ms). The listing agent says hello over it at 40 ms, which doesn't cut it, and
    answers once it has played. The Caller asks (100 ms from 1 s), they talk over that at 1.04 s, then hang
    up. Returns the fake database, fake Higgs and the line."""
    clock = [0.0]
    monkeypatch.setattr(phone_caller, "Playback", lambda rate: Playback(rate, clock=lambda: clock[0]))
    monkeypatch.setattr(phone_caller, "CallRecorder",
                        lambda fmt, caller: CallRecorder(fmt, caller, clock=lambda: clock[0]))
    script = [
        *_callers("i_open", OPENING, DISCLOSURE, "r1"),
        _at(0.04), *_theirs("i_hello", 20, 60, HELLO),     # over the opening: it plays on
        _at(0.2), *_theirs("i_yes", 180, 400, HEARD),      # once it has played
        _at(1.0), *_callers("i_ask", ASK, ASKED, "r2"),
        _at(1.04), *_theirs("i_sorry", 1020, 1060, SORRY),  # a barge-in
        _at(2.0),
    ]
    higgs, rt = ClockedHiggs(script, clock), WithDb(_runtime())
    run = PhoneCaller(rt, higgs.connect, "k", clock=lambda: NOW, record_audio=record_audio)
    plan = CallPlan(session_id=SID, listing_ids=["t:1"], created_at=NOW)
    dial = Dial(call_id="c_phone00002", session_id=SID, listing_id="t:1", to=AGENT, plan=plan,
                deadline=NOW + timedelta(seconds=60))
    line = FakeLine([_b64(FRAME)] * 3, hang_up_after=0.3)
    result = await asyncio.wait_for(run(dial, line), 5)
    assert result.end_reason == "hangup"
    return rt.db, higgs, line


# Each line at its place: theirs where Higgs heard it, the Caller's where it played. The opening plays whole
# under a hello; the question ends where they cut in.
PLACED = {0: ("agent", DISCLOSURE, 0, 100), 1: ("landlord", HELLO, 20, 60), 2: ("landlord", HEARD, 180, 400),
          3: ("agent", ASKED, 1000, 1040), 4: ("landlord", SORRY, 1020, 1060)}


async def test_a_phone_call_stores_both_sides_of_its_audio_with_each_line_in_place(monkeypatch):
    db, higgs, line = await _recorded_call(monkeypatch)
    assert f'exactly: "{DISCLOSURE} {RECORDED}"' in higgs.session["session"]["instructions"]
    assert db.turns.rows == PLACED
    assert line.cleared == 2   # the answer after the opening, and the barge-in
    assert {(fmt, rate) for _, _, _, fmt, rate, _ in db.audio.chunks} == {("pcmu", 8000)}
    tracks = {}
    for track, _seq, start_ms, _fmt, _rate, audio in sorted(db.audio.chunks):
        tracks.setdefault(track, []).append((start_ms, audio))
    assert tracks == {"landlord": [(0, FRAME * 3)],                  # all they said, from when it connected
                      "agent": [(0, OPENING), (1000, ASK[:320])]}    # what played, cut at the barge-in


async def test_with_record_audio_off_no_audio_is_stored_and_the_call_doesnt_say_it_is(monkeypatch):
    db, higgs, _ = await _recorded_call(monkeypatch, record_audio=False)
    instructions = higgs.session["session"]["instructions"]
    assert f'exactly: "{DISCLOSURE}"' in instructions and RECORDED not in instructions
    assert db.audio.chunks == []
    assert db.turns.rows == PLACED   # the lines still get their place
