"""Read side for the web app (owner: Mahyar). Shapes are in docs/CONTRACTS.md."""
from __future__ import annotations

import asyncio
import json
import math
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from landline.api.judge import client_ip
from landline.contracts import CataloguePoint, Listing, Preferences, SessionView

router = APIRouter()
HEARTBEAT_S = 5   # well inside the page's 6.5 s "Reconnecting…" threshold
BUSY = "All lines are busy. Try again in a minute."
CLOSED = "Judge mode opens soon."


@router.get("/health")
def health(request: Request) -> dict:
    """CONTRACTS.md: {status, listings, sessions, transport, higgs}. `sessions` counts the demo sessions
    held in memory (a restart wipes them); `higgs` is whatever the voice layer last reported."""
    state = request.app.state
    cat = state.catalogue
    return {"status": "ok", "listings": len(cat.listings), "catalogue": cat.origin,
            "sessions": len(state.sessions.views), "transport": state.settings.transport,
            "higgs": state.higgs}


@router.get("/api/catalogue", response_model=list[CataloguePoint])
def catalogue(request: Request) -> list[CataloguePoint]:
    return [
        CataloguePoint(listing_id=x.listing_id, lat=x.lat, lng=x.lng,
                       monthly_cost=x.monthly_cost, beds=x.beds)
        for x in request.app.state.catalogue.listings if x.lat is not None and x.lng is not None
    ]


@router.get("/api/listings/{listing_id}", response_model=Listing)
def listing(listing_id: str, request: Request) -> Listing:
    found = request.app.state.catalogue.by_id.get(listing_id)
    if found is None:
        raise HTTPException(404, "unknown listing")
    return found


@router.get("/api/sessions/{session_id}", response_model=SessionView)
def session(session_id: str, request: Request) -> SessionView:
    return request.app.state.sessions.get(session_id)


@router.get("/api/sessions/{session_id}/stream")
async def stream(session_id: str, request: Request) -> StreamingResponse:
    """Server-sent events: `{"version": n}` whenever the session changes, and a `ping` event every
    few seconds so the page can tell a quiet session from a dead connection."""
    sessions = request.app.state.sessions

    async def events():
        sent = 0
        while not await request.is_disconnected():
            version = sessions.get(session_id).version
            if version != sent:
                sent = version
                yield f"data: {json.dumps({'version': version})}\n\n"
            try:
                async with sessions.changed:
                    await asyncio.wait_for(sessions.changed.wait(), timeout=HEARTBEAT_S)
            except TimeoutError:
                yield "event: ping\ndata: {}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


class FocusBody(BaseModel):
    listing_id: str


@router.post("/api/sessions/{session_id}/focus", response_model=SessionView)
async def focus(session_id: str, body: FocusBody, request: Request) -> SessionView:
    if body.listing_id not in request.app.state.catalogue.by_id:
        raise HTTPException(404, "unknown listing")
    return await request.app.state.sessions.update(session_id, focus=body.listing_id)


class FeedbackBody(BaseModel):
    listing_id: str
    reaction: Literal["like", "dislike", "reject"]
    reason: str | None = Field(default=None, max_length=120)


@router.post("/api/sessions/{session_id}/feedback", response_model=SessionView)
async def feedback(session_id: str, body: FeedbackBody, request: Request) -> SessionView:
    """CONTRACTS.md: {listing_id, reaction, reason?} -> FeedbackGiven. The demo store shows it on the card."""
    if body.listing_id not in request.app.state.catalogue.by_id:
        raise HTTPException(404, "unknown listing")
    return await request.app.state.sessions.react(session_id, body.listing_id, body.reaction, body.reason)


@router.post("/api/sessions/{session_id}/calls", response_model=SessionView)
async def plan_calls(session_id: str, request: Request) -> SessionView:
    """Demo hook: stands in for the Concierge's call_listings until the voice layer exists. The top four
    places with a pin become the plan; nothing dials until the renter hangs up."""
    return await request.app.state.sessions.plan_top(session_id)


@router.post("/api/sessions/{session_id}/hang-up", response_model=SessionView)
async def hang_up(session_id: str, request: Request) -> SessionView:
    """Demo hook: stands in for the renter hanging up (SessionEnded) until the voice layer exists. The
    planned calls queue, ring and finish, scripted, and the session ends with its results."""
    return await request.app.state.sessions.hang_up(session_id)


