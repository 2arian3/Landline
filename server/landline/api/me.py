"""A renter's own history, behind a phone sign-in (U-11a).

GET /api/me with `Authorization: Bearer <Clerk session token>` returns the signed-in renter's history. The
phone number comes only from the verified token: its `phone` claim, which the Clerk dashboard adds to the
session token ({"phone": "{{user.primary_phone_number}}"}). Clerk's SMS code proved the number belongs to
whoever signed in. The token must be signed by our Clerk instance, current, and made for our web app: its
`azp` is WEB_URL, a CORS origin, or a Vercel preview that CORS_ORIGIN_REGEX matches. Anything else is 401.
Without CLERK_SECRET_KEY, sign-in is off (404).

POST /api/me {phone} is the hackathon's way in, where the number is the login. With CLERK_SECRET_KEY set it
answers 401, since a typed number proves nothing; without it (local dev, CI) it works as before.

Read only. The page shows that profile's saved search, places, sessions and calls; transcripts and
recordings stay on the admin routes. Off without a database (404). A number that isn't stored is 404. A
string that isn't a phone number is 422.
"""
from __future__ import annotations

import logging
import re

from clerk_backend_api.security import authenticate_request_async
from clerk_backend_api.security.types import AuthenticateRequestOptions
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from landline.api.admin import UserDetail, load_user
from landline.config import Settings
from landline.contracts import as_e164

log = logging.getLogger(__name__)
router = APIRouter()
SIGN_IN = "sign in with your phone"


class PhoneLogin(BaseModel):
    phone: str = Field(min_length=1, max_length=40)


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
    """Whether the token was made for our web app: WEB_URL, a CORS origin, or a matching Vercel preview."""
    if not isinstance(azp, str) or not azp:
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
