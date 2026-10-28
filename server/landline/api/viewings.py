"""The confirm page's API (O-08d): what a listing agent sees and answers at <WEB_URL>/v/<token>.

The token in the confirm text (O-08b) is the only key, so the page gets the viewing's time and address,
and the renter's first name only when the renter let Landline tell listing agents their name on that call
(the call plan's `shareable`), and nothing else about them. An unknown token is 404.

POST …/confirm takes the agent's email, checked here before orchestrator/viewings.py's `Decisions.confirm`
(O-08c) records the answer and sends the invites; POST …/decline needs nothing. A token decides once: a second
answer is 409, with how it was decided, which GET also says.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from landline.orchestrator.viewings import Answer, valid_email, when

router = APIRouter()
UNKNOWN = "unknown link"


class ViewingInvite(BaseModel):
    status: Literal["pending", "confirmed", "declined"]
    starts_at: datetime
    when: str                          # "Saturday, Oct 31 at 2 pm", in Toronto time
    address: str
    renter_first_name: str | None = None   # only when the renter shared their name with listing agents


class AgentEmail(BaseModel):
    email: str = Field(max_length=320)


class Answered(BaseModel):
    status: Literal["confirmed", "declined"]
    invite_sent: bool = False          # a Calendar invite went to the agent (a confirm, with Google keys)


@router.get("/api/viewings/{token}", response_model=ViewingInvite)
async def viewing(token: str, request: Request) -> ViewingInvite:
    db = getattr(request.app.state, "db", None)
    row = await db.confirmations.get(token) if db is not None else None
    if row is None:
        raise HTTPException(404, UNKNOWN)
    listing = request.app.state.catalogue.by_id.get(row.listing_id)
    return ViewingInvite(status=row.status, starts_at=row.starts_at, when=when(row.starts_at),
                         address=listing.address if listing else "the listing",
                         renter_first_name=await _first_name(request, row.session_id))


@router.post("/api/viewings/{token}/confirm", response_model=Answered)
async def confirm(token: str, body: AgentEmail, request: Request) -> Answered:
    email = valid_email(body.email)
    if email is None:
        raise HTTPException(422, "not an email address")
    return _answered(await request.app.state.viewing_decisions.confirm(token, email))


@router.post("/api/viewings/{token}/decline", response_model=Answered)
async def decline(token: str, request: Request) -> Answered:
    return _answered(await request.app.state.viewing_decisions.decline(token))


def _answered(answer: Answer) -> Answered:
    if answer.outcome in ("confirmed", "declined"):
        return Answered(status=answer.outcome, invite_sent=answer.event_id is not None)
    if answer.outcome == "already_decided":
        raise HTTPException(409, {"message": "already decided", "status": answer.status})
    if answer.outcome == "bad_email":
        raise HTTPException(422, "not an email address")
    raise HTTPException(404, UNKNOWN)


async def _first_name(request: Request, session_id: str) -> str | None:
    """The renter's first name, from the call plan's `shareable`: set only when they shared their name."""
    runtime = request.app.state.runtime
    if runtime.has(session_id):
        plan = runtime.state(session_id).plan
        name = plan.shareable.name if plan else None
    else:
        row = await request.app.state.db.sessions.get(session_id)
        state = ((row.snapshot or {}).get("state") or {}) if row else {}
        name = ((state.get("plan") or {}).get("shareable") or {}).get("name")
    first = (name or "").strip().split()
    return first[0] if first else None
