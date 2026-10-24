"""Profiles (O-05): renters remembered by phone number. Plain code, no model.

    remembered = await profiles.recall("+14165550100")   # at the start of a phone call
    await profiles.remember(state, "SessionEnded", address)   # and again on "CallsCompleted"

`recall` returns the user for that number, created on the first call, with what they asked for last time,
their reactions to listings and their last session's results. `remember` writes the profile, the memory
summary and the listing interactions. The summary is a template over the stored fields, so it can only say
what the renter said. `opening` is a ≤ 120-word brief of a returning caller. The call does not append it:
a renter's next session starts with nothing from earlier ones (#144).
"""
from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from landline.contracts import Anchor, Preferences, as_e164
from landline.db.pool import Database
from landline.db.rows import InteractionRow, ProfileRow, SessionRow
from landline.orchestrator.state import Said, SessionState

log = logging.getLogger(__name__)

TORONTO = ZoneInfo("America/Toronto")
MAX_OPENING_WORDS = 120
# From the session's start: the brief ends within its 15-minute deadline, and a plan's calls (at most 5,
# each with a 5-minute deadline) within half an hour after that. Past this, `calling` means stuck.
CALLS_STALE_AFTER = timedelta(minutes=45)
# listing_interactions.action for each reaction, and back. A dislike keeps its reason without excluding.
ACTION = {"like": "liked", "reject": "rejected", "dislike": "viewed"}
REACTION = {"liked": "like", "rejected": "reject", "viewed": "dislike"}


@dataclass(frozen=True)
class Remembered:
    user_id: int
    returning: bool                  # this number has called before
    name: str | None = None
    preferences: Preferences = field(default_factory=Preferences)
    reactions: dict[str, Said] = field(default_factory=dict)
    last_seen_at: datetime | None = None
    last_results: str | None = None  # the last session's results text, once its calls were done
    calls_running: bool = False      # the last session's calls haven't finished yet
    summary: str | None = None


class Profiles:
    def __init__(self, db: Database):
        self.db = db

    async def recall(self, phone: str) -> Remembered:
        """The renter behind this number. A new number gets a user now and a profile when its brief ends."""
        db = self.db
        e164 = as_e164(phone) or phone
        user = await db.users.by_phone(e164)
        if user is None:
            user = await db.users.upsert(e164)
            return Remembered(user_id=user.user_id, returning=False)
        profile = await db.profiles.get(user.user_id)
        last = await db.sessions.latest_for_user(user.user_id)
        last_state = (last.snapshot or {}).get("state", {}) if last else {}
        return Remembered(
            user_id=user.user_id, returning=True, name=user.name,
            preferences=preferences_of(profile) if profile else Preferences(),
            reactions=reactions_of(await db.interactions.for_user(user.user_id)),
            last_seen_at=user.last_seen_at, last_results=last_state.get("results"),
            calls_running=calls_running(last, datetime.now(UTC)),
            summary=profile.memory_summary if profile else None,
        )

    async def remember(self, state: SessionState, event_type: str, address: Callable[[str], str]) -> None:
        """Save what this session taught us about the renter. When the brief ends: the profile, its summary,
        and the reactions given on this call. When the calls are done: which places were called and booked.
        A phone session's user is found by its number here, created on the first call, and the session is
        linked to them, so nothing is read about the caller before the greeting."""
        db, uid = self.db, state.user_id
        if uid is None and state.channel == "phone" and state.caller_phone:
            e164 = as_e164(state.caller_phone) or state.caller_phone
            user = await db.users.by_phone(e164) or await db.users.upsert(e164)
            uid = user.user_id
            await db.sessions.link_user(state.session_id, uid)
        if uid is None:
            return
        async with db.transaction() as conn:
            if event_type == "SessionEnded":
                hard, priorities, anchors = stored_fields(state.preferences)
                await db.profiles.upsert(uid, hard=hard, priorities=priorities, anchors=anchors,
                                         occupants=state.preferences.occupants,
                                         memory_summary=summary(state.preferences, state.reactions, address),
                                         conn=conn)
                new = {lid: s for lid, s in state.reactions.items() if state.recalled.get(lid) != s}
                for lid, said in new.items():
                    await db.interactions.add(uid, lid, ACTION[said.reaction], session_id=state.session_id,
                                              reason=said.reason, attribute=said.attribute, conn=conn)
                await db.users.touch(uid, conn=conn)
            elif event_type == "CallsCompleted":
                for call in state.calls.values():
                    out = state.outcomes.get(call.listing_id)
                    if out is not None and out.end_reason == "blocked":
                        continue   # never dialled, so not "called"
                    action = "booked" if call.listing_id in state.viewings else "called"
                    await db.interactions.add(uid, call.listing_id, action, session_id=state.session_id,
                                              conn=conn)

    async def forget(self, user_id: int) -> bool:
        """forget_me: the user, their profile and interactions go; their sessions stay, unlinked."""
        return await self.db.users.forget(user_id)


