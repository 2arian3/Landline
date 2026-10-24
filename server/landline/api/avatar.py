"""The Concierge's face on the renter's board (owner: Danial, A-11). A page showing a session asks here while
the renter's call is live: the first ask starts the avatar (voice/avatar.py), and each ask gets its own token
that can only watch the call's LiveKit room. The answers:

- 200 `{url, room, identity, token}`: join `room` at `url` with `token`; the avatar is the participant named
  `identity`, there a few seconds after the first ask.
- 404 `{reason: "off"}`: this server has no avatars (no LiveKit keys). Don't ask again.
- 404 `{reason: "no_call"}`: no live renter call for this session. Ask again while the brief is open.
- 503 `{reason: "unavailable", retry_after_s}`: the last start was refused (no free avatar slot, an outage).
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from landline.voice.avatar import RETRY_S

router = APIRouter()


@router.post("/api/sessions/{session_id}/avatar")
async def avatar(session_id: str, request: Request) -> JSONResponse:
    avatars = request.app.state.avatars
    if not avatars.ready:
        return JSONResponse({"reason": "off"}, status_code=404)
    face = avatars.get(session_id)
    if face is None:
        return JSONResponse({"reason": "no_call"}, status_code=404)
    face.want()
    if face.state == "failed":
        return JSONResponse({"reason": "unavailable", "retry_after_s": RETRY_S}, status_code=503)
    return JSONResponse(face.viewer(), headers={"Cache-Control": "no-store"})
