"""A renter's own history, behind a phone sign-in (U-11a).

GET /api/me with `Authorization: Bearer <Clerk session token>` returns the signed-in renter's history. The
phone number comes only from the verified token: its `phone` claim, which the Clerk dashboard adds to the
session token ({"phone": "{{user.primary_phone_number}}"}). Clerk's SMS code proved the number belongs to
whoever signed in. The token must be signed by our Clerk instance and current. A browser's token carries
an `azp` (the page's origin), which must be WEB_URL, a CORS origin, or a Vercel preview that
CORS_ORIGIN_REGEX matches. A native app's token (the Expo app, U-13) has none, since Clerk sets `azp` from
the Origin header and native requests send no Origin; our instance's signature is the proof there. Anything
else is 401. Without CLERK_SECRET_KEY, sign-in is off (404).

POST /api/me {phone} is the hackathon's way in, where the number is the login. With CLERK_SECRET_KEY set it
answers 401, since a typed number proves nothing; without it (local dev, CI) it works as before.

GET /api/me/listings (U-12c), signed in the same way: every listing Landline called for the renter, newest
first, with how the call ended, the facts heard in the agent's own words, red flags and the viewing.

Read only. The page shows that profile's saved search, places, sessions and calls; transcripts and
recordings stay on the admin routes. Off without a database (404). A number that isn't stored is 404. A
string that isn't a phone number is 422.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Callable
from datetime import datetime
from typing import Any, Literal

from clerk_backend_api.security import authenticate_request_async
from clerk_backend_api.security.types import AuthenticateRequestOptions
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from landline.api.admin import Place, UserDetail, _places, load_user
from landline.config import Settings
from landline.contracts import as_e164

log = logging.getLogger(__name__)
router = APIRouter()
SIGN_IN = "sign in with your phone"


class PhoneLogin(BaseModel):
    phone: str = Field(min_length=1, max_length=40)


class HeardFact(BaseModel):
    """One fact a listing call heard, with its source: the agent's own words, and when."""
    field: str
    value: str
    quote: str
    at: datetime | None = None


class MyViewing(BaseModel):
    starts_at: datetime
    slot_text: str               # as the landlord said it
    status: str = "booked"       # booked, confirmed or declined: the card's, once X-04 stores it


class MyListing(BaseModel):
    listing_id: str
    address: str
    unit_label: str | None = None
    session_id: str
    call_id: str
    called_at: datetime
    ended: Literal["talked", "voicemail", "no_answer"]
    facts: list[HeardFact] = Field(default_factory=list)
    red_flags: list[str] = Field(default_factory=list)
    viewing: MyViewing | None = None


@router.get("/api/me", response_model=UserDetail)
async def signed_in(request: Request) -> UserDetail:
    settings: Settings = request.app.state.settings
    if not settings.clerk_secret_key:
        raise HTTPException(404, "sign-in is off")
    return await _history(await _signed_in_phone(request, settings), request)


@router.post("/api/me", response_model=UserDetail)
async def me(body: PhoneLogin, request: Request) -> UserDetail:
    if request.app.state.settings.clerk_secret_key:
        raise HTTPException(401, SIGN_IN)   # a typed number proves nothing once sign-in is on
    e164 = as_e164(body.phone)
    if not e164:
        raise HTTPException(422, "not a phone number")
    return await _history(e164, request)


@router.get("/api/me/listings", response_model=list[MyListing])
async def my_listings(request: Request) -> list[MyListing]:
    settings: Settings = request.app.state.settings
    if not settings.clerk_secret_key:
        raise HTTPException(404, "sign-in is off")
    phone = await _signed_in_phone(request, settings)
    db = getattr(request.app.state, "db", None)
    if db is None:
        raise HTTPException(404, "history is off")
    user = await db.users.by_phone(phone)
    if user is None:
        raise HTTPException(404, "unknown number")
    sessions = await db.admin.sessions(user.user_id)
    calls = await db.admin.calls([s["session_id"] for s in sessions]) if sessions else []
    return listings(sessions, calls, _places(request))


