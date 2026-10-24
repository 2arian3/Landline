"""Feedback moves the ranking (O-04): each reaction's effect from docs/SCORING.md §3, through the runtime."""
from datetime import UTC, datetime

from landline.contracts import (
    AnchorResolved,
    FeedbackGiven,
    Listing,
    ListingFocused,
    PreferenceDelta,
    PreferencesUpdated,
    SessionStarted,
)
from landline.contracts.tools import AnchorRef
from landline.db.catalogue import Catalogue
from landline.orchestrator import SessionRuntime
from landline.orchestrator.feedback import adjustments, attribute_of
from landline.scoring.weights import CAP

NOW = datetime(2026, 10, 24, 12, tzinfo=UTC)
SID = "s_feed000001"


def _listing(n: int, light: float, rent: int = 2000, sqft: int = 700, lat: float = 43.65,
             unit_type: str = "condo") -> Listing:
    return Listing(listing_id=f"t:{n}", source="t", source_id=str(n), url=f"https://example.com/{n}",
                   first_seen=NOW, last_seen=NOW, address=f"{n} Feedback St", lat=lat, lng=-79.38, rent=rent,
                   monthly_cost=rent, beds=1, sqft=sqft, light_score=light, unit_type=unit_type)


async def _session(listings: list[Listing], **prefs) -> SessionRuntime:
    rt = SessionRuntime(Catalogue(listings, "test"))
    await rt.start(SessionStarted(session_id=SID, at=NOW, idem_key=f"{SID}:start", channel="judge"))
    await rt.emit(PreferencesUpdated(session_id=SID, at=NOW, idem_key=f"{SID}:prefs",
                                     delta=PreferenceDelta(max_monthly=3000, **prefs)))
    return rt


async def _react(rt: SessionRuntime, lid: str, reaction: str, reason: str | None = None, key: str = "f",
                 attribute: str | None = None):
    return await rt.emit(FeedbackGiven(session_id=SID, at=NOW, idem_key=f"{SID}:{key}", listing_id=lid,
                                       reaction=reaction, reason=reason, attribute=attribute))


def _order(view) -> list[str]:
    return [c.listing_id for c in view.board]


async def test_too_dark_on_the_focused_card_sinks_darker_units():
    # Cheaper, darker places lead on price; light weighs 1 against cost's 3 until the renter says otherwise.
    rt = await _session([_listing(1, 0.25, rent=1400, unit_type="basement"), _listing(2, 0.3, rent=1450),
                         _listing(3, 0.9, rent=2800), _listing(4, 0.85, rent=2850)])
    before = _order(rt.get(SID))
    assert before == ["t:2", "t:1", "t:3", "t:4"]
    await rt.emit(ListingFocused(session_id=SID, at=NOW, idem_key=f"{SID}:focus", listing_id="t:1",
                                 origin="ui"))
    after = await _react(rt, "t:1", "dislike", "too dark")   # a tap on the page: a reason, no attribute
    # The brightest rises, both dark ones sink, and the one called too dark no longer counts its light.
    assert _order(after) == ["t:3", "t:2", "t:4", "t:1"]
    card = next(c for c in after.board if c.listing_id == "t:1")
    assert card.reaction == "too dark"
    assert after.agent_says == "3 Feedback St moves to the top. 2 Feedback St drops to second."
    # Said twice about two places, light keeps rising but never past the cap.
    await _react(rt, "t:2", "dislike", "still too dark", key="f2")
    assert adjustments(rt.state(SID), rt.catalogue).boosts == {"light": 2.0}


async def test_a_reject_takes_the_place_off_the_board_and_a_like_keeps_it():
    rt = await _session([_listing(1, 0.5), _listing(2, 0.5), _listing(3, 0.5)])
    gone = await _react(rt, "t:2", "reject", "not for me")
    assert "t:2" not in _order(gone) and len(gone.board) == 2
    kept = await _react(rt, "t:1", "like", key="f2")
    assert "t:1" in _order(kept)
    assert adjustments(rt.state(SID), rt.catalogue).liked == ["t:1"]