@router.post("/api/sessions/{session_id}/reset", response_model=SessionView)
async def reset(session_id: str, request: Request) -> SessionView:
    """Demo hook: back to an open brief with nothing planned, to run the demo again."""
    return await request.app.state.sessions.reset(session_id)


class ReturningBody(BaseModel):
    returning: bool = True
    name: str | None = Field(default=None, max_length=40)


@router.post("/api/sessions/{session_id}/returning", response_model=SessionView)
async def returning(session_id: str, body: ReturningBody, request: Request) -> SessionView:
    """Demo hook: marks the session as a returning renter until profiles (O-05) recognise the phone
    number. The panel then opens with "Welcome back, <name>."."""
    name = (body.name or "").strip() or None
    return await request.app.state.sessions.welcome_back(session_id, body.returning, name)


@router.post("/api/sessions/{session_id}/preferences", response_model=SessionView)
async def preferences(session_id: str, body: Preferences, request: Request) -> SessionView:
    """Demo hook: stands in for the Concierge's update_preferences until the voice layer exists."""
    return await request.app.state.sessions.update(session_id, prefs=body)


class JudgeStart(BaseModel):
    session_id: str
    ws_url: str


@router.post("/api/judge/start", response_model=JudgeStart)
async def judge_start(request: Request) -> JudgeStart | JSONResponse:
    """CONTRACTS.md: {session_id, ws_url} for browser audio. Turned down, with a `reason` /try can tell
    apart: "closed" (503) until JUDGE_AUDIO_READY, "rate_limited" (429) after JUDGE_STARTS_PER_IP starts
    from one address in the window, "busy" (503) while HIGGS_MAX_SESSIONS judge sessions hold a line.
    Only a start that opens a line counts against the address. Async on purpose: taking a line never
    races another request."""
    state = request.app.state
    if not state.settings.judge_audio_ready:
        return _refused(503, "closed", CLOSED)
    ip = client_ip(request.headers, request.client.host if request.client else None)
    if wait := state.judge_limit.wait_s(ip):
        minutes = math.ceil(wait / 60)
        return _refused(429, "rate_limited", f"Too many judge sessions from your network. Try again in "
                        f"{minutes} minute{'' if minutes == 1 else 's'}.", math.ceil(wait))
    session_id = state.judges.take()
    if session_id is None:
        return _refused(503, "busy", BUSY, 60)
    state.judge_limit.record(ip)
    state.sessions.get(session_id)   # SessionStarted, until O-02's runtime owns it
    ws_url = state.settings.judge_ws_path.format(session_id=session_id)
    return JudgeStart(session_id=session_id, ws_url=ws_url)


@router.post("/api/judge/{session_id}/done", response_model=SessionView)
async def judge_done(session_id: str, request: Request) -> SessionView | JSONResponse:
    """The judge's Done button ends the brief, the way hanging up ends a phone call: the planned calls go
    out, or with nothing planned the brief closes. A stand-in until the browser transport (A-03) and the
    session runtime (O-02) end the brief themselves, so it's behind the same JUDGE_AUDIO_READY gate as
    /api/judge/start. The judge's line is free again for the next judge."""
    state = request.app.state
    if not state.settings.judge_audio_ready:
        return _refused(503, "closed", CLOSED)
    state.judges.hang_up(session_id)
    return await state.sessions.hang_up(session_id)


@router.post("/api/judge/{session_id}/hang-up", status_code=204)
async def judge_hang_up(session_id: str, request: Request) -> Response:
    """/try leaving (a beacon on pagehide) or starting again: the judge's line frees at once instead of at
    its timeout. Only the line: unlike Done, nothing gets called. Ungated and idempotent, because freeing
    a line is always safe and a beacon can arrive after Done already freed it."""
    request.app.state.judges.hang_up(session_id)
    return Response(status_code=204)


def _refused(status: int, reason: str, message: str, retry_after_s: int | None = None) -> JSONResponse:
    # The wait goes in the body too: a browser on another origin can't read Retry-After without CORS.
    body: dict = {"detail": message, "reason": reason}
    headers = None
    if retry_after_s is not None:
        body["retry_after_s"] = retry_after_s
        headers = {"Retry-After": str(retry_after_s)}
    return JSONResponse(body, status_code=status, headers=headers)
