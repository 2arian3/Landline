"""Gemini Live behind the Higgs event dialect the bridge already speaks.

The Concierge, Caller, sim and line send `session.update`, `input_audio_buffer.append`,
`conversation.item.create` and `response.create`, and read `response.output_audio.delta`,
`response.function_call_arguments.done` and the rest. This session translates that to the Gemini
Live WebSocket (`google-genai`) and back. Audio formats stay the transport's: μ-law 8 kHz on the
phone, PCM16 24 kHz in the browser. See .claude/skills/gemini-live/SKILL.md.

`connect(api_key, session)` matches `higgs.connect`, so `app.state.connect_higgs` can point here
without the bridge changing. Setup cannot be updated on an open socket; the session dict is applied
once, at connect.
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from landline.voice.audio_codec import Transcoder

log = logging.getLogger(__name__)

DEFAULT_MODEL = "gemini-3.8-live"
DEFAULT_VOICE = "Aoede"
CONNECT_TIMEOUT_S = 20


class GeminiError(RuntimeError):
    """Gemini refused the session or closed before it was ready."""


@dataclass
class LiveEvent:
    """One thing the Live socket told us, already pulled apart. Tests build these directly."""

    audio: bytes | None = None
    text: str | None = None
    interrupted: bool = False
    input_transcript: str | None = None
    input_interim: str | None = None
    output_transcript: str | None = None
    turn_complete: bool = False
    tool_calls: list[tuple[str, str, Any]] = field(default_factory=list)
    cancelled: list[str] = field(default_factory=list)
    activity_start: bool = False
    error: str | None = None


def live_config(session: dict[str, Any], voice: str) -> dict[str, Any]:
    """The Live setup for a Higgs `session.update`. Text mode is the eval simulator's modality."""
    body = session.get("session") or session
    text = (body.get("output_modalities") or ["audio"])[0] == "text"
    chosen = ((body.get("audio") or {}).get("output") or {}).get("voice") or ""
    if not chosen or chosen == "default":
        chosen = voice or DEFAULT_VOICE
    config: dict[str, Any] = {
        "response_modalities": ["TEXT" if text else "AUDIO"],
        "system_instruction": body.get("instructions") or "",
        "temperature": body.get("temperature", 0.3),
        # A renter call can run the full fifteen minutes. Compress instead of dying at the window.
        "context_window_compression": {"sliding_window": {}},
    }
    decls = [_declaration(tool) for tool in body.get("tools") or [] if tool.get("type") == "function"]
    if decls:
        config["tools"] = [{"function_declarations": decls}]
    if not text:
        config["speech_config"] = {
            "voice_config": {"prebuilt_voice_config": {"voice_name": chosen}},
        }
        config["input_audio_transcription"] = {}
        config["output_audio_transcription"] = {}
        config["realtime_input_config"] = {"automatic_activity_detection": {"disabled": False}}
    return config


def _declaration(tool: dict[str, Any]) -> dict[str, Any]:
    decl: dict[str, Any] = {"name": tool["name"], "description": tool.get("description") or ""}
    if tool.get("parameters"):
        decl["parameters_json_schema"] = tool["parameters"]
    return decl


