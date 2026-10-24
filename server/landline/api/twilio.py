"""Twilio webhooks for the renter's call (owner: Danial). The TwiML webhook mints the call and hands it to
our media stream; the stream runs the Concierge bridge (landline/voice/bridge.py)."""
from __future__ import annotations

import logging
from urllib.parse import parse_qsl

from fastapi import APIRouter, HTTPException, Request, WebSocket
from fastapi.responses import Response

from landline.voice import twilio
from landline.voice.bridge import run_bridge

log = logging.getLogger(__name__)
router = APIRouter()

NOT_READY = "Sorry, Landline isn't taking calls right now. Please try again later."
BUSY = "All our lines are busy. Please call again in a minute."
MEDIA_PATH = "/twilio/media"


@router.post("/twiml/renter")
async def twiml_renter(request: Request) -> Response:
    """Twilio's "a call comes in" webhook for our number. Refuses anything Twilio didn't sign."""
    settings = request.app.state.settings
    params = dict(parse_qsl((await request.body()).decode(), keep_blank_values=True))
    signed = request.headers.get("X-Twilio-Signature", "")
    if not settings.twilio_auth_token or not twilio.valid_signature(
            settings.twilio_auth_token, _signed_url(request), params, signed):
        raise HTTPException(403, "invalid Twilio signature")
    if not settings.boson_api_key:
        return _xml(twilio.twiml_say_hangup(NOT_READY))
    call = request.app.state.calls.open(caller=params.get("From"))
    if call is None:
        return _xml(twilio.twiml_say_hangup(BUSY))
    log.info("renter call %s opened session %s", call.call_id, call.session_id)
    return _xml(twilio.twiml_stream(_ws_base(request) + MEDIA_PATH, call.call_id))


@router.websocket(MEDIA_PATH)
async def twilio_media(ws: WebSocket) -> None:
    state = ws.app.state
    await run_bridge(ws, calls=state.calls, runtime=state.runtime, connect_higgs=state.connect_higgs,
                     settings=state.settings, set_status=lambda status: setattr(state, "higgs", status))


def _signed_url(request: Request) -> str:
    """The URL Twilio signed: our public origin plus the path, since nginx hands us plain http."""
    public = request.app.state.settings.public_url
    if not public:
        return str(request.url)
    query = f"?{request.url.query}" if request.url.query else ""
    return f"{public}{request.url.path}{query}"


def _ws_base(request: Request) -> str:
    base = request.app.state.settings.public_url or str(request.base_url).rstrip("/")
    if base.startswith("https://"):
        return "wss://" + base.removeprefix("https://")
    return "ws://" + base.removeprefix("http://")


def _xml(body: str) -> Response:
    return Response(content=body, media_type="application/xml")
