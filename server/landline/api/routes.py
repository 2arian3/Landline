"""Read side for the web app (owner: Mahyar). Shapes are in docs/CONTRACTS.md."""
from __future__ import annotations

import asyncio
import json
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from landline.contracts import CataloguePoint, Listing, Preferences, SessionView

router = APIRouter()
HEARTBEAT_S = 5   # well inside the page's 6.5 s "Reconnecting…" threshold
BUSY = "All lines are busy. Try again in a minute."


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
async def call_top(session_id: str, request: Request) -> SessionView:
    """Demo hook: stands in for the Concierge's call_listings until the voice layer exists. The top four
    cards show "calling", then a scripted outcome about 3 s later; the fourth doesn't pick up."""
    return await request.app.state.sessions.call_top(session_id)


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
async def judge_start(request: Request) -> JudgeStart:
    """CONTRACTS.md: {session_id, ws_url} for browser audio. At most HIGGS_MAX_SESSIONS judge sessions
    hold a line at once; past that it answers 503 with Retry-After, and /try says to try again in a
    minute. Async on purpose: taking a line never races another request."""
    session_id = request.app.state.judges.take()
    if session_id is None:
        raise HTTPException(503, BUSY, headers={"Retry-After": "60"})
    request.app.state.sessions.get(session_id)   # SessionStarted, until O-02's runtime owns it
    ws_url = request.app.state.settings.judge_ws_path.format(session_id=session_id)
    return JudgeStart(session_id=session_id, ws_url=ws_url)
