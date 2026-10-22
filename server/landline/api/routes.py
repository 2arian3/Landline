"""Read side for the web app (owner: Mahyar). Shapes are in docs/CONTRACTS.md."""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from landline.contracts import CataloguePoint, Listing, Preferences, SessionView

router = APIRouter()
HEARTBEAT_S = 5   # well inside the page's 6.5 s "Reconnecting…" threshold


@router.get("/health")
def health(request: Request) -> dict:
    cat = request.app.state.catalogue
    return {"status": "ok", "listings": len(cat.listings), "catalogue": cat.origin,
            "transport": request.app.state.settings.transport}


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


@router.post("/api/sessions/{session_id}/preferences", response_model=SessionView)
async def preferences(session_id: str, body: Preferences, request: Request) -> SessionView:
    """Demo hook: stands in for the Concierge's update_preferences until the voice layer exists."""
    return await request.app.state.sessions.update(session_id, prefs=body)
