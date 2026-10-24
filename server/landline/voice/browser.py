"""The browser transport (A-03): a judge talks to the Concierge from /try with their own microphone.

Wire format, as `web/lib/judgeAudio.ts` documents it: binary frames both ways are PCM16 little-endian
mono at 24 kHz (Higgs `audio/pcm` at 24000), and a text frame `{"type": "clear"}` tells the page to drop
audio it has queued (barge-in). The page gets its socket from POST /api/judge/start, which starts the
session and holds a judge line for it; this socket only serves a session holding one.
"""
from __future__ import annotations

import base64
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from fastapi import WebSocket

from landline.config import Settings
from landline.orchestrator import SessionRuntime
from landline.voice.bridge import POLICY_VIOLATION, close_socket, converse
from landline.voice.calls import RenterCalls
from landline.voice.higgs import PCM_24K, ConnectHiggs
from landline.voice.line import Lines

if TYPE_CHECKING:
    from landline.api.judge import JudgeLines

log = logging.getLogger(__name__)

TRY_AGAIN_LATER = 1013   # every Higgs line is busy


class BrowserTransport:
    audio_format = PCM_24K
    bytes_per_second = 48000   # PCM16 mono at 24 kHz

    def __init__(self, ws: WebSocket) -> None:
        self.ws = ws

    async def receive_audio(self) -> str | None:
        while True:
            msg = await self.ws.receive()
            if msg["type"] == "websocket.disconnect":
                return None
            if data := msg.get("bytes"):
                return base64.b64encode(data).decode()
            # Text frames from the page carry nothing the call needs.

    async def send_audio(self, b64: str) -> None:
        await self.ws.send_bytes(base64.b64decode(b64))

    async def send_clear(self) -> None:
        await self.ws.send_json({"type": "clear"})


async def run_browser(ws: WebSocket, session_id: str, *, calls: RenterCalls, judges: JudgeLines,
                      runtime: SessionRuntime, connect_higgs: ConnectHiggs, settings: Settings,
                      set_status: Callable[[str], None] = lambda _: None, lines: Lines | None = None) -> None:
    """The judge's audio socket for `session_id`, a session /api/judge/start already started in the
    runtime. Ends the call and frees the judge line when it closes."""
    await ws.accept()
    # Holds the line to its deadline. False when it isn't held: never started, already freed, or the
    # socket came after the connect window, when another judge may hold that line.
    if not judges.connected(session_id):
        log.warning("browser audio refused: %r holds no judge line", session_id)
        await close_socket(ws, POLICY_VIOLATION)
        return
    try:
        call = calls.open(caller=None, session_id=session_id)
        if call is None:
            log.warning("browser audio refused for %s: every Higgs line is busy", session_id)
            await close_socket(ws, TRY_AGAIN_LATER)
            return
        calls.claim(call.call_id)
        log.info("browser call %s opened for session %s", call.call_id, session_id)
        await converse(BrowserTransport(ws), call, calls=calls, runtime=runtime, connect_higgs=connect_higgs,
                       settings=settings, set_status=set_status, lines=lines)
    finally:
        judges.hang_up(session_id)
        await close_socket(ws)