def calls_running(last: SessionRow | None, now: datetime) -> bool:
    """The last session's calls are still going: it is `calling`, and recent enough that they could be. One
    left in `calling` (its calls never placed, or the server restarted mid-plan) counts as finished once
    CALLS_STALE_AFTER has passed, so later calls aren't told "still running" forever."""
    return bool(last and last.status == "calling" and now - last.started_at < CALLS_STALE_AFTER)


# Stored fields, both ways


def stored_fields(p: Preferences) -> tuple[dict, list, list]:
    """user_profiles.hard, .priorities and .anchors for these preferences."""
    hard = {k: v for k, v in {"max_monthly": p.max_monthly, "min_beds": p.min_beds, "areas": p.areas,
                              "pets": p.pets, "must_have": p.must_have, "move_in": p.move_in,
                              "language": p.language}.items() if v not in (None, [])}
    priorities = [{"feature": f} for f in p.priorities]
    anchors = [a.model_dump() for a in p.anchors]
    return hard, priorities, anchors


def preferences_of(profile: ProfileRow) -> Preferences:
    hard = profile.hard or {}
    return Preferences(
        max_monthly=hard.get("max_monthly"), min_beds=hard.get("min_beds"), areas=hard.get("areas", []),
        pets=hard.get("pets", []), must_have=hard.get("must_have", []), move_in=hard.get("move_in"),
        language=hard.get("language"), occupants=profile.occupants,
        priorities=[p["feature"] for p in profile.priorities if isinstance(p, dict) and p.get("feature")],
        anchors=[Anchor.model_validate(a) for a in profile.anchors],
    )


def reactions_of(rows: Sequence[InteractionRow]) -> dict[str, Said]:
    """The latest reaction to each listing (rows come most recent first)."""
    out: dict[str, Said] = {}
    for r in rows:
        if r.listing_id not in out and r.action in REACTION and (r.action != "viewed" or r.reason):
            out[r.listing_id] = Said(reaction=REACTION[r.action], reason=r.reason, attribute=r.attribute)
    return out


# Words


def summary(p: Preferences, reactions: dict[str, Said], address: Callable[[str], str]) -> str:
    """The memory summary: only what the renter said, read back from the stored fields."""
    wants = [w for w in (
        f"up to ${p.max_monthly:,} a month" if p.max_monthly is not None else None,
        _beds(p.min_beds),
        f"around {_join(p.areas)}" if p.areas else None,
        f"near {_join([a.label for a in p.anchors])}" if p.anchors else None,
        f"{p.priorities[0]} matters most" if p.priorities else None,
        f"has {_join([f'a {x}' for x in p.pets])}" if p.pets else None,
        f"must have {_join(p.must_have)}" if p.must_have else None,
        f"moving {p.move_in}" if p.move_in else None,
        f"{p.occupants} people" if p.occupants else None,
    ) if w]
    said = [f"Wants {_join(wants)}." if wants else "Hasn't said what they want yet."]
    liked = [address(lid) for lid, s in reactions.items() if s.reaction == "like"]
    passed = [f"{address(lid)} ({s.reason})" if s.reason else address(lid)
              for lid, s in reactions.items() if s.reaction != "like"]
    if liked:
        said.append(f"Liked {_join(liked)}.")
    if passed:
        said.append(f"Turned down {_join(passed)}.")
    return " ".join(said)


def opening(r: Remembered, new_matches: int, now: datetime) -> str:
    """What the Concierge is told about a returning caller, facts only, within 120 words. The results
    come first: the renter is calling back to hear them."""
    lines = [f"This renter last called {_ago(r.last_seen_at, now)}." if r.last_seen_at else
             "This renter has called before."]
    if r.calls_running:
        lines.append("The calls from their last call are still running.")
    elif r.last_results:
        lines.append(f"Results of the calls you made for them: {r.last_results}")
    if r.summary:
        lines.append(f"What you know: {r.summary}")
    if new_matches:
        lines.append(f"{new_matches} new place matches since then." if new_matches == 1
                     else f"{new_matches} new places match since then.")
    while len(" ".join(lines).split()) > MAX_OPENING_WORDS and len(lines) > 1:
        # Results go sentence by sentence from the end before anything else is dropped.
        results = next((i for i, s in enumerate(lines) if s.startswith("Results")), None)
        if results is not None and len(lines[results].split(". ")) > 1:
            lines[results] = lines[results].rsplit(". ", 1)[0] + "."
        else:
            lines.pop()
    return " ".join(lines)


def _ago(at: datetime, now: datetime) -> str:
    then, today = at.astimezone(TORONTO), now.astimezone(TORONTO)
    days = (today.date() - then.date()).days
    if days == 0:
        return "earlier today"
    if days == 1:
        return "yesterday"
    if days < 7:
        return f"on {then:%A}"
    return f"on {then:%B} {then.day}"


def _beds(n: float | None) -> str | None:
    if n is None:
        return None
    return "a studio or bigger" if n == 0 else f"{n:g} bedroom{'s' if n != 1 else ''} or more"


def _join(items: Sequence[str]) -> str:
    items = [i for i in items if i]
    return " and ".join(items) if len(items) <= 2 else f"{', '.join(items[:-1])} and {items[-1]}"