class GeminiLiveSession:
    """One Live socket that speaks the Higgs dialect on `send` / `events`."""

    def __init__(self, link: Any, audio_format: dict[str, Any], modality: str = "audio") -> None:
        self.link = link
        self.modality = modality
        self._codec = Transcoder(audio_format)
        self._queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        self._reader: asyncio.Task | None = None
        self._flush_task: asyncio.Task | None = None
        self._closed = False
        self._heard = 0
        self._flush_gen = 0
        self._waiting: set[str] = set()
        self._names: dict[str, str] = {}
        self._cancelled: set[str] = set()
        self._outputs: list[tuple[str, str, str]] = []
        self._texts: list[str] = []
        self._wants_speech = False
        self._suppress = False
        self._open = False
        self._resp_n = 0
        self._resp_id = ""
        self._item_id = ""
        self._tools_out: list[dict[str, str]] = []
        self._out_text: list[str] = []
        self._out_transcript: list[str] = []
        self._had_audio = False
        self._speaking = False
        self._user_n = 0
        self._user_item = ""
        self._in_parts: list[str] = []

    async def start(self) -> None:
        if self._reader is None:
            self._reader = asyncio.create_task(self._run())

    async def send(self, event: dict[str, Any]) -> None:
        if self._closed:
            return
        kind = event.get("type")
        if kind == "input_audio_buffer.append":
            raw = base64.b64decode(event.get("audio") or "")
            self._heard += len(raw)
            converted = self._codec.to_gemini(event.get("audio") or "")
            if converted:
                pcm, rate = converted
                await self.link.send_audio(pcm, rate)
            return
        if kind == "conversation.item.create":
            await self._item(event.get("item") or {})
            return
        if kind == "response.create":
            await self._respond()

    async def events(self) -> AsyncIterator[dict[str, Any]]:
        while True:
            event = await self._queue.get()
            if event is None:
                return
            yield event

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._flush_gen += 1
        task = self._flush_task
        if task is not None and not task.done():
            task.cancel()
        with contextlib.suppress(Exception):
            await self.link.close()
        reader = self._reader
        if reader is not None and reader is not asyncio.current_task() and not reader.done():
            reader.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await reader

    async def _item(self, item: dict[str, Any]) -> None:
        if item.get("type") == "function_call_output":
            cid = str(item.get("call_id") or "")
            self._waiting.discard(cid)
            if cid in self._cancelled:
                return
            name = self._names.get(cid) or str(item.get("name") or "")
            self._outputs.append((cid, name, str(item.get("output") or "")))
            if not self._waiting:
                if self._wants_speech:
                    await self._send_tools(suppress=False)
                else:
                    self._arm_flush()
            return
        if item.get("type") == "message":
            text = _input_text(item)
            if text:
                self._texts.append(text)

    async def _respond(self) -> None:
        """`response.create` after tool output is the one reply. A second one for the same response
        is a no-op: Higgs answers 400 "No user input" (#175), and a client turn here would interrupt
        the reply the tool response just started."""
        self._wants_speech = True
        self._flush_gen += 1
        if self._outputs and not self._waiting:
            await self._send_tools(suppress=False)
        elif self._texts and not self._outputs:
            await self._send_text()

    def _arm_flush(self) -> None:
        """Tool output with no `response.create` yet. The bridge sends the create in the same turn,
        before the loop yields, so a flush that survives that is the #219 case: answer the tool
        (an open call stalls the session) and drop the follow-up speech."""
        self._flush_gen += 1
        gen = self._flush_gen
        self._flush_task = asyncio.create_task(self._flush(gen))

    async def _flush(self, gen: int) -> None:
        await asyncio.sleep(0)
        if gen != self._flush_gen or self._closed:
            return
        if self._outputs and not self._waiting:
            await self._send_tools(suppress=not self._wants_speech)

    async def _send_tools(self, *, suppress: bool) -> None:
        batch, self._outputs = self._outputs, []
        self._wants_speech = False
        self._flush_gen += 1
        if suppress:
            self._suppress = True
        await self.link.send_tools(batch)

    async def _send_text(self) -> None:
        text = "\n".join(self._texts)
        self._texts.clear()
        self._wants_speech = False
        self._suppress = False
        await self.link.send_text(text, turn_complete=True)

    async def _run(self) -> None:
        try:
            async for ev in self.link.incoming():
                if self._closed:
                    break
                await self._on(ev)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if not self._closed:
                log.warning("Gemini session closed: %s", exc)
                await self._put({"type": "error", "error": str(exc)})
        finally:
            await self._put(None)

    async def _on(self, ev: LiveEvent) -> None:
        if ev.error:
            await self._put({"type": "error", "error": ev.error})
            return
        if ev.cancelled:
            for cid in ev.cancelled:
                self._cancelled.add(cid)
                self._waiting.discard(cid)
        if ev.interrupted or ev.activity_start:
            self._suppress = False
            await self._speech_started()
        if ev.interrupted and self._open:
            await self._finish("cancelled")
        if ev.input_interim:
            await self._speech_started()
        if ev.input_transcript:
            await self._speech_started()
            self._in_parts.append(ev.input_transcript)
        if ev.audio or ev.text or ev.tool_calls:
            await self._flush_input()
        if ev.tool_calls:
            for cid, name, _args in ev.tool_calls:
                self._waiting.add(cid)
                self._names[cid] = name
            for cid, name, args in ev.tool_calls:
                if cid in self._cancelled:
                    self._waiting.discard(cid)
                    continue
                await self._open_response()
                self._tools_out.append({"name": name, "call_id": cid})
                await self._put({
                    "type": "response.function_call_arguments.done",
                    "call_id": cid, "name": name, "arguments": _args_json(args),
                    "response_id": self._resp_id,
                })
        if ev.audio and not self._suppress:
            spoken = self._codec.from_gemini(ev.audio)
            if spoken:
                await self._open_response()
                self._had_audio = True
                await self._put({
                    "type": "response.output_audio.delta", "delta": spoken, "item_id": self._item_id,
                })
        if ev.text and not self._suppress:
            await self._open_response()
            self._out_text.append(ev.text)
            await self._put({
                "type": "response.output_text.delta", "delta": ev.text, "item_id": self._item_id,
            })
        if ev.output_transcript and not self._suppress:
            self._out_transcript.append(ev.output_transcript)
        if ev.turn_complete:
            await self._finish("completed")

    async def _open_response(self) -> None:
        if self._open:
            return
        self._resp_n += 1
        self._resp_id = f"r{self._resp_n}"
        self._item_id = f"out_{self._resp_n}"
        self._open = True
        self._tools_out = []
        self._out_text = []
        self._out_transcript = []
        self._had_audio = False
        await self._put({"type": "response.created", "response": {"id": self._resp_id}})

    async def _finish(self, status: str) -> None:
        # A barge-in cancels the reply first. speech_stopped waits for this turn to complete, so the
        # line stays "they're talking" until Gemini finishes the interruption, not for one event.
        if status != "cancelled":
            await self._flush_input()
        if not self._open:
            if status == "completed":
                await self._flush_input()
                # A follow-up we refused to play ends here, with no response the bridge has to settle.
                self._suppress = False
            return
        # #219: the reply already spoke, so the bridge sent no response.create. The tool still had
        # to be answered. Drop the follow-up's words; the bridge already settled the turn.
        if self._suppress and not self._tools_out:
            self._open = False
            self._suppress = False
            self._out_text = []
            self._out_transcript = []
            return
        transcript = "".join(self._out_transcript).strip()
        if transcript:
            await self._put({
                "type": "response.output_audio_transcript.done",
                "transcript": transcript, "item_id": self._item_id,
            })
        said = "".join(self._out_text)
        if said:
            await self._put({"type": "response.output_text.done", "text": said, "item_id": self._item_id})
        output: list[dict[str, str]] = []
        if self._had_audio or said or transcript:
            output.append({"type": "message"})
        output.extend({"type": "function_call", "name": t["name"], "call_id": t["call_id"]}
                      for t in self._tools_out)
        resp_id, self._open = self._resp_id, False
        await self._put({
            "type": "response.done",
            "response": {"id": resp_id, "status": status, "output": output},
        })

    async def _flush_input(self) -> None:
        if not self._in_parts and not self._speaking:
            return
        text = "".join(self._in_parts)
        self._in_parts.clear()
        if text and not self._speaking:
            await self._speech_started()
        await self._speech_stopped()
        if text:
            await self._put({
                "type": "conversation.item.input_audio_transcription.completed",
                "transcript": text, "item_id": self._user_item,
            })

    async def _speech_started(self) -> None:
        if self._speaking:
            return
        self._speaking = True
        self._suppress = False
        self._user_n += 1
        self._user_item = f"in_{self._user_n}"
        await self._put({
            "type": "input_audio_buffer.speech_started",
            "item_id": self._user_item, "audio_start_ms": self._codec.heard_ms(self._heard),
        })

    async def _speech_stopped(self) -> None:
        if not self._speaking:
            return
        self._speaking = False
        await self._put({
            "type": "input_audio_buffer.speech_stopped",
            "item_id": self._user_item, "audio_end_ms": self._codec.heard_ms(self._heard),
        })

    async def _put(self, event: dict[str, Any] | None) -> None:
        if event is None or not self._closed:
            await self._queue.put(event)


