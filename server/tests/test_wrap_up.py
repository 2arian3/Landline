"""The renter call's wrap-up (voice/bridge.py `_wraps_up`, #205's twin): once call_listings said the plan is
complete and the line has gone quiet, the Concierge is told to say goodbye and call end_session, and the
call ends if it only says goodbye. Scripted Higgs events through `_higgs_to_renter`, no network."""
import asyncio
from datetime import UTC, datetime

from landline.agents.concierge import tools
from landline.contracts import Listing, SessionStarted
from landline.db.catalogue import Catalogue
from landline.orchestrator import SessionRuntime
from landline.voice import bridge
from landline.voice.recorder import PCMU_8K, CallRecorder

NOW = datetime(2026, 10, 24, 17, 0, tzinfo=UTC)
SID = "s_wrap000001"
COMPLETE = (f"Calling 1 Main St as soon as you hang up, and texting you what they say. "
            f"{bridge.PLAN_COMPLETE} tell them you'll call as soon as you hang up and text the results, say "
            "goodbye, and call end_session.")
PLAN = [
    {"type": "response.created"},
    {"type": "response.function_call_arguments.done", "name": "call_listings", "call_id": "fc1",
     "arguments": "{}", "response_id": "r1"},
    {"type": "response.done", "response": {"id": "r1", "output": [{"type": "function_call"}]}},
    {"type": "response.created"},
    {"type": "response.output_audio.delta", "delta": "AAAA"},
    {"type": "response.output_audio_transcript.done", "transcript": "I'll call 1 Main St when we hang up."},
    {"type": "response.done", "response": {"id": "r2", "output": [{"type": "message"}]}},
]
GOODBYE = [   # what a Concierge told to wrap up says, here with no end_session
    {"type": "response.created"},
    {"type": "response.output_audio.delta", "delta": "BBBB"},
    {"type": "response.output_audio_transcript.done", "transcript": "I'll text you soon. Goodbye!"},
    {"type": "response.done", "response": {"id": "r9", "output": [{"type": "message"}]}},
]


class Higgs:
    """Plays `script` ({"type": "test.wait", "s": …} waits, {"type": "test.mark"} notes the time line),
    then answers the wrap-up note with GOODBYE."""

    def __init__(self, script):
        self.script, self.sent, self.timeline, self.told = script, [], [], asyncio.Event()

    async def send(self, event):
        self.sent.append(event)
        if _text(event) == bridge.WRAP_UP:
            self.timeline.append("wrap up")
            self.told.set()

    async def events(self):
        for event in self.script:
            await asyncio.sleep(event["s"] if event["type"] == "test.wait" else 0)
            if event["type"] == "test.mark":
                self.timeline.append(event["what"])
            elif event["type"] != "test.wait":
                yield event
        await self.told.wait()
        for event in GOODBYE:
            await asyncio.sleep(0)
            yield event
        await asyncio.sleep(60)


class Quiet:
    async def send_audio(self, b64):
        pass

    async def send_clear(self):
        pass


def _text(event):
    return ((event.get("item") or {}).get("content") or [{}])[0].get("text")


async def _call(monkeypatch, script, output=COMPLETE, timeout=2.0):
    """The renter call's pumps on `script`, with call_listings answering `output`. Returns fake Higgs and
    why the call ended."""
    monkeypatch.setattr(bridge, "WRAP_UP_S", 0.1)
    monkeypatch.setattr(bridge, "QUIET_EVERY_S", 0.02)

    async def run_tool(higgs, event, ctx, *, spoke=True, agent=tools):
        return output
    monkeypatch.setattr(bridge, "run_tool", run_tool)
    listing = Listing(listing_id="t:1", source="t", source_id="1", url="https://example.com/1",
                      first_seen=NOW, last_seen=NOW, address="1 Main St", lat=43.65, lng=-79.38, rent=2400,
                      monthly_cost=2400, beds=1)
    runtime = SessionRuntime(Catalogue([listing], "test"))
    await runtime.start(SessionStarted(session_id=SID, at=NOW, idem_key=f"{SID}:start", channel="browser"))
    ctx = tools.ToolContext(session_id=SID, call_id="c_wrap000001", runtime=runtime)
    higgs, playback, wrap = Higgs(script), bridge.Playback(8000), bridge.WrapUp()
    pumps = {asyncio.create_task(bridge._higgs_to_renter(higgs, Quiet(), ctx, playback, CallRecorder(PCMU_8K),
                                                         wrap=wrap)),
             asyncio.create_task(bridge._wraps_up(higgs, ctx, playback, wrap))}
    done, pending = await asyncio.wait(pumps, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    return higgs, bridge._why(done)


async def test_a_complete_plan_on_a_quiet_line_wraps_up_once_and_the_goodbye_ends_the_call(monkeypatch):
    """The team's test: the plan was read back and nothing was left, and the call waited for the renter's
    bye."""
    higgs, why = await _call(monkeypatch, PLAN)
    notes = [i for i, e in enumerate(higgs.sent) if _text(e) == bridge.WRAP_UP]
    assert len(notes) == 1 and higgs.sent[notes[0] + 1] == {"type": "response.create"}   # a plain one
    assert why == "end_session"   # the goodbye played with no end_session: ended as end_session would


async def test_the_renter_talking_holds_the_wrap_up_until_the_line_is_quiet(monkeypatch):
    higgs, why = await _call(monkeypatch, [*PLAN, {"type": "input_audio_buffer.speech_started"},
                                           {"type": "test.wait", "s": 0.3},   # longer than WRAP_UP_S
                                           {"type": "test.mark", "what": "they stop"},
                                           {"type": "input_audio_buffer.speech_stopped"}])
    assert higgs.timeline == ["they stop", "wrap up"] and why == "end_session"


async def test_a_plan_with_something_left_to_ask_never_wraps_up(monkeypatch):
    output = "Calling 1 Main St as soon as you hang up. Booking: not allowed yet. Ask whether you may book."
    higgs, why = await _call(monkeypatch, PLAN, output=output, timeout=0.5)
    assert not any(_text(e) == bridge.WRAP_UP for e in higgs.sent) and why == "deadline"
