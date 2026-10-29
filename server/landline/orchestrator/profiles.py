"""Profiles (O-05): what a renter's calls are recorded as, by phone number. Plain code, no model.

    await profiles.remember(state, "SessionEnded", address)   # when the brief ends, and on "CallsCompleted"

`remember` writes the profile, the memory summary and the listing interactions, for /me and the app. The
summary is a template over the stored fields, so it can only say what the renter said. Nothing here is read
back into a call: every call starts from the beginning, with no saved search, preferences, reactions, places
or results from earlier sessions (#144).
"""
from __future__ import annotations

import logging
from collections.abc import Callable, Sequence

from landline.contracts import Anchor, Preferences, as_e164
from landline.db.pool import Database
from landline.db.rows import ProfileRow
from landline.orchestrator.state import Said, SessionState

log = logging.getLogger(__name__)

# listing_interactions.action for each reaction. A dislike keeps its reason without excluding.
ACTION = {"like": "liked", "reject": "rejected", "dislike": "viewed"}


class Profiles:
    def __init__(self, db: Database):
        self.db = db

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
                # The call adds to the profile; what it didn't mention stays, and so do the renter's own
                # edits on /me (U-12a): what Landline may share is never a call's to change, nor the move-in
                # date.
                stored = await db.profiles.get(uid, conn, lock=True)
                hard, priorities, anchors = stored_fields(state.preferences)
                occupants = state.preferences.occupants
                if stored is not None:
                    hard = {**stored.hard, **hard}
                    priorities, anchors = priorities or stored.priorities, anchors or stored.anchors
                    occupants = stored.occupants if occupants is None else occupants
                kept = {} if stored is None else {"move_in": stored.move_in, "shareable": stored.shareable}
                await db.profiles.upsert(uid, hard=hard, priorities=priorities, anchors=anchors,
                                         occupants=occupants,
                                         memory_summary=summary(state.preferences, state.reactions, address),
                                         conn=conn, **kept)
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


def _beds(n: float | None) -> str | None:
    if n is None:
        return None
    return "a studio or bigger" if n == 0 else f"{n:g} bedroom{'s' if n != 1 else ''} or more"


def _join(items: Sequence[str]) -> str:
    items = [i for i in items if i]
    return " and ".join(items) if len(items) <= 2 else f"{', '.join(items[:-1])} and {items[-1]}"