def _input_text(item: dict[str, Any]) -> str:
    parts = []
    for piece in item.get("content") or []:
        if isinstance(piece, dict) and piece.get("text"):
            parts.append(str(piece["text"]))
        elif isinstance(piece, str):
            parts.append(piece)
    return "\n".join(parts)


def _args_json(raw: Any) -> str:
    if raw is None or raw == "":
        return "{}"
    if isinstance(raw, str):
        return raw
    if hasattr(raw, "model_dump"):
        raw = raw.model_dump()
    return json.dumps(raw)


def events_of(msg: Any) -> list[LiveEvent]:
    """SDK messages to `LiveEvent`s. `data` repeats audio that `model_turn` already carries."""
    out: list[LiveEvent] = []
    if getattr(msg, "go_away", None) is not None:
        out.append(LiveEvent(error="session ending"))
    sc = getattr(msg, "server_content", None)
    saw_audio = False
    if sc is not None:
        if getattr(sc, "interrupted", False):
            out.append(LiveEvent(interrupted=True))
        turn = getattr(sc, "model_turn", None)
        if turn is not None:
            for part in getattr(turn, "parts", None) or []:
                inline = getattr(part, "inline_data", None)
                data = getattr(inline, "data", None) if inline is not None else None
                if data:
                    saw_audio = True
                    out.append(LiveEvent(audio=bytes(data)))
                text = getattr(part, "text", None)
                if text:
                    out.append(LiveEvent(text=text))
        heard = getattr(sc, "input_transcription", None)
        if heard is not None and getattr(heard, "text", None):
            out.append(LiveEvent(input_transcript=heard.text))
        interim = getattr(sc, "interim_input_transcription", None)
        if interim is not None and getattr(interim, "text", None):
            out.append(LiveEvent(input_interim=interim.text))
        spoken = getattr(sc, "output_transcription", None)
        if spoken is not None and getattr(spoken, "text", None):
            out.append(LiveEvent(output_transcript=spoken.text))
        if getattr(sc, "turn_complete", False):
            out.append(LiveEvent(turn_complete=True))
    if not saw_audio and getattr(msg, "data", None):
        out.append(LiveEvent(audio=bytes(msg.data)))
    call = getattr(msg, "tool_call", None)
    if call is not None:
        tools = []
        for fn in getattr(call, "function_calls", None) or []:
            tools.append((str(getattr(fn, "id", "") or ""), str(getattr(fn, "name", "") or ""),
                          getattr(fn, "args", None)))
        if tools:
            out.append(LiveEvent(tool_calls=tools))
    activity = getattr(msg, "voice_activity", None)
    if activity is not None and "START" in str(getattr(activity, "voice_activity_type", "") or ""):
        out.append(LiveEvent(activity_start=True))
    cancelled = getattr(msg, "tool_call_cancellation", None)
    if cancelled is not None:
        ids = [str(i) for i in (getattr(cancelled, "ids", None) or [])]
        if ids:
            out.append(LiveEvent(cancelled=ids))
    return out


