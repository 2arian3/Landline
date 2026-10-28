"""Twilio webhooks for the renter's call (owner: Danial). The TwiML webhook mints the call and hands it to
our media stream; the stream runs the Concierge bridge (landline/voice/bridge.py). A listing agent calling
back about a call that reached no one goes to the Caller for that listing instead (A-13b,
voice/callbacks.py)."""
from __future__ import annotations

import asyncio
import logging
from urllib.parse import parse_qsl

from fastapi import APIRouter, HTTPException, Request, WebSocket
from fastapi.responses import Response

from landline.voice import callbacks, twilio
from landline.voice.bridge import POLICY_VIOLATION, TwilioTransport, _wait_for_start, close_socket, run_bridge
from landline.voice.outbound import MACHINE

log = logging.getLogger(__name__)
router = APIRouter()

NOT_READY = "Sorry, Landline isn't taking calls right now. Please try again later."
BUSY = "All our lines are busy. Please call again in a minute."
MEDIA_PATH = "/twilio/media"
OUTBOUND_MEDIA_PATH = "/twilio/outbound/media"
CALLBACK_PATH = "/twiml/callback"


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
    choices = await _callback_choices(request, params)
    if len(choices) == 1 and (answered := await _answer_callback(request, choices[0], params)) is not None:
        return answered
    if len(choices) > 1:
        return _ask_which(choices, tries=1)
    return _renter(request, params)


@router.post(CALLBACK_PATH)
async def twiml_callback(request: Request) -> Response:
    """Which listing a callback is about (A-13b): Twilio posts the key pressed or the words said, or nothing.
    Code matches it to the list it read out; after TRIES without a match, the call ends politely."""
    params = await _signed_params(request)
    try:
        tries = max(1, int(request.query_params.get("try", "1")))
    except ValueError:
        tries = 1
    choices = await _callback_choices(request, params)
    if not choices:   # the lookup failed meanwhile, or the session went: the renter's Concierge, as before
        return _renter(request, params)
    picked = choices[0] if len(choices) == 1 else callbacks.pick(
        choices, params.get("Digits", ""), params.get("SpeechResult", ""))
    if picked is not None:
        answered = await _answer_callback(request, picked, params)
        return answered if answered is not None else _renter(request, params)
    if tries < callbacks.TRIES:
        return _ask_which(choices, tries=tries + 1)
    log.info("callback from %s: no listing picked after %d tries", _tail(params.get("From")), tries)
    return _xml(twilio.twiml_say_hangup(callbacks.GIVE_UP))


def _renter(request: Request, params: dict[str, str]) -> Response:
    """A renter's call: a new session, streamed to the Concierge."""
    call = request.app.state.calls.open(caller=params.get("From"), called=params.get("To"))
    if call is None:
        return _xml(twilio.twiml_say_hangup(BUSY))
    log.info("renter call %s opened session %s", call.call_id, call.session_id)
    return _xml(twilio.twiml_stream(_ws_base(request) + MEDIA_PATH, call.call_id))


async def _callback_choices(request: Request, params: dict[str, str]) -> list[callbacks.Missed]:
    """The missed listing calls this caller may be calling back about, that a runner here can answer; [] for
    a renter, and whenever the lookup fails or is slow (logged), so the Concierge takes the call."""
    state = request.app.state
    dispatcher = getattr(state, "dispatcher", None)
    if dispatcher is None:
        return []
    choices = await callbacks.find(state.runtime, params.get("From"), dispatcher.clock())
    return [c for c in choices if dispatcher.callback_runner(c.session_id) is not None]


async def _answer_callback(request: Request, missed: callbacks.Missed,
                           params: dict[str, str]) -> Response | None:
    """The Caller for `missed`'s listing takes the call on the listing calls' stream; None if it can't."""
    dispatcher = request.app.state.dispatcher
    try:
        call_id = await dispatcher.callback(missed, params.get("From"), params.get("CallSid"))
    except Exception:
        log.exception("callback about %s couldn't start; the call goes to the Concierge", missed.call_id)
        return None
    if call_id is None:
        return None
    return _xml(twilio.twiml_stream(_ws_base(request) + OUTBOUND_MEDIA_PATH, call_id))


def _ask_which(choices: list[callbacks.Missed], tries: int) -> Response:
    return _xml(twilio.twiml_gather(callbacks.ask(choices, again=tries > 1), f"{CALLBACK_PATH}?try={tries}",
                                    hints=[c.address for c in choices]))


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


def _tail(number: str | None) -> str:
    return f"…{(number or '')[-4:]}"
