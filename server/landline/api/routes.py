"""Read side for the web app (owner: Mahyar). Shapes are in docs/CONTRACTS.md."""
from __future__ import annotations

import asyncio
import contextlib
import json
import math
import secrets
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel, Field

from landline.api.demo import PLANNED, _pinned, area_named
from landline.api.judge import client_ip
from landline.contracts import (
    AreaFocused,
    CallsRequested,
    CataloguePoint,
    Event,
    FeedbackGiven,
    Listing,
    ListingFocused,
    PreferenceDelta,
    Preferences,
    PreferencesUpdated,
    SessionEnded,
    SessionStarted,
    SessionView,
    Turn,
)
from landline.orchestrator import Refused

router = APIRouter()
HEARTBEAT_S = 5   # well inside the page's 6.5 s "Reconnecting…" threshold
BUSY = "All lines are busy. Try again in a minute."
CLOSED = "Judge mode opens soon."


@router.get("/", include_in_schema=False)
def root(request: Request) -> RedirectResponse:
    """Someone opening the bare API domain (a judge, say) lands on the web app, not a 404."""
    return RedirectResponse(request.app.state.settings.web_url, status_code=307)


@router.get("/health")
def health(request: Request) -> dict:
    """CONTRACTS.md: {status, listings, sessions, transport, higgs}. `sessions` counts the demo sessions
    held in memory (a restart wipes them); `higgs` is whatever the voice layer last reported."""
    state = request.app.state
    cat = state.catalogue
    return {"status": "ok", "listings": len(cat.listings), "catalogue": cat.origin,
            "sessions": len(state.sessions.views) + len(state.runtime), "transport": state.settings.transport,
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
async def session(session_id: str, request: Request) -> SessionView:
    if _live(request, session_id):
        return request.app.state.runtime.get(session_id)
    if (stored := await _stored(request, session_id)) is not None:
        return stored
    return request.app.state.sessions.get(session_id)


@router.get("/api/sessions/{session_id}/stream")
async def stream(session_id: str, request: Request) -> StreamingResponse:
    """Server-sent events: `{"version": n}` whenever the session changes, and a `ping` event every
    few seconds so the page can tell a quiet session from a dead connection. A stored session never
    changes: its version once, then pings."""
    stored = await _stored(request, session_id)
    sessions = _store(request, session_id)

    async def events():
        sent = 0
        while not await request.is_disconnected():
            version = stored.version if stored is not None else sessions.get(session_id).version
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
    if _live(request, session_id):
        tap = ListingFocused(**_meta(session_id), listing_id=body.listing_id, origin="ui")
        return await _emit(request, tap)
    await _not_stored(request, session_id)
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
    if _live(request, session_id):
        return await _emit(request, FeedbackGiven(**_meta(session_id), listing_id=body.listing_id,
                                                  reaction=body.reaction, reason=body.reason))
    await _not_stored(request, session_id)
    return await request.app.state.sessions.react(session_id, body.listing_id, body.reaction, body.reason)


@router.get("/api/calls/{call_id}/turns", response_model=list[Turn])
async def call_turns(call_id: str, request: Request, after: int = -1) -> list[Turn]:
    """CONTRACTS.md: live captions for a call, the lines after `after` in order; poll with the last idx
    shown. A call the demo store dialled answers from its script, any other from call_turns when there's
    a database. Unknown everywhere: 404."""
    state = request.app.state
    rows = state.sessions.turns(call_id, after)
    if rows is None and state.db is not None:
        rows = await state.db.turns.list(call_id, after)
        if not rows and await state.db.calls.get(call_id) is None:
            rows = None
    if rows is None:
        raise HTTPException(404, "unknown call")
    return [Turn.model_validate(row, from_attributes=True) for row in rows]


@router.post("/api/sessions/{session_id}/calls", response_model=SessionView)
async def plan_calls(session_id: str, request: Request) -> SessionView:
    """Demo hook: stands in for the Concierge's call_listings until the voice layer exists. The top four
    places with a pin become the plan; nothing dials until the renter hangs up."""
    if _live(request, session_id):
        top = [c.listing_id for c in request.app.state.runtime.get(session_id).board if _pinned(c)][:PLANNED]
        if not top:
            raise HTTPException(409, "nothing on the board to call")
        return await _emit(request, CallsRequested(**_meta(session_id), listing_ids=top))
    await _not_stored(request, session_id)
    return await request.app.state.sessions.plan_top(session_id)


@router.post("/api/sessions/{session_id}/hang-up", response_model=SessionView)
async def hang_up(session_id: str, request: Request) -> SessionView:
    """Demo hook: stands in for the renter hanging up (SessionEnded) until the voice layer exists. The
    planned calls queue, ring and finish, scripted, and the session ends with its results. A runtime
    session goes on to `calling` with its plan, and waits there for the Dispatcher (A-06)."""
    if _live(request, session_id):
        return await _emit(request, SessionEnded(**_meta(session_id), reason="hangup"))
    await _not_stored(request, session_id)
    return await request.app.state.sessions.hang_up(session_id)


@router.post("/api/sessions/{session_id}/reset", response_model=SessionView)
async def reset(session_id: str, request: Request) -> SessionView:
    """Demo hook: back to an open brief with nothing planned, to run the demo again."""
    await _demo_only(request, session_id)
    return await request.app.state.sessions.reset(session_id)


class ReturningBody(BaseModel):
    returning: bool = True
    name: str | None = Field(default=None, max_length=40)


@router.post("/api/sessions/{session_id}/returning", response_model=SessionView)
async def returning(session_id: str, body: ReturningBody, request: Request) -> SessionView:
    """Demo hook: marks a demo session as a returning renter, the way profiles (O-05) recognise a phone
    call's number for a runtime session. The panel then opens with "Welcome back, <name>."."""
    name = (body.name or "").strip() or None
    await _demo_only(request, session_id)
    return await request.app.state.sessions.welcome_back(session_id, body.returning, name)


class AreaBody(BaseModel):
    place: str = Field(max_length=60)


@router.post("/api/sessions/{session_id}/area", response_model=SessionView)
async def focus_area(session_id: str, body: AreaBody, request: Request) -> SessionView:
    """Demo hook: stands in for the Concierge's focus_area ("show me Christie Pits") until the voice layer
    exists. The session's focus_area becomes that place, and the map flies there. A runtime session gets
    it as AreaFocused."""
    area = area_named(body.place)
    if area is None:
        raise HTTPException(404, "unknown place")
    if _live(request, session_id):
        return await _emit(request, AreaFocused(**_meta(session_id), **area.model_dump()))
    await _not_stored(request, session_id)
    return await request.app.state.sessions.focus_area(session_id, area)


@router.post("/api/sessions/{session_id}/preferences", response_model=SessionView)
async def preferences(session_id: str, body: Preferences, request: Request) -> SessionView:
    """Demo hook: stands in for the Concierge's update_preferences until the voice layer exists. For a
    runtime session the fields sent are the delta."""
    if _live(request, session_id):
        delta = PreferenceDelta.model_validate(body.model_dump(exclude_unset=True))
        return await _emit(request, PreferencesUpdated(**_meta(session_id), delta=delta))
    await _not_stored(request, session_id)
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
    await state.runtime.start(SessionStarted(**_meta(session_id, "started"), channel="judge"))
    ws_url = state.settings.judge_ws_path.format(session_id=session_id)
    return JudgeStart(session_id=session_id, ws_url=ws_url)


@router.post("/api/judge/{session_id}/done", response_model=SessionView)
async def judge_done(session_id: str, request: Request) -> SessionView | JSONResponse:
    """The judge's Done button ends the brief (SessionEnded), the way hanging up ends a phone call: with a
    plan the session goes on to its calls, with nothing planned it closes. The audio socket closing ends it
    too (the bridge, under the same idem key, so the brief ends once); this covers a judge whose audio never
    connected, and gives the page's line back. Only a judge session: a renter's phone call isn't this
    endpoint's to end. Behind the same JUDGE_AUDIO_READY gate as /api/judge/start."""
    state = request.app.state
    if not state.settings.judge_audio_ready:
        return _refused(503, "closed", CLOSED)
    if not _live(request, session_id) or state.runtime.state(session_id).channel != "judge":
        raise HTTPException(404, "unknown judge session")
    state.judges.release(session_id)   # an open socket's line frees when the socket closes
    return await _emit(request, SessionEnded(**_meta(session_id, "ended"), reason="end_session"))


def _live(request: Request, session_id: str) -> bool:
    """A session the runtime (O-02) owns: one that voice or judge mode started. Any other id is a stored
    session (below) or a demo session, which the demo store creates on first sight. Each session has
    exactly one writer."""
    return request.app.state.runtime.has(session_id)


async def _stored(request: Request, session_id: str) -> SessionView | None:
    """The last view stored for a session the runtime doesn't hold: a restart forgets every live session,
    and Postgres keeps each one's snapshot, written on every commit. Read-only, so a renter's results link
    still shows the results. None without a database, or for an id never stored."""
    db = request.app.state.db
    if db is None or _live(request, session_id):
        return None
    row = await db.sessions.get(session_id)
    view = row.snapshot.get("view") if row is not None and row.snapshot else None
    return SessionView.model_validate(view) if view else None


async def _not_stored(request: Request, session_id: str) -> None:
    """Nothing writes to a stored session, and the demo store never takes its id."""
    if await _stored(request, session_id) is not None:
        raise HTTPException(409, "this session has ended")


def _store(request: Request, session_id: str):
    state = request.app.state
    return state.runtime if _live(request, session_id) else state.sessions


async def _demo_only(request: Request, session_id: str) -> None:
    if _live(request, session_id):
        raise HTTPException(409, "only demo sessions can do that")
    await _not_stored(request, session_id)


def _meta(session_id: str, key: str | None = None) -> dict:
    """What every event carries. A tap is a new event each time, so its idem_key is minted here."""
    return {"session_id": session_id, "at": datetime.now(UTC),
            "idem_key": f"{session_id}:{key or 'ui:' + secrets.token_urlsafe(8)}"}


async def _emit(request: Request, event: Event) -> SessionView:
    try:
        return await request.app.state.runtime.emit(event)
    except Refused as e:
        raise HTTPException(409, str(e)) from e


@router.post("/api/judge/{session_id}/hang-up", status_code=204)
async def judge_hang_up(session_id: str, request: Request) -> Response:
    """/try leaving (a beacon on pagehide) or starting again. A line whose socket never opened frees at
    once instead of at its timeout, and a brief with nothing requested closes, since nothing else would
    close it; nothing gets called from here. An open socket's line frees when the socket closes, and the
    bridge ends that brief. Ungated and idempotent: a beacon can arrive after Done or the socket."""
    state = request.app.state
    freed = state.judges.release(session_id)
    if freed and _live(request, session_id) and state.runtime.state(session_id).request is None:
        with contextlib.suppress(HTTPException):   # already ended: nothing to do
            await _emit(request, SessionEnded(**_meta(session_id, "ended"), reason="closed"))
    return Response(status_code=204)


def _refused(status: int, reason: str, message: str, retry_after_s: int | None = None) -> JSONResponse:
    # The wait goes in the body too: a browser on another origin can't read Retry-After without CORS.
    body: dict = {"detail": message, "reason": reason}
    headers = None
    if retry_after_s is not None:
        body["retry_after_s"] = retry_after_s
        headers = {"Retry-After": str(retry_after_s)}
    return JSONResponse(body, status_code=status, headers=headers)
