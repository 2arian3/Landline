"""The Concierge's tools against a real session runtime (in memory): each tool's event changes the session,
and its spoken reply comes from the board that event produced."""
from datetime import UTC, datetime

import pytest

from landline.agents.concierge import refs, tools
from landline.contracts import Listing, SessionEnded, SessionStarted
from landline.db.catalogue import Catalogue
from landline.orchestrator import SessionRuntime

T0 = datetime(2026, 10, 23, 23, 0, tzinfo=UTC)
SID = "s_tools00001"


def _listing(i: int, address: str, rent: int, beds: float, lat: float, lng: float,
             neighbourhood: str | None = None) -> Listing:
    return Listing(listing_id=f"t:{i}", source="t", source_id=str(i), url=f"https://example.com/{i}",
                   first_seen=T0, last_seen=T0, address=address, lat=lat, lng=lng, rent=rent,
                   monthly_cost=rent, beds=beds, sqft=700, neighbourhood=neighbourhood)


CATALOGUE = Catalogue([
    _listing(1, "412 Markham St", 2950, 2, 43.660, -79.410),
    _listing(2, "88 Erskine Ave", 2600, 1, 43.710, -79.400),
    _listing(3, "322 Dupont St", 2400, 1, 43.675, -79.405),
    _listing(4, "88 Bloor St E", 3100, 2, 43.670, -79.380),
    _listing(5, "200 Bloor St W", 2800, 2, 43.668, -79.398),
    _listing(6, "15 Leslie St", 2500, 1, 43.662, -79.333, neighbourhood="Leslieville"),
], "test")


@pytest.fixture
async def ctx():
    runtime = SessionRuntime(CATALOGUE)
    await runtime.start(SessionStarted(session_id=SID, at=T0, idem_key=f"{SID}:started", channel="judge"))
    yield tools.ToolContext(session_id=SID, call_id="c_tools00001", runtime=runtime)
    await runtime.close()


async def _say(ctx, name: str, arguments: str = "{}", tool_call_id: str = "fc_1") -> str:
    return await tools.handle(name, arguments, tool_call_id, ctx)


def _view(ctx):
    return ctx.runtime.get(SID)


# ── resolving what the renter named ─────────────────────────────────────────

async def test_refs_resolve_ranks_and_address_words(ctx):
    board = _view(ctx).board
    assert len(board) == 6
    for ref in ("2", "#2", "second", "the second one", "number two"):
        assert refs.resolve(ref, board) is board[1], ref
    assert refs.resolve("last", board) is board[-1]
    assert refs.resolve("Markham", board).address == "412 Markham St"
    assert refs.resolve("412", board).address == "412 Markham St"       # too big for a rank: an address
    assert refs.resolve("88 Bloor", board).address == "88 Bloor St E"   # beats 88 Erskine and 200 Bloor
    assert refs.resolve("Bloor", board) is None                          # two on Bloor: ask which
    for ref in ("", "Spadina", "ninth"):
        assert refs.resolve(ref, board) is None, ref


def test_place_uses_neighbourhoods_and_street_names_only():
    assert refs.place("Leslieville", CATALOGUE) == pytest.approx((43.662, -79.333))
    assert refs.place("Bloor", CATALOGUE) == pytest.approx((43.669, -79.389))   # the two Bloor listings
    assert refs.place("King and Bay", CATALOGUE) is None                          # waits for the geocoder


# ── each tool ───────────────────────────────────────────────────────────────

async def test_update_preferences_re_ranks_and_speaks_the_board(ctx):
    before = _view(ctx).version
    reply = await _say(ctx, "update_preferences", '{"max_monthly": 3000, "min_beds": 2}')
    view = _view(ctx)
    assert (view.preferences.max_monthly, view.preferences.min_beds) == (3000, 2)
    assert view.version == before + 1 and reply == view.agent_says
    assert {c.address for c in view.board} >= {"412 Markham St", "200 Bloor St W"}
    await _say(ctx, "update_preferences", '{"max_monthly": 2000}')   # the same tool call again: a no-op
    assert _view(ctx).version == before + 1 and _view(ctx).preferences.max_monthly == 3000


async def test_focus_listing_by_rank_reads_the_card_back(ctx):
    second = _view(ctx).board[1]
    reply = await _say(ctx, "focus_listing", '{"listing_ref": "second"}')
    assert _view(ctx).focus_listing_id == second.listing_id
    assert reply.startswith(second.address) and f"${second.monthly_cost:,} a month" in reply
    assert await _say(ctx, "focus_listing", '{"listing_ref": "Spadina"}', "fc_2") == tools.WHICH


async def test_give_feedback_lands_on_the_focused_listing(ctx):
    assert await _say(ctx, "give_feedback", '{"reaction": "dislike"}') == tools.WHICH   # nothing focused
    await _say(ctx, "focus_listing", '{"listing_ref": "Markham"}', "fc_2")
    reply = await _say(ctx, "give_feedback", '{"reaction": "dislike", "reason": "too dark", '
                                             '"attribute": "light"}', "fc_3")
    card = next(c for c in _view(ctx).board if c.address == "412 Markham St")
    assert card.reaction == "too dark" and reply.startswith("Noted for 412 Markham St.")


async def test_focus_area_flies_to_a_known_street_or_says_it_cannot(ctx):
    assert await _say(ctx, "focus_area", '{"place": "Bloor"}') == "Showing Bloor."
    area = _view(ctx).focus_area
    assert area.label == "Bloor" and area.lat == pytest.approx(43.669) and area.zoom == tools.AREA_ZOOM
    before = _view(ctx).version
    assert await _say(ctx, "focus_area", '{"place": "King and Bay"}', "fc_2") == \
        "I can't place that on the map yet."
    assert _view(ctx).version == before


async def test_call_listings_plans_the_top_three_and_reads_them_back(ctx):
    top = [c.listing_id for c in _view(ctx).board[:3]]
    reply = await _say(ctx, "call_listings", '{"availability": [{"day": "saturday", "from": "13:00", '
                                             '"to": "17:00"}], "may_book": true}')
    assert _view(ctx).plan == top
    request = ctx.runtime.state(SID).request
    assert request.may_book and request.availability[0].from_ == "13:00"
    addresses = [CATALOGUE.by_id[lid].address for lid in top]
    assert reply == f"I'll call {addresses[0]}, {addresses[1]} and {addresses[2]} after you hang up, " \
                    "and text you what they say."
    assert await _say(ctx, "call_listings", '{"listing_refs": ["Spadina"]}', "fc_2") == tools.WHICH


async def test_call_listings_after_the_brief_speaks_the_refusal(ctx):
    await _say(ctx, "call_listings", "{}")
    await ctx.runtime.emit(SessionEnded(session_id=SID, at=T0, idem_key=f"{SID}:ended", reason="hangup"))
    assert await _say(ctx, "call_listings", "{}", "fc_2") == "Those calls are already under way."


async def test_end_session_asks_the_bridge_to_hang_up(ctx):
    assert await _say(ctx, "end_session") == "Goodbye." and ctx.hang_up


async def test_bad_arguments_unknown_and_deferred_tools(ctx):
    assert await _say(ctx, "give_feedback", '{"reaction": "meh"}') == tools.NOT_CAUGHT
    assert await _say(ctx, "dial_anyone", '{"number": "+1"}') == tools.UNKNOWN
    assert await _say(ctx, "remember_me") == tools.UNKNOWN
    assert {t["name"] for t in tools.TOOLS} == {"update_preferences", "give_feedback", "focus_listing",
                                                "focus_area", "call_listings", "end_session"}