class _GenaiLink:
    """The `google-genai` Live socket. Imported here so tests of the dialect don't need a key."""

    def __init__(self, session: Any, closer: Any) -> None:
        self._session, self._closer = session, closer
        self._closed = False

    async def send_audio(self, pcm: bytes, rate: int) -> None:
        from google.genai import types
        await self._session.send_realtime_input(
            audio=types.Blob(data=pcm, mime_type=f"audio/pcm;rate={rate}"))

    async def send_text(self, text: str, turn_complete: bool = True) -> None:
        await self._session.send_client_content(
            turns={"role": "user", "parts": [{"text": text}]}, turn_complete=turn_complete)

    async def send_tools(self, responses: list[tuple[str, str, str]]) -> None:
        from google.genai import types
        fns = [types.FunctionResponse(id=cid, name=name, response={"result": output})
               for cid, name, output in responses]
        await self._session.send_tool_response(function_responses=fns)

    async def incoming(self) -> AsyncIterator[LiveEvent]:
        while not self._closed:
            async for msg in self._session.receive():
                if self._closed:
                    return
                for ev in events_of(msg):
                    yield ev

    async def close(self) -> None:
        self._closed = True
        with contextlib.suppress(Exception):
            await self._closer.__aexit__(None, None, None)


async def _open(api_key: str, model: str, config: dict[str, Any]) -> _GenaiLink:
    from google import genai
    client = genai.Client(api_key=api_key)
    closer = client.aio.live.connect(model=model, config=config)
    try:
        async with asyncio.timeout(CONNECT_TIMEOUT_S):
            session = await closer.__aenter__()
    except Exception as exc:
        with contextlib.suppress(Exception):
            await closer.__aexit__(None, None, None)
        raise GeminiError(str(exc)) from exc
    return _GenaiLink(session, closer)


def _format_of(session: dict[str, Any]) -> dict[str, Any]:
    body = session.get("session") or {}
    return ((body.get("audio") or {}).get("input") or {}).get("format") or {"type": "audio/pcmu"}


def _modality_of(session: dict[str, Any]) -> str:
    body = session.get("session") or {}
    return (body.get("output_modalities") or ["audio"])[0]


async def connect(api_key: str, session: dict[str, Any], *, model: str = DEFAULT_MODEL,
                  voice: str = DEFAULT_VOICE) -> GeminiLiveSession:
    """Opens a Live session from a Higgs `session.update` and returns it ready to `send`."""
    if not api_key:
        raise GeminiError("GEMINI_API_KEY is not set")
    link = await _open(api_key, model, live_config(session, voice))
    live = GeminiLiveSession(link, _format_of(session), _modality_of(session))
    await live.start()
    return live


def connect_for(settings: Any):
    """`higgs.connect`'s signature, with this process's Gemini model and voice."""

    async def _connect(api_key: str, session: dict[str, Any]) -> GeminiLiveSession:
        return await connect(api_key, session, model=settings.gemini_live_model,
                             voice=settings.gemini_live_voice)

    return _connect


def session_connect(settings: Any):
    """The opener `app.state.connect_higgs` holds: Gemini Live, or Higgs when that is the provider."""
    if getattr(settings, "voice_provider", "higgs") == "gemini":
        return connect_for(settings)
    from landline.voice.higgs import connect as higgs_connect
    return higgs_connect
