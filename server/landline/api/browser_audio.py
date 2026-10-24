"""Judge mode's audio socket (owner: Danial, A-03). The /try page streams the judge's microphone here and
plays back the Concierge; landline/voice/browser.py runs the call. The path is `Settings.judge_ws_path`,
which POST /api/judge/start hands the page."""
from __future__ import annotations

from fastapi import APIRouter, WebSocket

from landline.voice.browser import run_browser

router = APIRouter()
JUDGE_AUDIO_PATH = "/api/judge/{session_id}/audio"


@router.websocket(JUDGE_AUDIO_PATH)
async def judge_audio(ws: WebSocket, session_id: str) -> None:
    state = ws.app.state
    await run_browser(ws, session_id, calls=state.calls, judges=state.judges, runtime=state.runtime,
                      connect_higgs=state.connect_higgs, settings=state.settings,
                      set_status=lambda status: setattr(state, "higgs", status))