def listings(sessions: list[dict[str, Any]], calls: list[dict[str, Any]],
             place: Callable[[str], Place]) -> list[MyListing]:
    """Each listing call that was dialled, with what its session's stored snapshot kept: the Auditor's outcome
    (the facts with the agent's own words, red flags) and the viewing. Newest first."""
    snapshots = {s["session_id"]: s.get("snapshot") or {} for s in sessions}
    out: list[MyListing] = []
    for c in calls:
        lid = c.get("listing_id")
        if c["role"] != "listing" or not lid:
            continue
        snapshot = snapshots.get(c["session_id"], {})
        state = snapshot.get("state") or {}
        outcome = (state.get("outcomes") or {}).get(lid) or {}
        if outcome.get("call_id") not in (None, c["call_id"]):
            outcome = {}   # what a later call to the same listing settled
        # Never dialled: blocked, or still queued with nothing settled (an outcome says the call happened,
        # even when its row wasn't moved on).
        if "blocked" in (c["state"], c.get("end_reason"), outcome.get("end_reason")) or (
                c["state"] == "queued" and not outcome):
            continue
        viewing = outcome.get("viewing") or (state.get("viewings") or {}).get(lid)
        board = (snapshot.get("view") or {}).get("board") or []
        card = next((x for x in board if x.get("listing_id") == lid), {})
        at = place(lid)
        out.append(MyListing(
            listing_id=lid, address=at.address, unit_label=at.unit_label, session_id=c["session_id"],
            call_id=c["call_id"], called_at=c["answered_at"] or c["created_at"], ended=_ended(c, outcome),
            facts=[HeardFact(field=f["field"], value=f["value"], quote=f["quote"], at=f.get("at"))
                   for f in outcome.get("facts") or []],
            red_flags=list(outcome.get("red_flags") or []),
            viewing=MyViewing(starts_at=viewing["starts_at"], slot_text=viewing["slot_text"],
                              status=(card.get("viewing") or {}).get("status") or "booked")
            if viewing else None,
        ))
    return sorted(out, key=lambda x: x.called_at, reverse=True)


def _ended(call: dict[str, Any], outcome: dict[str, Any]) -> Literal["talked", "voicemail", "no_answer"]:
    reason = call.get("end_reason") or outcome.get("end_reason")
    if reason == "voicemail" or call.get("state") == "voicemail":
        return "voicemail"
    if call.get("answered_at") or reason in ("done", "refused", "wrong_number", "hangup"):
        return "talked"
    return "no_answer"


async def _signed_in_phone(request: Request, settings: Settings) -> str:
    """The E.164 phone number of the verified Clerk session, or 401."""
    options = AuthenticateRequestOptions(secret_key=settings.clerk_secret_key, jwt_key=settings.clerk_jwt_key,
                                         accepts_token=["session_token"])
    state = await authenticate_request_async(request, options)
    if not state.is_signed_in or not state.payload:
        log.info("me: sign-in refused (%s)", state.message)
        raise HTTPException(401, SIGN_IN)
    if not _ours(state.payload.get("azp"), settings):
        log.info("me: a token made for %r, not our web app", state.payload.get("azp"))
        raise HTTPException(401, SIGN_IN)
    phone = as_e164(str(state.payload.get("phone") or ""))
    if not phone:   # the session token wasn't customized to carry the phone number
        log.warning("me: a signed-in session with no phone claim")
        raise HTTPException(401, SIGN_IN)
    return phone


def _ours(azp: object, settings: Settings) -> bool:
    """Whether the token was made for us: no `azp` (a native app's), or WEB_URL, a CORS origin, or a
    matching Vercel preview. A native app that turns out to send a fixed `azp` goes in CORS_ORIGINS."""
    if azp is None or azp == "":
        return True
    if not isinstance(azp, str):
        return False
    azp = azp.rstrip("/")
    allowed = {o.rstrip("/") for o in (settings.web_url, *settings.cors_origins)}
    regex = settings.cors_origin_regex
    return azp in allowed or bool(regex and re.fullmatch(regex, azp))


async def _history(e164: str, request: Request) -> UserDetail:
    db = getattr(request.app.state, "db", None)
    if db is None:
        raise HTTPException(404, "history is off")
    user = await db.users.by_phone(e164)
    if user is None:
        raise HTTPException(404, "unknown number")
    detail = await load_user(user.user_id, request, db)
    if detail is None:
        raise HTTPException(404, "unknown number")
    return detail
