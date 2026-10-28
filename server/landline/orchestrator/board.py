"""The SessionView a session's state produces: the Matcher's board plus what calls found.

Built inside `session_tx` on every commit (a re-rank is < 5 ms), so the board can
never lag the state it came from.
"""
from __future__ import annotations

from landline.contracts import CallView, Card, CardViewing, Listing, SessionView
from landline.db.catalogue import Catalogue
from landline.orchestrator import feedback, overrides
from landline.orchestrator.state import SessionState
from landline.scoring import FeatureMatrix, Ranked, explain

BOARD_SIZE = 6


def build(state: SessionState, version: int, catalogue: Catalogue, matrix: FeatureMatrix) -> SessionView:
    status = state.statuses()
    heard = overrides.from_calls(state, catalogue)
    # The renter's reactions move it (O-04), and so does what calls heard.
    ranking = feedback.ranked(state, catalogue, matrix, status, heard)
    by_id = catalogue.by_id
    matches = [lid for lid in ranking.order if by_id[lid].lat is not None]
    shown = matches[:BOARD_SIZE]
    # A listing being called stays on the board even if it slips below the top or out of the filters
    # (a call found it rented): its status must show.
    requested = state.request.listing_ids if state.request else []
    called = [c.listing_id for c in state.calls.values()] + requested
    shown += [lid for lid in dict.fromkeys(called) if lid not in shown and lid in by_id]
    # A card the renter disliked stays too, below the rest, so its "You: too dark" shows after it sinks.
    shown += [lid for lid in matches if lid not in shown and (said := state.reactions.get(lid))
              and said.reaction == "dislike"]
    passed = ranking.scores
    board = [_card(state, by_id[lid], n, ranking.explain(lid) if lid in passed
                   else explain(by_id[lid], state.preferences, overrides=heard.get(lid)), heard.get(lid, {}))
             for n, lid in enumerate(shown, start=1)]
    top = board[0].address if board else None
    says = (f"{len(matches)} places fit. {top} is on top." if top
            else "Nothing fits yet. Want to widen the budget or the area?")
    plan = state.plan.listing_ids if state.plan else requested
    return SessionView(
        session_id=state.session_id, version=version, phase=state.phase, preferences=state.preferences,
        focus_listing_id=state.focus_listing_id, focus_area=state.focus_area, matches=matches, board=board,
        agent_says=says, plan=list(plan), calls=[_with_outcome(state, c) for c in state.calls.values()],
        results=state.results,
    )


def _with_outcome(state: SessionState, call: CallView) -> CallView:
    """The call with the Auditor's outcome for it, matched on the call, not just the listing, so a listing
    called again never shows the earlier call's outcome."""
    out = state.outcomes.get(call.listing_id)
    if out is None or out.call_id != call.call_id:
        return call
    return call.model_copy(update={"outcome": out})


def _card(state: SessionState, x: Listing, n: int, why: Ranked, heard: dict) -> Card:
    lid = x.listing_id
    outcome = state.outcomes.get(lid)
    said = state.reactions.get(lid)
    booked = state.viewings.get(lid)
    return Card(
        listing_id=lid, address=x.address, unit_label=x.unit_label,
        neighbourhood=x.neighbourhood or x.building_name, lat=x.lat, lng=x.lng, rent=x.rent,
        monthly_cost=heard.get("monthly_cost", x.monthly_cost), beds=x.beds, baths=x.baths, sqft=x.sqft,
        floor=x.floor,
        unit_type=x.unit_type, photo=x.photos[0] if x.photos else None, light_score=x.light_score,
        source=x.source, url=x.url, rank=n, status=state.status(lid), reasons=why.reasons,
        watch_out=why.watch_out, reaction=(said.reason or said.reaction) if said else None,
        facts=list(outcome.facts) if outcome else list(state.heard.get(lid, [])),
        red_flags=list(outcome.red_flags) if outcome else [],
        # The booked viewing (O-08b), waiting for the listing agent to confirm it; O-08c adds the answer.
        viewing=CardViewing(starts_at=booked.starts_at, slot_text=booked.slot_text, status="booked")
        if booked else None,
    )
