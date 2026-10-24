"""The SessionView a session's state produces: the Matcher's board plus what calls found.

Built inside `session_tx` on every commit (a re-rank is < 5 ms), so the board can
never lag the state it came from.
"""
from __future__ import annotations

from landline.contracts import Card, Listing, SessionView
from landline.db.catalogue import Catalogue
from landline.orchestrator import feedback
from landline.orchestrator.state import SessionState
from landline.scoring import FeatureMatrix, Ranked, explain

BOARD_SIZE = 6


def build(state: SessionState, version: int, catalogue: Catalogue, matrix: FeatureMatrix) -> SessionView:
    status = state.statuses()
    ranking = feedback.ranked(state, catalogue, matrix, status)   # the renter's reactions move it (O-04)
    by_id = catalogue.by_id
    matches = [lid for lid in ranking.order if by_id[lid].lat is not None]
    shown = matches[:BOARD_SIZE]
    # A listing being called stays on the board even if it slips below the top or out of the filters
    # (a call found it rented): its status must show.
    requested = state.request.listing_ids if state.request else []
    called = [c.listing_id for c in state.calls.values()] + requested
    shown += [lid for lid in dict.fromkeys(called) if lid not in shown and lid in by_id]
    passed = ranking.scores
    board = [_card(state, by_id[lid], n, ranking.explain(lid) if lid in passed
                   else explain(by_id[lid], state.preferences))
             for n, lid in enumerate(shown, start=1)]
    top = board[0].address if board else None
    says = (f"{len(matches)} places fit. {top} is on top." if top
            else "Nothing fits yet. Want to widen the budget or the area?")
    plan = state.plan.listing_ids if state.plan else requested
    return SessionView(
        session_id=state.session_id, version=version, phase=state.phase, preferences=state.preferences,
        focus_listing_id=state.focus_listing_id, focus_area=state.focus_area, matches=matches, board=board,
        agent_says=says, plan=list(plan), calls=list(state.calls.values()), results=state.results,
    )


def _card(state: SessionState, x: Listing, n: int, why: Ranked) -> Card:
    lid = x.listing_id
    outcome = state.outcomes.get(lid)
    said = state.reactions.get(lid)
    return Card(
        listing_id=lid, address=x.address, unit_label=x.unit_label,
        neighbourhood=x.neighbourhood or x.building_name, lat=x.lat, lng=x.lng, rent=x.rent,
        monthly_cost=x.monthly_cost, beds=x.beds, baths=x.baths, sqft=x.sqft, floor=x.floor,
        unit_type=x.unit_type, photo=x.photos[0] if x.photos else None, light_score=x.light_score,
        source=x.source, url=x.url, rank=n, status=state.status(lid), reasons=why.reasons,
        watch_out=why.watch_out, reaction=(said.reason or said.reaction) if said else None,
        facts=list(outcome.facts) if outcome else list(state.heard.get(lid, [])),
        red_flags=list(outcome.red_flags) if outcome else [],
    )
