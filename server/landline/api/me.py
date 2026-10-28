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

GET and PATCH /api/me/profile (U-12a), signed in the same way: the renter's saved search, email, what the
Caller may tell listing agents, and whether the next call starts from the saved search. A PATCH changes only
the fields it sends (null clears one), and every field is checked in code first (ProfileEdit): a bad one is
a 422 naming it, and nothing is saved.

Read only. The page shows that profile's saved search, places, sessions and calls; transcripts and
recordings stay on the admin routes. Off without a database (404). A number that isn't stored is 404. A
string that isn't a phone number is 422.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Callable
from datetime import date, datetime
from typing import Annotated, Any, Literal

from clerk_backend_api.security import authenticate_request_async
from clerk_backend_api.security.types import AuthenticateRequestOptions
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from landline.api.admin import Place, UserDetail, _places, load_user
from landline.config import Settings
from landline.contracts import Preferences, as_e164
from landline.db.rows import ProfileRow, UserRow
from landline.orchestrator.profiles import preferences_of

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


# ── The renter's own profile (U-12a) ─────────────────────────────────────────

SHARED_BY_DEFAULT = ("move_in", "occupants", "pets")   # user_profiles.shareable's default
ShareKey = Literal["name", "move_in", "occupants", "pets"]
EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
ISO_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")


def _words(longest: int) -> Any:
    return Annotated[str, Field(strict=True), StringConstraints(strip_whitespace=True, min_length=1,
                                                                max_length=longest)]


class ProfileEdit(BaseModel):
    """PATCH /api/me/profile: any of these. A field left out stays as it is; null clears one that can be
    empty, and [] empties a list. Each is checked here, before anything is saved."""
    model_config = ConfigDict(extra="forbid")

    max_monthly: Annotated[int, Field(strict=True, ge=0, le=20_000)] | None = None   # whole dollars
    min_beds: Annotated[float, Field(strict=True, ge=0, le=5, multiple_of=0.5)] | None = None   # 0: a studio
    areas: Annotated[list[_words(80)], Field(max_length=10)] = None
    pets: Annotated[list[_words(40)], Field(max_length=10)] = None
    must_have: Annotated[list[_words(60)], Field(max_length=20)] = None
    move_in: date | None = None
    email: Annotated[str, Field(strict=True, max_length=254)] | None = None
    shareable: list[ShareKey] = None
    use_saved_search: Annotated[bool, Field(strict=True)] = None

    @field_validator("move_in", mode="before")
    @classmethod
    def _a_day(cls, v: Any) -> Any:
        if v is not None and not (isinstance(v, str) and ISO_DAY.fullmatch(v)):
            raise ValueError("a date written as YYYY-MM-DD")
        return v   # then checked as a real calendar day

    @field_validator("email")
    @classmethod
    def _an_email(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = v.strip()
        if not EMAIL.fullmatch(v):
            raise ValueError("not an email address")
        return v

    @field_validator("shareable")
    @classmethod
    def _each_once(cls, v: list[str] | None) -> list[str] | None:
        if v is not None and len(set(v)) != len(v):
            raise ValueError("each field once")
        return v


class MyProfile(BaseModel):
    """The renter's profile as /me shows and edits it."""
    phone: str
    email: str | None = None
    use_saved_search: bool = False
    shareable: list[str] = Field(default_factory=lambda: list(SHARED_BY_DEFAULT))
    preferences: Preferences = Field(default_factory=Preferences)
    updated_at: datetime | None = None


@router.get("/api/me/profile", response_model=MyProfile)
async def my_profile(request: Request) -> MyProfile:
    db, user = await _signed_in_user(request)
    return _profile_of(user, await db.profiles.get(user.user_id))


@router.patch("/api/me/profile", response_model=MyProfile)
async def edit_profile(edit: ProfileEdit, request: Request) -> MyProfile:
    db, user = await _signed_in_user(request)
    changed = edit.model_fields_set
    if not changed:
        return _profile_of(user, await db.profiles.get(user.user_id))
    async with db.transaction() as conn:
        profile = await db.profiles.get(user.user_id, conn)
        hard = dict(profile.hard) if profile else {}   # where profiles.py keeps the search, areas included
        for key in ("max_monthly", "min_beds", "areas", "pets", "must_have"):
            if key in changed:
                _put(hard, key, getattr(edit, key))
        move_in = profile.move_in if profile else None
        if "move_in" in changed:
            move_in = edit.move_in
            _put(hard, "move_in", move_in.isoformat() if move_in else None)
        profile = await db.profiles.upsert(
            user.user_id, hard=hard, priorities=profile.priorities if profile else [],
            anchors=profile.anchors if profile else [], move_in=move_in,
            occupants=profile.occupants if profile else None,
            shareable=edit.shareable if "shareable" in changed else
            (profile.shareable if profile else SHARED_BY_DEFAULT),
            memory_summary=profile.memory_summary if profile else None, conn=conn)
        if {"email", "use_saved_search"} & changed:
            saved = edit.use_saved_search if "use_saved_search" in changed else user.use_saved_search
            await db.users.set_settings(user.user_id, email=edit.email if "email" in changed else user.email,
                                        use_saved_search=saved, conn=conn)
            user = await db.users.get(user.user_id, conn) or user
    log.info("me: profile of user %s changed: %s", user.user_id, ", ".join(sorted(changed)))
    return _profile_of(user, profile)


def _put(hard: dict[str, Any], key: str, value: Any) -> None:
    """Set one saved-search field, or drop it when cleared (stored_fields keeps no empty ones either)."""
    if value in (None, []):
        hard.pop(key, None)
    else:
        hard[key] = value


def _profile_of(user: UserRow, profile: ProfileRow | None) -> MyProfile:
    return MyProfile(phone=user.phone_e164, email=user.email, use_saved_search=user.use_saved_search,
                     shareable=list(profile.shareable) if profile else list(SHARED_BY_DEFAULT),
                     preferences=preferences_of(profile) if profile else Preferences(),
                     updated_at=profile.updated_at if profile else None)


async def _signed_in_user(request: Request) -> tuple[Any, UserRow]:
    """The database and the signed-in renter's row; 404 when sign-in or the database is off, or the number
    isn't stored; 401 without a valid token."""
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
    return db, user
