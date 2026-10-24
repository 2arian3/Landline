"""Higgs Realtime client, ported from the A-01 spike (scripts/spike_higgs.py on agents/A-01-higgs-spike).

Higgs speaks the GA OpenAI Realtime protocol. The first message must be `session.update`, and
`session.created` only arrives after it. Audio is `audio/pcmu` both ways, so Twilio frames pass through
untouched. See .claude/skills/higgs-realtime/SKILL.md.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from websockets.asyncio.client import ClientConnection
from websockets.asyncio.client import connect as ws_connect
from websockets.exceptions import ConnectionClosed

WS_URL = "wss://api.boson.ai/v1/realtime?model=higgs-realtime"
CONNECT_TIMEOUT_S = 20
PCMU = {"type": "audio/pcmu"}                     # Twilio: G.711 μ-law, 8 kHz
PCM_24K = {"type": "audio/pcm", "rate": 24000}    # the browser: PCM16 little-endian mono, 24 kHz


class HiggsError(RuntimeError):
    """Higgs refused the session or closed before it was ready."""


def session_config(*, instructions: str, tools: list[dict[str, Any]], voice: str = "default",
                   audio_format: dict[str, Any] = PCMU, modality: str = "audio",
                   language: str | None = None) -> dict[str, Any]:
    """The `session.update` that opens every session: the same audio format in and out (pcmu for a
    phone, 24 kHz PCM for a browser), server VAD, and tools when there are any. `modality="text"` is for
    the eval simulator: the same agent answers in `response.output_text.*` events instead of audio.

    Higgs detects the spoken language by itself. `language` (an ISO-639-1 code, from code) only hints the
    transcription of the first turns; after the first reply the transcription follows the conversation on
    its own (docs.boson.ai, sessions guide). A renter call does not pass a language from an earlier one."""
    transcription: dict[str, Any] = {"model": "higgs-stt-3.1"}
    if language:
        transcription["language"] = language
    return {"type": "session.update", "session": {
        "model": "higgs-realtime",
        "instructions": instructions,
        "output_modalities": [modality],
        "audio": {
            "input": {"format": dict(audio_format),
                      "transcription": transcription,
                      "turn_detection": {"type": "server_vad"}},
            "output": {"format": dict(audio_format), "voice": voice},
        },
        "tools": tools,
        "tool_choice": "auto" if tools else "none",
        "temperature": 0.3,
    }}


class HiggsSession:
    """One open Higgs socket: send events, read the ones that come back."""

    def __init__(self, ws: ClientConnection) -> None:
        self.ws = ws

    async def send(self, event: dict[str, Any]) -> None:
        await self.ws.send(json.dumps(event))

    async def recv(self) -> dict[str, Any]:
        return json.loads(await self.ws.recv())

    async def events(self) -> AsyncIterator[dict[str, Any]]:
        """Every event until Higgs closes the socket."""
        while True:
            try:
                yield await self.recv()
            except ConnectionClosed:
                return

    async def close(self) -> None:
        await self.ws.close()


# What the bridge calls to get a ready session. Tests swap in a fake through app.state.connect_higgs.
ConnectHiggs = Callable[[str, dict[str, Any]], Awaitable[Any]]


async def connect(api_key: str, session: dict[str, Any]) -> HiggsSession:
    """Opens a socket, sends `session` (a `session.update`), and returns once Higgs answers
    `session.created`. Raises HiggsError if Higgs answers `error` or closes first."""
    ws = await ws_connect(WS_URL, additional_headers={"Authorization": f"Bearer {api_key}"},
                          subprotocols=["realtime"], max_size=8 * 1024 * 1024,
                          open_timeout=CONNECT_TIMEOUT_S, close_timeout=5)
    higgs = HiggsSession(ws)
    try:
        await higgs.send(session)
        async with asyncio.timeout(CONNECT_TIMEOUT_S):
            while True:
                event = await higgs.recv()
                if event.get("type") == "session.created":
                    return higgs
                if event.get("type") == "error":
                    raise HiggsError(f"session.update rejected: {event.get('error')}")
    except ConnectionClosed as exc:
        raise HiggsError(f"closed before session.created: {exc.code} {exc.reason}") from exc
    except BaseException:
        with contextlib.suppress(Exception):
            await ws.close()
        raise
