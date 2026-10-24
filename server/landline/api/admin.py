"""The admin panel's API: every renter, their sessions, calls, transcripts and the listings they chose.

Off unless ADMIN_TOKEN is set and there is a database: then every route answers 404, as if it weren't
there. Each request carries the token as `Authorization: Bearer <token>`, never in the URL, and a wrong
one gets 401. Read only.
"""
from __future__ import annotations

import secrets
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from landline.api.routes import Turn
from landline.contracts import Preferences
from landline.db.pool import Database
from landline.db.rows import ProfileRow
from landline.orchestrator.profiles import preferences_of

router = APIRouter(prefix="/api/admin")


def admin_db(request: Request) -> Database:
    state = request.app.state
    token, db = state.settings.admin_token, getattr(state, "db", None)
    if not token or db is None:
        raise HTTPException(404, "Not Found")
    given = request.headers.get("authorization", "")
    scheme, _, value = given.partition(" ")
    if scheme.lower() != "bearer" or not secrets.compare_digest(value.strip().encode(), token.encode()):
        raise HTTPException(401, "admin token required", headers={"WWW-Authenticate": "Bearer"})
    return db


AdminDb = Annotated[Database, Depends(admin_db)]


class UserRow(BaseModel):
    user_id: int
    phone: str
    name: str | None = None
    language: str | None = None
    created_at: datetime
    last_seen_at: datetime | None = None
    sessions: int
    calls: int
    booked: int
    last_phase: str | None = None
    summary: str | None = None


class Place(BaseModel):
    listing_id: str
    address: str
    unit_label: str | None = None
    monthly_cost: int | None = None
    beds: float | None = None
    url: str | None = None


class ChosenPlace(Place):
    """A listing the renter did something with, newest first: liked, rejected, planned, called, booked."""
    actions: list[str]
    reason: str | None = None
    last_at: datetime | None = None


class Interaction(BaseModel):
    listing_id: str
    address: str
    action: str
    reason: str | None = None
    attribute: str | None = None
    at: datetime
    session_id: str | None = None


class CallRow(BaseModel):
    call_id: str
    role: str
    transport: str
    listing_id: str | None = None
    address: str | None = None
    state: str
    end_reason: str | None = None
    created_at: datetime
    answered_at: datetime | None = None
    ended_at: datetime | None = None
    turns: int = 0


class SessionRow(BaseModel):
    session_id: str
    channel: str
    phase: str
    started_at: datetime
    ended_at: datetime | None = None
    preferences: Preferences | None = None
    plan: list[Place] = Field(default_factory=list)
    results: str | None = None
    agent_says: str | None = None
    calls: list[CallRow] = Field(default_factory=list)


class UserDetail(UserRow):
    consent_memory: bool = False
    preferences: Preferences | None = None
    profile_updated_at: datetime | None = None
    places: list[ChosenPlace] = Field(default_factory=list)
    interactions: list[Interaction] = Field(default_factory=list)
    history: list[SessionRow] = Field(default_factory=list)


class Fact(BaseModel):
    field: str
    value: Any
    quote: str
    confirmed: bool
    created_at: datetime


class CallDetail(CallRow):
    session_id: str | None = None
    user_id: int | None = None
    transcript: list[Turn] = Field(default_factory=list)
    facts: list[Fact] = Field(default_factory=list)


@router.get("/users", response_model=list[UserRow])
async def users(db: AdminDb) -> list[UserRow]:
    return [_user_row(r) for r in await db.admin.users()]


@router.get("/users/{user_id}", response_model=UserDetail)
async def user(user_id: int, request: Request, db: AdminDb) -> UserDetail:
    row = await db.admin.user(user_id)
    counts = await db.admin.users(user_id)
    if row is None or not counts:
        raise HTTPException(404, "unknown user")
    sessions = await db.admin.sessions(user_id)
    calls = await db.admin.calls([s["session_id"] for s in sessions]) if sessions else []
    interactions = await db.admin.interactions(user_id)
    place = _places(request)
    by_session: dict[str, list[CallRow]] = {}
    for c in calls:
        by_session.setdefault(c["session_id"], []).append(_call_row(c, place))
    history = [_session_row(s, by_session.get(s["session_id"], []), place) for s in sessions]
    return UserDetail(
        **_user_row(counts[0]).model_dump(),
        consent_memory=row["consent_memory"],
        preferences=_profile_preferences(row),
        profile_updated_at=row.get("profile_updated_at"),
        places=_chosen(interactions, history, place),
        interactions=[Interaction(**{k: i[k] for k in ("listing_id", "action", "reason", "attribute", "at",
                                                      "session_id")}, address=place(i["listing_id"]).address)
                      for i in interactions],
        history=history,
    )


