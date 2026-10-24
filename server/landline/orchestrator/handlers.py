"""One handler per event type. Each runs inside `session_tx` on the session's working copy.

A handler returns True when it changed the session, False when the event changes
nothing (a second hang-up, a state change for a call that already ended); then
nothing is stored and the version stays. It raises `Refused` when the event isn't
allowed now, with words a tool reply can say. Ids come from the event and the
session, never from a model: a fact's listing is the listing of the call it was
heard on.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from landline.contracts import (
    Anchor,
    AnchorResolved,
    Area,
    AreaFocused,
    CallEnded,
    CallsCompleted,
    CallsRequested,
    CallsScheduled,
    CallStateChanged,
    CallView,
    Event,
    Fact,
    FactRecorded,
    FeedbackGiven,
    ListingFocused,
    OutcomeReady,
    PreferencesUpdated,
    QuestionNoted,
    SessionEnded,
    SessionStarted,
    Viewing,
    ViewingBooked,
)
from landline.orchestrator.state import ON_THE_LINE, Request, Said, SessionState


class Refused(Exception):
    """The event isn't allowed in the session's current phase. The message is safe to speak."""


Known = Callable[[str], bool]   # is this listing in the catalogue?


def apply(state: SessionState, event: Event, known: Known) -> bool:
    return HANDLERS[event.type](state, event, known)


# Flow 1: the brief


def started(state: SessionState, e: SessionStarted, known: Known) -> bool:
    return False   # the runtime creates the session from it; a second one changes nothing


def preferences(state: SessionState, e: PreferencesUpdated, known: Known) -> bool:
    """Deltas only: a field the Concierge left out (None) stays as it was; a list it sent replaces the
    old one. An anchor the geocoder already placed keeps its coordinates."""
    delta = e.delta.model_dump(exclude_none=True)
    if not delta:
        return False
    if "anchors" in delta:
        placed = {a.label.lower(): a for a in state.preferences.anchors}
        delta["anchors"] = [placed.get(a["label"].lower()) or Anchor(label=a["label"])
                            for a in delta["anchors"]]
    before = state.preferences
    state.preferences = before.model_copy(update=delta)
    return state.preferences != before


def feedback(state: SessionState, e: FeedbackGiven, known: Known) -> bool:
    """Shows on the card. Weight changes and excluding rejected listings come with O-04."""
    _known(e.listing_id, known)
    said = Said(reaction=e.reaction, reason=e.reason, attribute=e.attribute)
    if state.reactions.get(e.listing_id) == said:
        return False
    state.reactions[e.listing_id] = said
    return True


def listing_focused(state: SessionState, e: ListingFocused, known: Known) -> bool:
    _known(e.listing_id, known)
    if state.focus_listing_id == e.listing_id:
        return False
    state.focus_listing_id = e.listing_id
    return True


def area_focused(state: SessionState, e: AreaFocused, known: Known) -> bool:
    area = Area(label=e.label, lat=e.lat, lng=e.lng, zoom=e.zoom)
    if state.focus_area == area:
        return False
    state.focus_area = area
    return True


def anchor_resolved(state: SessionState, e: AnchorResolved, known: Known) -> bool:
    changed = False
    anchors = []
    for a in state.preferences.anchors:
        if a.label.lower() == e.label.lower() and (a.lat, a.lng) != (e.lat, e.lng):
            a, changed = a.model_copy(update={"lat": e.lat, "lng": e.lng}), True
        anchors.append(a)
    if changed:
        state.preferences = state.preferences.model_copy(update={"anchors": anchors})
    return changed


def calls_requested(state: SessionState, e: CallsRequested, known: Known) -> bool:
    """Records the plan; nothing dials until the session ends. Asking again replaces it."""
    if state.phase != "open":
        raise Refused("Those calls are already under way.")
    for lid in e.listing_ids:
        _known(lid, known)
    state.request = Request(listing_ids=list(dict.fromkeys(e.listing_ids)), questions=e.extra_questions,
                            availability=e.availability, may_book=e.may_book)
    return True


def session_ended(state: SessionState, e: SessionEnded, known: Known) -> bool:
    """With a plan the session goes on to the calls; without one it closes. It ends once."""
    if state.phase != "open":
        return False
    state.phase = "calling" if state.request else "closed"
    # The results say where each called place moved since this board (the Narrator).
    state.ranks_at_hangup = {lid: n for n, lid in enumerate(state.board, start=1)}
    return True


# Hand-off


def calls_scheduled(state: SessionState, e: CallsScheduled, known: Known) -> bool:
    if state.phase != "calling":
        raise Refused("This session has no calls to make.")
    if state.plan is not None:
        return False   # the plan is frozen once
    if len(e.call_ids) != len(e.plan.listing_ids):
        raise Refused("Every listing in the plan needs exactly one call.")
    state.plan = e.plan
    state.calls = {cid: CallView(call_id=cid, listing_id=lid, state="queued")
                   for cid, lid in zip(e.call_ids, e.plan.listing_ids, strict=True)}
    return True


# Flow 2: the calls


def call_state(state: SessionState, e: CallStateChanged, known: Known) -> bool:
    call = _call(state, e.call_id)
    if call.state == "ended" or call.state == e.state:
        return False   # an ended call never changes again
    started_at = call.started_at or (e.at if e.state in ON_THE_LINE else None)
    state.calls[e.call_id] = call.model_copy(update={"state": e.state, "started_at": started_at})
    return True


def fact_recorded(state: SessionState, e: FactRecorded, known: Known) -> bool:
    lid = _call(state, e.call_id).listing_id
    state.heard.setdefault(lid, []).append(_fact(e.field, e.value, e.quote, e.at))
    return True


def question_noted(state: SessionState, e: QuestionNoted, known: Known) -> bool:
    lid = _call(state, e.call_id).listing_id
    asked = state.questions.setdefault(lid, [])
    if e.question in asked:
        return False
    asked.append(e.question)
    return True


def viewing_booked(state: SessionState, e: ViewingBooked, known: Known) -> bool:
    """Code has already checked the slot against the renter's windows (A-05). The listing is the call's."""
    lid = _call(state, e.call_id).listing_id
    if lid != e.listing_id:
        raise Refused("That booking is for a different listing than this call.")
    viewing = Viewing(starts_at=e.starts_at, slot_text=e.slot_text)
    if state.viewings.get(lid) == viewing:
        return False
    state.viewings[lid] = viewing
    return True


def call_ended(state: SessionState, e: CallEnded, known: Known) -> bool:
    call = _call(state, e.call_id)
    if call.state == "ended":
        return False
    state.calls[e.call_id] = call.model_copy(update={"state": "ended"})
    return True


def outcome_ready(state: SessionState, e: OutcomeReady, known: Known) -> bool:
    """The Auditor's outcome replaces what was heard live on that listing's card."""
    out = e.outcome
    if _call(state, out.call_id).listing_id != out.listing_id:
        raise Refused("That outcome is for a different listing than its call.")
    if state.outcomes.get(out.listing_id) == out:
        return False
    state.outcomes[out.listing_id] = out
    if out.viewing is not None:
        state.viewings[out.listing_id] = out.viewing
    return True


def calls_completed(state: SessionState, e: CallsCompleted, known: Known) -> bool:
    """Every call in the plan has an outcome or timed out. The results text comes with the Narrator (O-06)."""
    if state.phase != "calling":
        return False
    state.phase = "done"
    return True


HANDLERS: dict[str, Callable[[SessionState, Event, Known], bool]] = {
    "SessionStarted": started,
    "PreferencesUpdated": preferences,
    "FeedbackGiven": feedback,
    "ListingFocused": listing_focused,
    "AreaFocused": area_focused,
    "AnchorResolved": anchor_resolved,
    "CallsRequested": calls_requested,
    "SessionEnded": session_ended,
    "CallsScheduled": calls_scheduled,
    "CallStateChanged": call_state,
    "FactRecorded": fact_recorded,
    "QuestionNoted": question_noted,
    "ViewingBooked": viewing_booked,
    "CallEnded": call_ended,
    "OutcomeReady": outcome_ready,
    "CallsCompleted": calls_completed,
}


def _known(listing_id: str, known: Known) -> None:
    if not known(listing_id):
        raise Refused("I can't find that listing.")


def _call(state: SessionState, call_id: str) -> CallView:
    call = state.calls.get(call_id)
    if call is None:
        raise Refused("That call isn't part of this session.")
    return call


def _fact(field: str, value: str, quote: str, at: datetime) -> Fact:
    return Fact(field=field, value=value, quote=quote, speaker="Agent heard", at=at)
