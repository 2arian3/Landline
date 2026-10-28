"""Gemini Live speaking the Higgs dialect, with no network: codec, tool replies, barge-in, and the
phone and browser bridges on a fake Live socket."""
import asyncio
import base64
import json
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field
from starlette.websockets import WebSocketDisconnect

from landline.agents.concierge.prompt import CALL_CONNECTED
from landline.api.twilio import NOT_READY
from landline.app import create_app
from landline.config import Settings
from landline.contracts import Listing
from landline.contracts.tools import tool_spec
from landline.db.catalogue import Catalogue
from landline.voice import twilio
from landline.voice.audio_codec import Transcoder
from landline.voice.gemini_live import (
    TEXT_MODEL,
    GeminiLiveSession,
    LiveEvent,
    _FlashTextLink,
    _text_setup,
    live_config,
)
from landline.voice.higgs import PCM_24K, PCMU, session_config

PUBLIC = "https://landline.test"
TOKEN = "test-token"
T0 = datetime(2026, 10, 23, tzinfo=UTC)


class _Note(BaseModel):
    text: str = Field(description="What was said")


def _listing(i: int, rent: int, beds: float) -> Listing:
    return Listing(listing_id=f"t:{i}", source="t", source_id=str(i), url=f"https://example.com/{i}",
                   first_seen=T0, last_seen=T0, address=f"{i} Test St", lat=43.65, lng=-79.38,
                   rent=rent, monthly_cost=rent, beds=beds, sqft=700)


def _client(**overrides) -> TestClient:
    settings = Settings(database_url=None, web_url="http://localhost:3000", transport="stub",
                        boson_api_key="test-key", twilio_auth_token=TOKEN, public_url=PUBLIC)
    catalogue = Catalogue([_listing(1, 2400, 1), _listing(2, 2900, 2), _listing(3, 3600, 2)], "test")
    return TestClient(create_app(replace(settings, **overrides), catalogue))


def _start(call_id: str, sid: str = "MZ1") -> dict:
    return {"event": "start", "streamSid": sid,
            "start": {"streamSid": sid, "callSid": "CA1", "customParameters": {"call_id": call_id}}}


def _ring(c: TestClient, token: str = TOKEN):
    params = {"CallSid": "CA1", "From": "+14165550100", "To": "+16475550199"}
    sig = twilio.signature(token, f"{PUBLIC}/twiml/renter", params)
    return c.post("/twiml/renter", data=params, headers={"X-Twilio-Signature": sig})


class FakeLink:
    """A Live socket that records what the session sends and plays a script, then stays open."""

    def __init__(self, script: list[LiveEvent] | None = None):
        self.audio: list[tuple[int, bytes]] = []
        self.texts: list[str] = []
        self.tools: list[list[tuple[str, str, str]]] = []
        self.closed = False
        self._in: asyncio.Queue[LiveEvent | None] = asyncio.Queue()
        for ev in script or []:
            self._in.put_nowait(ev)

    def push(self, ev: LiveEvent) -> None:
        self._in.put_nowait(ev)

    async def send_audio(self, pcm: bytes, rate: int) -> None:
        self.audio.append((rate, pcm))

    async def send_text(self, text: str, turn_complete: bool = True) -> None:
        self.texts.append(text)

    async def send_tools(self, responses: list[tuple[str, str, str]]) -> None:
        self.tools.append(list(responses))

    async def incoming(self):
        while not self.closed:
            ev = await self._in.get()
            if ev is None:
                return
            yield ev

    async def close(self) -> None:
        self.closed = True
        self._in.put_nowait(None)


def _session(link: FakeLink, fmt: dict | None = None, modality: str = "audio") -> GeminiLiveSession:
    return GeminiLiveSession(link, fmt or PCMU, modality)


async def _collect(session: GeminiLiveSession, n: int) -> list[dict]:
    out = []
    async for ev in session.events():
        out.append(ev)
        if len(out) >= n:
            return out
    return out