@router.get("/calls/{call_id}", response_model=CallDetail)
async def call(call_id: str, request: Request, db: AdminDb) -> CallDetail:
    row = await db.admin.call(call_id)
    if row is None:
        raise HTTPException(404, "unknown call")
    turns = await db.turns.list(call_id)
    facts = await db.facts.for_call(call_id)
    return CallDetail(
        **_call_row({**row, "turns": len(turns)}, _places(request)).model_dump(),
        session_id=row["session_id"], user_id=row["user_id"],
        transcript=[Turn.model_validate(t, from_attributes=True) for t in turns],
        facts=[Fact(field=f.field, value=f.value, quote=f.quote, confirmed=f.confirmed,
                    created_at=f.created_at) for f in facts],
    )


# Shaping


def _places(request: Request):
    """listing_id -> what the panel shows of it, from the catalogue held in memory."""
    by_id = request.app.state.catalogue.by_id

    def place(lid: str) -> Place:
        x = by_id.get(lid)
        if x is None:
            return Place(listing_id=lid, address=lid)
        return Place(listing_id=lid, address=x.address, unit_label=x.unit_label, monthly_cost=x.monthly_cost,
                     beds=x.beds, url=x.url)
    return place


def _user_row(r: dict[str, Any]) -> UserRow:
    return UserRow(user_id=r["user_id"], phone=r["phone_e164"], name=r["name"], language=r["language"],
                   created_at=r["created_at"], last_seen_at=r["last_seen_at"], sessions=r["sessions"],
                   calls=r["calls"], booked=r["booked"], last_phase=r["last_phase"],
                   summary=r["memory_summary"])


def _call_row(c: dict[str, Any], place) -> CallRow:
    lid = c.get("listing_id")
    return CallRow(call_id=c["call_id"], role=c["role"], transport=c["transport"], listing_id=lid,
                   address=place(lid).address if lid else None, state=c["state"], end_reason=c["end_reason"],
                   created_at=c["created_at"], answered_at=c["answered_at"], ended_at=c["ended_at"],
                   turns=c.get("turns") or 0)


def _session_row(s: dict[str, Any], calls: list[CallRow], place) -> SessionRow:
    state = (s.get("snapshot") or {}).get("state") or {}
    view = (s.get("snapshot") or {}).get("view") or {}
    frozen, asked = state.get("plan") or {}, state.get("request") or {}
    planned = frozen.get("listing_ids") or asked.get("listing_ids") or []
    prefs = state.get("preferences")
    return SessionRow(session_id=s["session_id"], channel=s["channel"], phase=s["status"],
                      started_at=s["started_at"], ended_at=s["ended_at"],
                      preferences=Preferences.model_validate(prefs) if prefs else None,
                      plan=[place(lid) for lid in planned], results=state.get("results"),
                      agent_says=view.get("agent_says"), calls=calls)


def _profile_preferences(row: dict[str, Any]) -> Preferences | None:
    if row.get("hard") is None:
        return None
    profile = ProfileRow(user_id=row["user_id"], hard=row["hard"], priorities=row["priorities"] or [],
                         anchors=row["anchors"] or [], move_in=None, occupants=row["occupants"], shareable=[],
                         memory_summary=row["memory_summary"], updated_at=row["profile_updated_at"])
    return preferences_of(profile)


def _chosen(interactions: list[dict[str, Any]], history: list[SessionRow], place) -> list[ChosenPlace]:
    """One entry per listing the renter acted on, newest first, with everything they did with it."""
    seen: dict[str, ChosenPlace] = {}
    for i in interactions:   # newest first
        p = seen.get(i["listing_id"])
        if p is None:
            p = seen[i["listing_id"]] = ChosenPlace(**place(i["listing_id"]).model_dump(), actions=[],
                                                    reason=i["reason"], last_at=i["at"])
        if i["action"] not in p.actions:
            p.actions.append(i["action"])
    for s in history:        # planned in a session whose calls haven't been stored as interactions yet
        for pl in s.plan:
            p = seen.get(pl.listing_id)
            if p is None:
                p = seen[pl.listing_id] = ChosenPlace(**pl.model_dump(), actions=[], last_at=s.started_at)
            if "called" not in p.actions and "booked" not in p.actions and "planned" not in p.actions:
                p.actions.append("planned")
    return list(seen.values())
