"""A renter's own history, opened by the phone number they call from.

Read only. The number is the login: the page shows that profile's saved search,
places, sessions and calls. Transcripts and recordings stay on the admin routes.
Off without a database (404). A number that isn't stored is 404. A string that
isn't a phone number is 422.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from landline.api.admin import UserDetail, load_user
from landline.contracts import as_e164

router = APIRouter()


class PhoneLogin(BaseModel):
    phone: str = Field(min_length=1, max_length=40)


@router.post("/api/me", response_model=UserDetail)
async def me(body: PhoneLogin, request: Request) -> UserDetail:
    e164 = as_e164(body.phone)
    if not e164:
        raise HTTPException(422, "not a phone number")
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
