"""Twilio webhooks for the renter's call (owner: Danial). The TwiML webhook mints the call and hands it to
our media stream; the stream runs the Concierge bridge (landline/voice/bridge.py)."""
from __future__ import annotations

import asyncio
import logging
from urllib.parse import parse_qsl

from fastapi import APIRouter, HTTPException, Request, WebSocket
from fastapi.responses import Response

from landline.voice import twilio
from landline.voice.bridge import POLICY_VIOLATION, TwilioTransport, _wait_for_start, close_socket, run_bridge
from landline.voice.outbound import MACHINE

log = logging.getLogger(__name__)
router = APIRouter()

NOT_READY = "Sorry, Landline isn't taking calls right now. Please try again later."
BUSY = "All our lines are busy. Please call again in a minute."
MEDIA_PATH = "/twilio/media"
OUTBOUND_MEDIA_PATH = "/twilio/outbound/media"


@router.post("/twiml/renter")
async def twiml_renter(request: Request) -> Response:
    """Twilio's "a call comes in" webhook for our number. Refuses anything Twilio didn't sign."""
    settings = request.app.state.settings
    params = dict(parse_qsl((await request.body()).decode(), keep_blank_values=True))
    signed = request.headers.get("X-Twilio-Signature", "")
    if not settings.twilio_auth_token or not twilio.valid_signature(
            settings.twilio_auth_token, _signed_url(request), params, signed):
        raise HTTPException(403, "invalid Twilio signature")
    if not settings.voice_api_key():
        return _xml(twilio.twiml_say_hangup(NOT_READY))
    if request.app.state.drain.draining:   # restarting (#167): a call taken now would be cut
        return _xml(twilio.twiml_say_hangup(BUSY))
    call = request.app.state.calls.open(caller=params.get("From"), called=params.get("To"))
    if call is None:
        return _xml(twilio.twiml_say_hangup(BUSY))
    log.info("renter call %s opened session %s", call.call_id, call.session_id)
    return _xml(twilio.twiml_stream(_ws_base(request) + MEDIA_PATH, call.call_id))


@router.websocket(MEDIA_PATH)
async def twilio_media(ws: WebSocket) -> None:
    state = ws.app.state
    await run_bridge(ws, calls=state.calls, runtime=state.runtime, connect_higgs=state.connect_higgs,
                     settings=state.settings, set_status=lambda status: setattr(state, "higgs", status),
                     texter=state.texter, lines=state.lines, clips=state.clips)


# ── listing calls (outbound, voice/outbound.py) ─────────────────────────────

@router.post("/twiml/outbound/{call_id}")
async def twiml_outbound(call_id: str, request: Request) -> Response:
    """Twilio asks what to do the moment a listing call is answered; machine detection reports on /amd."""
    params = await _signed_params(request)
    calls = request.app.state.outbound
    if calls.runner is None or calls.get(call_id) is None:
        return _xml(twilio.twiml_hangup())
    answered_by = params.get("AnsweredBy", "")
    log.info("listing call %s answered by %s", call_id, answered_by or "?")
    if answered_by in MACHINE:
        await calls.set_state(call_id, "voicemail")
    return _xml(calls.runner.answered_twiml(call_id, answered_by, _ws_base(request) + OUTBOUND_MEDIA_PATH))


@router.post("/twilio/outbound/status/{call_id}", status_code=204)
async def outbound_status(call_id: str, request: Request) -> Response:
    """Twilio's status callback for a listing call: ringing, then how it ended."""
    params = await _signed_params(request)
    calls = request.app.state.outbound
    status = params.get("CallStatus", "")
    if status == "ringing":
        await calls.set_state(call_id, "ringing")
    elif (reason := calls.end_reason(call_id, status)) is not None:
        calls.finalize(call_id, reason)
    return Response(status_code=204)


@router.post("/twilio/outbound/amd/{call_id}", status_code=204)
async def outbound_amd(call_id: str, request: Request) -> Response:
    """Twilio's machine detection for a listing call, decided while the Caller talks (AsyncAmd)."""
    params = await _signed_params(request)
    calls = request.app.state.outbound
    answered_by = params.get("AnsweredBy", "")
    log.info("listing call %s detected as %s after %s ms", call_id, answered_by or "?",
             params.get("MachineDetectionDuration", "?"))
    if calls.runner is not None:
        await calls.runner.detected(call_id, answered_by)
    return Response(status_code=204)


@router.websocket(OUTBOUND_MEDIA_PATH)
async def outbound_media(ws: WebSocket) -> None:
    """The media stream of an answered listing call. It hands the line to the runner that dialled it and
    stays open until that call ends."""
    calls = ws.app.state.outbound
    await ws.accept()
    start = await _wait_for_start(ws)
    call_id = str(((start or {}).get("customParameters") or {}).get("call_id", ""))
    call = calls.claim(call_id) if start else None
    if call is None:
        log.warning("outbound media stream refused: unknown or reused call_id %r", call_id)
        await close_socket(ws, POLICY_VIOLATION)
        return
    log.info("listing call %s stream started", call_id)
    try:
        await calls.set_state(call_id, "live")
        if not call.media.done():
            call.media.set_result(TwilioTransport(ws, str(start.get("streamSid", ""))))
        await asyncio.shield(call.ended)
    finally:
        await close_socket(ws)


async def _signed_params(request: Request) -> dict[str, str]:
    """The POST fields of a webhook Twilio signed; anything else is refused with 403."""
    settings = request.app.state.settings
    params = dict(parse_qsl((await request.body()).decode(), keep_blank_values=True))
    signed = request.headers.get("X-Twilio-Signature", "")
    if not settings.twilio_auth_token or not twilio.valid_signature(
            settings.twilio_auth_token, _signed_url(request), params, signed):
        raise HTTPException(403, "invalid Twilio signature")
    return params


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