async def test_too_far_tightens_distance_and_too_small_raises_the_size_wanted():
    near, far = _listing(1, 0.5, lat=43.650), _listing(2, 0.5, lat=43.680, sqft=500)
    rt = await _session([near, far], anchors=[AnchorRef(label="King and Bay")])
    await rt.emit(AnchorResolved(session_id=SID, at=NOW, idem_key=f"{SID}:anchor", label="King and Bay",
                                 lat=43.6487, lng=-79.3816))
    await _react(rt, "t:2", "dislike", "too far")
    await _react(rt, "t:2", "dislike", "too small", key="f2")   # a changed mind replaces the first reason
    adj = adjustments(rt.state(SID), rt.catalogue)
    assert adj.boosts == {"size": 1.0} and adj.desired_sqft == 575 and adj.decay_km is None
    await _react(rt, "t:1", "dislike", "a bit far from work", key="f3")
    adj = adjustments(rt.state(SID), rt.catalogue)
    assert adj.boosts == {"size": 1.0, "location": 1.0} and adj.decay_km == 1.8


async def test_a_dislike_with_no_reason_takes_the_card_off_the_top():
    # "Just not for me": no feature to move, so the card itself drops, and no weight changes.
    rt = await _session([_listing(1, 0.5, rent=1400), _listing(2, 0.5, rent=1450),
                         _listing(3, 0.5, rent=1500)])
    assert _order(rt.get(SID)) == ["t:1", "t:2", "t:3"]
    after = await _react(rt, "t:1", "dislike", "just not for me")
    assert _order(after) == ["t:2", "t:3", "t:1"]
    adj = adjustments(rt.state(SID), rt.catalogue)
    assert adj.disliked == {"t:1": None} and adj.boosts == {}


async def test_too_small_on_a_unit_whose_size_is_unknown_still_drops_it_and_keeps_it_shown():
    # Nothing to zero for its size, so it takes the flat penalty; the board still explains it.
    rt = await _session([_listing(1, 0.5, rent=1400, sqft=None)] +
                        [_listing(n, 0.5, rent=1400 + 50 * n, sqft=600) for n in range(2, 9)])
    assert _order(rt.get(SID))[0] == "t:1"
    after = await _react(rt, "t:1", "dislike", "too small")
    assert _order(after)[-1] == "t:1"   # last of eight, so below the top six, and still on the board


async def test_too_expensive_centres_the_cost_curve_on_that_price_but_keeps_the_budget():
    rt = await _session([_listing(1, 0.5, rent=2500), _listing(2, 0.5, rent=2000),
                         _listing(3, 0.5, rent=3200)])
    await _react(rt, "t:1", "dislike", "too expensive")
    adj = adjustments(rt.state(SID), rt.catalogue)
    assert adj.cost_budget == 2500 and adj.boosts == {"cost": 1.0}
    assert _order(rt.get(SID))[0] == "t:2"
    assert "t:3" in _order(rt.get(SID))   # over that price but inside the stated $3,000 + 10%: still shown


async def test_needing_parking_makes_it_a_must_have():
    rt = await _session([_listing(1, 0.5), _listing(2, 0.5)])
    await _react(rt, "t:1", "dislike", "I need parking")
    assert adjustments(rt.state(SID), rt.catalogue).must_have == ["parking"]


def test_the_concierge_attribute_wins_and_unknown_reasons_change_nothing():
    assert attribute_of("cost", "too dark") == "cost"
    assert attribute_of(None, "Way too expensive") == "cost"
    assert attribute_of(None, "the kitchen is ugly") is None


async def test_weights_never_pass_the_cap():
    rt = await _session([_listing(n, 0.3 + n / 20) for n in range(1, 9)])
    for n in range(1, 9):
        await _react(rt, f"t:{n}", "dislike", "too dark", key=f"f{n}")
    boost = adjustments(rt.state(SID), rt.catalogue).boosts["light"]
    from landline.scoring import weights_for
    assert boost == 8.0 and weights_for(rt.state(SID).preferences, {"light": boost})["light"] == CAP