def _note(text: str) -> dict:
    return {"type": "conversation.item.create", "item": {
        "type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]}}


def _tool_out(call_id: str, output: str) -> dict:
    return {"type": "conversation.item.create",
            "item": {"type": "function_call_output", "call_id": call_id, "output": output}}


# ── codec ───────────────────────────────────────────────────────────────────

def test_phone_audio_becomes_pcm16_16k_and_back_to_ulaw():
    codec = Transcoder(PCMU)
    ulaw = b"\xff" * 160   # 20 ms of silence
    pcm, rate = codec.to_gemini(base64.b64encode(ulaw).decode())
    assert rate == 16000 and pcm and len(pcm) % 2 == 0
    # 20 ms at 16 kHz is 640 bytes. The resampler's filter adds a little.
    assert 500 < len(pcm) < 900
    spoken = codec.from_gemini(b"\x00\x01" * 240)   # 10 ms of 24 kHz PCM16
    back = base64.b64decode(spoken)
    assert spoken and 40 < len(back) < 160


def test_browser_pcm_24k_passes_through_both_ways():
    codec = Transcoder(PCM_24K)
    raw = b"\x03\x00\x04\x00"
    pcm, rate = codec.to_gemini(base64.b64encode(raw).decode())
    assert (rate, pcm) == (24000, raw)
    assert base64.b64decode(codec.from_gemini(raw)) == raw


def test_heard_ms_uses_the_transport_clock():
    assert Transcoder(PCMU).heard_ms(8000) == 1000
    assert Transcoder(PCM_24K).heard_ms(48000) == 1000


# ── setup ───────────────────────────────────────────────────────────────────

def test_setup_uses_a_gemini_voice_and_the_tools_schema():
    spec = tool_spec("note", _Note, "Write down a short note.")
    cfg = live_config(session_config(instructions="Be brief.", tools=[spec], voice="default"), "Aoede")
    assert cfg["response_modalities"] == ["AUDIO"]
    assert cfg["speech_config"]["voice_config"]["prebuilt_voice_config"]["voice_name"] == "Aoede"
    assert cfg["input_audio_transcription"] == {} and cfg["output_audio_transcription"] == {}
    decl = cfg["tools"][0]["function_declarations"][0]
    assert decl["name"] == "note" and decl["parameters_json_schema"]["properties"]["text"]["type"] == "string"


def test_text_modality_drops_the_audio_config():
    cfg = live_config(session_config(instructions="Be brief.", tools=[], modality="text"), "Aoede")
    assert cfg["response_modalities"] == ["TEXT"]
    assert "speech_config" not in cfg and "input_audio_transcription" not in cfg


def test_an_explicit_voice_is_kept():
    cfg = live_config(session_config(instructions="x", tools=[], voice="Kore"), "Aoede")
    assert cfg["speech_config"]["voice_config"]["prebuilt_voice_config"]["voice_name"] == "Kore"


def test_events_of_reads_a_sdk_message_once():
    from types import SimpleNamespace

    from landline.voice.gemini_live import events_of

    audio = b"\x00\x01"
    msg = SimpleNamespace(
        data=audio, go_away=None, tool_call_cancellation=None,
        server_content=SimpleNamespace(
            interrupted=True,
            model_turn=SimpleNamespace(parts=[
                SimpleNamespace(inline_data=SimpleNamespace(data=audio), text="Hi"),
            ]),
            input_transcription=SimpleNamespace(text="hey"),
            interim_input_transcription=None,
            output_transcription=SimpleNamespace(text="Hi"),
            turn_complete=True,
        ),
        tool_call=SimpleNamespace(function_calls=[
            SimpleNamespace(id="fc", name="note", args={"text": "a"}),
        ]),
    )
    evs = events_of(msg)
    assert [e.audio for e in evs if e.audio] == [audio]   # data repeats the part; counted once
    assert evs[0].interrupted and evs[2].text == "Hi"
    assert any(e.input_transcript == "hey" for e in evs)
    assert any(e.output_transcript == "Hi" for e in evs)
    assert any(e.tool_calls == [("fc", "note", {"text": "a"})] for e in evs)
    assert any(e.turn_complete for e in evs)


# ── dialect ─────────────────────────────────────────────────────────────────

async def test_greeting_is_one_client_turn_and_a_second_create_is_ignored():
    link = FakeLink()
    session = _session(link)
    await session.start()
    try:
        await session.send(_note(CALL_CONNECTED))
        await session.send({"type": "response.create"})
        await session.send({"type": "response.create"})
        assert link.texts == [CALL_CONNECTED]
        assert link.tools == []
    finally:
        await session.close()


async def test_tool_calls_become_one_tool_response_on_response_create():
    link = FakeLink()
    session = _session(link)
    await session.start()
    try:
        link.push(LiveEvent(tool_calls=[
            ("fc1", "focus_listing", {"listing_ref": 1}),
            ("fc2", "update_preferences", {"max_monthly": 3000}),
        ]))
        link.push(LiveEvent(turn_complete=True))
        events = await asyncio.wait_for(_collect(session, 4), 1)
        kinds = [e["type"] for e in events]
        assert kinds == [
            "response.created",
            "response.function_call_arguments.done",
            "response.function_call_arguments.done",
            "response.done",
        ]
        assert events[1]["arguments"] == json.dumps({"listing_ref": 1})
        assert events[1]["response_id"] == events[3]["response"]["id"] == "r1"
        assert events[3]["response"]["output"] == [
            {"type": "function_call", "name": "focus_listing", "call_id": "fc1"},
            {"type": "function_call", "name": "update_preferences", "call_id": "fc2"},
        ]
        await session.send(_tool_out("fc1", "Showing 1."))
        await session.send({"type": "response.create"})
        await session.send(_tool_out("fc2", "Saved."))
        await session.send({"type": "response.create"})   # the second create Higgs would refuse
        assert link.tools == [[("fc1", "focus_listing", "Showing 1."),
                               ("fc2", "update_preferences", "Saved.")]]
        assert link.texts == []
    finally:
        await session.close()


async def test_a_tool_answer_with_no_response_create_drops_the_follow_up():
    """#219: the reply already spoke, so nothing asks the model to speak again. The tool is still
    answered, or the session stalls, and the extra speech is not played."""
    link = FakeLink()
    session = _session(link)
    await session.start()
    try:
        link.push(LiveEvent(audio=b"\x00\x01" * 20, output_transcript="Noted."))
        link.push(LiveEvent(tool_calls=[("fc1", "focus_listing", {"listing_ref": "1"})]))
        link.push(LiveEvent(turn_complete=True))
        first = await asyncio.wait_for(_collect(session, 4), 1)
        assert [e["type"] for e in first] == [
            "response.created", "response.output_audio.delta",
            "response.function_call_arguments.done", "response.output_audio_transcript.done",
        ]
        done = await asyncio.wait_for(_collect(session, 1), 1)
        assert done[0]["type"] == "response.done"
        await session.send(_tool_out("fc1", "Showing it."))
        for _ in range(50):
            if link.tools:
                break
            await asyncio.sleep(0.01)
        assert link.tools == [[("fc1", "focus_listing", "Showing it.")]]
        assert session._suppress
        link.push(LiveEvent(audio=b"\x02\x00" * 20, output_transcript="Again."))
        link.push(LiveEvent(turn_complete=True))
        for _ in range(50):
            if not session._suppress:
                break
            await asyncio.sleep(0.01)
        assert not session._suppress
        assert session._queue.empty()
    finally:
        await session.close()


async def test_barge_in_and_the_callers_words():
    link = FakeLink()
    session = _session(link)
    await session.start()
    try:
        raw = base64.b64encode(b"\xff" * 800).decode()
        await session.send({"type": "input_audio_buffer.append", "audio": raw})
        link.push(LiveEvent(interrupted=True, input_transcript="a one bedroom"))
        link.push(LiveEvent(turn_complete=True))
        events = await asyncio.wait_for(_collect(session, 3), 1)
        assert events[0]["type"] == "input_audio_buffer.speech_started"
        assert events[0]["audio_start_ms"] == 100   # 800 bytes of μ-law
        assert events[1] == {
            "type": "input_audio_buffer.speech_stopped", "item_id": events[0]["item_id"],
            "audio_end_ms": 100,
        }
        assert events[2]["type"] == "conversation.item.input_audio_transcription.completed"
        assert events[2]["transcript"] == "a one bedroom"
        assert link.audio and link.audio[0][0] == 16000
    finally:
        await session.close()


async def test_text_mode_emits_output_text():
    link = FakeLink()
    session = _session(link, modality="text")
    await session.start()
    try:
        link.push(LiveEvent(text="Hello"))
        link.push(LiveEvent(text=" there", turn_complete=True))
        events = await asyncio.wait_for(_collect(session, 4), 1)
        assert [e["type"] for e in events] == [
            "response.created", "response.output_text.delta", "response.output_text.delta",
            "response.output_text.done",
        ]
        assert events[3]["text"] == "Hello there"
        done = await asyncio.wait_for(_collect(session, 1), 1)
        assert done[0]["response"]["output"] == [{"type": "message"}]
    finally:
        await session.close()


def test_text_setup_keeps_the_tools_and_skips_temperature():
    cfg = _text_setup(session_config(
        instructions="Be brief.", tools=[tool_spec("note", _Note, "Write down a short note.")],
        modality="text"))
    assert cfg["system_instruction"] == "Be brief."
    assert "temperature" not in cfg
    assert cfg["thinking_config"] == {"thinking_level": "MEDIUM"}
    decl = cfg["tools"][0]["function_declarations"][0]
    assert decl["name"] == "note"
    assert "parameters_json_schema" in decl


async def test_a_text_session_speaks_the_flash_lite_reply():
    from types import SimpleNamespace

    seen: dict = {}

    class _Models:
        async def generate_content(self, *, model, contents, config):
            seen["model"], seen["contents"], seen["config"] = model, contents, config
            content = SimpleNamespace(
                role="model", parts=[SimpleNamespace(text="pong", thought=False, function_call=None)])
            return SimpleNamespace(candidates=[SimpleNamespace(content=content)])

    client = SimpleNamespace(aio=SimpleNamespace(models=_Models()))
    link = _FlashTextLink(client, TEXT_MODEL, _text_setup(session_config(
        instructions="One word.", tools=[], modality="text")))
    session = _session(link, modality="text")
    await session.start()
    try:
        await session.send(_note("Reply with the single word pong."))
        await session.send({"type": "response.create"})
        events = await asyncio.wait_for(_collect(session, 4), 1)
    finally:
        await session.close()
    assert seen["model"] == "gemini-3.5-flash-lite"
    assert seen["config"]["system_instruction"] == "One word."
    assert [e["type"] for e in events] == [
        "response.created", "response.output_text.delta", "response.output_text.done", "response.done",
    ]
    assert events[2]["text"] == "pong"


async def test_a_text_tool_call_is_sent_back_with_its_id():
    from types import SimpleNamespace

    from google.genai import types

    calls: list = []

    class _Models:
        async def generate_content(self, *, model, contents, config):
            del model, config
            calls.append(list(contents))
            if len(calls) == 1:
                call = types.FunctionCall(name="note", args={"text": "harbour"})
                content = types.Content(role="model", parts=[types.Part(function_call=call)])
            else:
                content = types.Content(role="model", parts=[types.Part(text="Saved harbour.")])
            return SimpleNamespace(candidates=[SimpleNamespace(content=content)])

    client = SimpleNamespace(aio=SimpleNamespace(models=_Models()))
    link = _FlashTextLink(client, TEXT_MODEL, _text_setup(session_config(
        instructions="Remember words.", tools=[tool_spec("note", _Note, "Write down a short note.")],
        modality="text")))
    session = _session(link, modality="text")
    await session.start()
    stream = session.events()
    try:
        await session.send(_note("Remember the word harbour."))
        await session.send({"type": "response.create"})
        call = await asyncio.wait_for(_until(stream, "response.function_call_arguments.done"), 1)
        await session.send(_tool_out(call["call_id"], "Saved."))
        await session.send({"type": "response.create"})
        said = await asyncio.wait_for(_until(stream, "response.output_text.done"), 1)
    finally:
        await session.close()
    assert call["name"] == "note"
    assert call["arguments"] == '{"text": "harbour"}'
    reply = calls[1][-1].parts[0].function_response
    assert reply.id == call["call_id"]
    assert reply.name == "note"
    assert reply.response == {"result": "Saved."}
    assert said["text"] == "Saved harbour."


async def _until(stream, kind: str) -> dict:
    while True:
        event = await anext(stream)
        if event.get("type") == kind:
            return event


async def test_browser_audio_is_forwarded_at_24k():
    link = FakeLink()
    session = _session(link, PCM_24K)
    await session.start()
    try:
        raw = b"\x03\x00\x04\x00"
        await session.send({"type": "input_audio_buffer.append", "audio": base64.b64encode(raw).decode()})
        assert link.audio == [(24000, raw)]
        link.push(LiveEvent(audio=raw, turn_complete=True))
        events = await asyncio.wait_for(_collect(session, 3), 1)
        assert base64.b64decode(events[1]["delta"]) == raw
    finally:
        await session.close()


# ── the bridges, on the fake socket ─────────────────────────────────────────

def _connect(script: list[LiveEvent]):
    link = FakeLink(script)

    async def connect(_key, session):
        body = session["session"]
        live = GeminiLiveSession(link, body["audio"]["input"]["format"], body["output_modalities"][0])
        await live.start()
        connect.session = session
        return live

    connect.link = link
    connect.session = None
    return connect


def test_phone_bridge_transcodes_barge_in_and_a_tool_that_updates_the_session():
    pcm = b"\x00\x10" * 2400   # 0.1 s at 24 kHz, becomes μ-law for the phone
    connect = _connect([
        LiveEvent(tool_calls=[("fc1", "update_preferences", {"max_monthly": 3000, "min_beds": 2})]),
        LiveEvent(turn_complete=True),
        LiveEvent(audio=pcm),
        LiveEvent(interrupted=True),
    ])
    with _client(voice_provider="gemini", gemini_api_key="g-test", boson_api_key=None) as c:
        c.app.state.connect_higgs = connect
        call = c.app.state.calls.open(caller="+14165550100")
        with c.websocket_connect("/twilio/media") as ws:
            ws.send_json({"event": "connected", "protocol": "Call"})
            ws.send_json(_start(call.call_id))
            media = ws.receive_json()
            assert media["event"] == "media"
            payload = base64.b64decode(media["media"]["payload"])
            assert payload and payload != pcm
            assert ws.receive_json() == {"event": "clear", "streamSid": "MZ1"}
            view = c.app.state.runtime.get(call.session_id)
            ws.send_json({"event": "media", "streamSid": "MZ1",
                          "media": {"track": "inbound", "payload": base64.b64encode(b"\xff" * 160).decode()}})
            ws.send_json({"event": "stop", "streamSid": "MZ1"})
            with pytest.raises(WebSocketDisconnect):
                ws.receive_json()
        assert (view.preferences.max_monthly, view.preferences.min_beds) == (3000, 2)
        assert connect.link.texts == [CALL_CONNECTED]
        assert connect.link.tools and connect.link.tools[0][0][0] == "fc1"
        assert connect.link.audio and connect.link.audio[0][0] == 16000
        assert connect.session["session"]["audio"]["input"]["format"] == {"type": "audio/pcmu"}
        assert c.get("/health").json()["higgs"] == "ok"
        assert c.app.state.calls.recent[-1].end_reason == "hangup"


def test_browser_bridge_keeps_24k_pcm_and_plays_the_goodbye(monkeypatch):
    from landline.voice import bridge
    monkeypatch.setattr(bridge, "HANG_UP_GRACE_S", 0.01)
    goodbye = b"\x00\x00" * 2400   # 0.1 s, so playback has something to finish
    connect = _connect([
        LiveEvent(audio=goodbye),
        LiveEvent(tool_calls=[("fc_bye", "end_session", {})]),
        LiveEvent(turn_complete=True),
    ])
    with _client(judge_audio_ready=True, voice_provider="gemini", gemini_api_key="g-test") as c:
        c.app.state.connect_higgs = connect
        start = c.post("/api/judge/start").json()
        with c.websocket_connect(start["ws_url"]) as ws:
            assert ws.receive_bytes() == goodbye
            with pytest.raises(WebSocketDisconnect):
                ws.receive_bytes()
        assert c.app.state.calls.recent[-1].end_reason == "end_session"
        assert connect.session["session"]["audio"]["output"]["format"] == PCM_24K
        # The goodbye was already in the audio, so the bridge does not ask for another reply.
        assert connect.link.texts == [CALL_CONNECTED]
        assert all(batch[0][1] == "end_session" for batch in connect.link.tools)


def test_twiml_follows_the_voice_provider_key():
    with _client(boson_api_key=None, voice_provider="gemini", gemini_api_key=None) as c:
        assert NOT_READY in _ring(c).text
    with _client(boson_api_key=None, voice_provider="gemini", gemini_api_key="g-test") as c:
        assert "<Stream" in _ring(c).text
    with _client(boson_api_key=None, voice_provider="higgs") as c:
        assert NOT_READY in _ring(c).text


def test_settings_load_voice_provider(monkeypatch):
    monkeypatch.setenv("VOICE_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "g-test")
    monkeypatch.setenv("GEMINI_LIVE_MODEL", "gemini-3.8-live")
    monkeypatch.setenv("GEMINI_LIVE_VOICE", "Kore")
    settings = Settings.load()
    assert settings.voice_provider == "gemini"
    assert settings.voice_api_key() == "g-test"
    assert settings.live_voice() == "Kore"
    assert settings.gemini_live_model == "gemini-3.8-live"
    monkeypatch.setenv("VOICE_PROVIDER", "higgs")
    monkeypatch.setenv("HIGGS_VOICE_RENTER", "default")
    higgs = Settings.load()
    assert higgs.live_voice() == "default"
    assert higgs.voice_api_key() == higgs.boson_api_key
