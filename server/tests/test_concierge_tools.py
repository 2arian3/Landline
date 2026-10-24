"""The Concierge's tools against a real session runtime (in memory): each tool's event changes the session,
and its spoken reply comes from the board that event produced."""
import asyncio
import json
from datetime import UTC, datetime
from urllib.parse import parse_qs

import httpx
import pytest

from landline.agents.concierge import refs, tools
from landline.contracts import Card, Listing, SessionEnded, SessionStarted
from landline.db.catalogue import Catalogue
from landline.orchestrator import SessionRuntime, places
from landline.orchestrator.places import Places, Point
from landline.scoring.features import FeatureMatrix, Hood

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


def _card(rank: int, address: str, cost: int, lid: str | None = None) -> Card:
    return Card(listing_id=lid or f"t:{rank}", address=address, rent=cost, monthly_cost=cost, beds=1,
                source="t", url="https://example.com", rank=rank)


# The eval's board for a one bedroom under $2,400: three cards at 30 Carabob Court with no unit label.
TWINS = [_card(1, "30 Carabob Court", 1999), _card(2, "701 Don Mills Rd", 1800),
         _card(3, "1915 Martin Grove Rd", 2039), _card(4, "630 Vesta Dr", 1800),
         _card(5, "30 Carabob Court", 1950), _card(6, "30 Carabob Court", 1999)]


def test_a_shared_address_alone_names_no_card_and_offers_every_twin():
    for ref in ("30 Carabob Court", "Carabob", "the one on Carabob Court"):
        found = refs.match(ref, TWINS)
        assert found.card is None, ref
        assert [c.rank for c in found.options] == [1, 5, 6], ref


def test_a_rank_or_a_price_picks_among_twins():
    assert refs.resolve("30 Carabob Court, number 5", TWINS) is TWINS[4]
    assert refs.resolve("the first one on Carabob", TWINS) is TWINS[0]
    assert refs.resolve("30 Carabob Court for 1950", TWINS) is TWINS[4]
    assert refs.resolve("Carabob, the last one", TWINS) is TWINS[5]
    # A rank that isn't one of the twins doesn't pick one; nor does a price two of them share.
    assert refs.resolve("30 Carabob Court, number 2", TWINS) is None
    found = refs.match("30 Carabob Court at 1999", TWINS)
    assert found.card is None and [c.rank for c in found.options] == [1, 6]


def test_the_focused_card_picks_among_twins_but_never_another_card():
    assert refs.resolve("30 Carabob Court", TWINS, focus_id="t:5") is TWINS[4]
    assert refs.resolve("30 Carabob Court", TWINS, focus_id="t:2") is None   # Don Mills isn't a twin
    assert refs.resolve("Don Mills", TWINS, focus_id="t:5") is TWINS[1]      # a unique address wins
    assert refs.resolve("2", TWINS, focus_id="t:5") is TWINS[1]              # so does a rank


def test_unit_labels_tell_twins_apart():
    board = [_card(1, "30 Carabob Court", 1999), _card(2, "30 Carabob Court", 1950)]
    board[1].unit_label = "1204"
    assert refs.resolve("30 Carabob Court unit 1204", board) is board[1]


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
    assert await _say(ctx, "give_feedback", '{"reaction": "dislike"}') == tools.NO_REF   # nothing focused
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
        f"Couldn't find King and Bay on the map. Nothing changed. {tools.ASK_PLACE}"
    assert _view(ctx).version == before


def test_area_finds_places_said_loosely():
    """"Queens" is Queen station; a street said with no type, a possessive or "over by" is still found."""
    stations = [{"name": "Queen", "lat": 43.653, "lng": -79.379}, {"name": "Queen's Park", "lat": 43.660,
                                                                   "lng": -79.390}]
    def find(label):
        return refs.area(label, lambda _: [], stations, CATALOGUE)
    for said in ("Queens", "Queen's", "around Queens", "Queen station"):
        assert find(said).how == "station" and find(said).lat == pytest.approx(43.653), said
    assert find("Queens Park").lat == pytest.approx(43.660)
    assert find("over by Dupont").how == "street" and find("Dupont Street").lat == pytest.approx(43.675)
    east = find("Bloor East")
    assert east.how == "street" and east.lng == pytest.approx(-79.380)   # only the east part of Bloor
    assert find("Leslieville").how == "listings"                         # a listing's neighbourhood
    for nonsense in ("purple fridge", "the", ""):
        assert find(nonsense) is None, nonsense


async def test_update_preferences_places_an_area_or_says_it_cannot(ctx):
    """An area the search can't use doesn't get a bare "got it": a street becomes a point the board moves
    to; a place nothing here holds stays for the geocoder, and the agent hears the list doesn't use it."""
    reply = await _say(ctx, "update_preferences", '{"areas": ["over by Dupont"]}')
    prefs = ctx.runtime.state(SID).preferences
    assert prefs.areas == [] and prefs.anchors[0].label == "over by Dupont"
    assert prefs.anchors[0].lat == pytest.approx(43.675)
    assert _view(ctx).board[0].address == "322 Dupont St" and "Couldn't find" not in reply
    reply = await _say(ctx, "update_preferences", '{"anchors": [{"label": "over by Dupont"}, '
                                                  '{"label": "King and Bay"}]}', "fc_2")
    assert reply.endswith(f"Couldn't find King and Bay on the map, so the list doesn't use it yet. "
                          f"{tools.ASK_PLACE}")
    prefs = ctx.runtime.state(SID).preferences
    assert [(a.label, a.lat is not None) for a in prefs.anchors] == [("over by Dupont", True),
                                                                      ("King and Bay", False)]


KING_AND_BAY = (43.6486561, -79.3802633)
CHRISTIE_PITS = (43.6645, -79.4207)


def _net(request: httpx.Request) -> httpx.Response:
    """A fake Overpass (King and Bay meet) and Nominatim (knows Christie Pits); nothing leaves the machine."""
    if "overpass" in request.url.host:
        query = parse_qs(request.content.decode())["data"][0]
        meets = "King" in query and "Bay" in query
        meet = [{"lat": KING_AND_BAY[0], "lon": KING_AND_BAY[1]}] if meets else []
        return httpx.Response(200, json={"elements": meet})
    known = parse_qs(request.url.query.decode())["q"][0].startswith("Christie Pits")
    found = [{"lat": str(CHRISTIE_PITS[0]), "lon": str(CHRISTIE_PITS[1])}] if known else []
    return httpx.Response(200, json=found)


@pytest.fixture
async def placed(monkeypatch):
    """A session whose runtime places names through Places (D-04), with the Annex, a Christie station and the
    fake network."""
    monkeypatch.setattr(places, "MIN_GAP_S", 0)
    hoods = [Hood(1, "Annex", 43.670, -79.405, ())]
    stations = [{"id": "s1", "name": "Christie", "lat": 43.6640, "lng": -79.4183}]
    client = httpx.AsyncClient(transport=httpx.MockTransport(_net))
    runtime = SessionRuntime(CATALOGUE, places=Places(FeatureMatrix.build([], hoods).named, stations,
                                                      contact="team@example.com", client=client))
    await runtime.start(SessionStarted(session_id=SID, at=T0, idem_key=f"{SID}:started", channel="judge"))
    yield tools.ToolContext(session_id=SID, call_id="c_tools00003", runtime=runtime)
    await runtime.close()
    await client.aclose()


@pytest.mark.parametrize(("place", "point"), [
    ("the Annex", (43.670, -79.405)),        # a City neighbourhood, no network
    ("King and Bay", KING_AND_BAY),          # an intersection, through Overpass
    ("Christie Pits", CHRISTIE_PITS),        # a landmark, through the geocoder, not the Christie station
])
async def test_focus_area_asks_places_for_what_no_listing_names(placed, place, point):
    assert await _say(placed, "focus_area", json.dumps({"place": place})) == f"Showing {place}."
    area = _view(placed).focus_area
    assert (area.label, area.lat, area.lng, area.zoom) == (place, *point, tools.AREA_ZOOM)


async def test_focus_area_says_it_cannot_when_places_finds_nothing_either(placed):
    before = _view(placed).version
    assert await _say(placed, "focus_area", '{"place": "Narnia"}') == \
        f"Couldn't find Narnia on the map. Nothing changed. {tools.ASK_PLACE}"
    assert _view(placed).version == before


async def test_a_slow_lookup_answers_at_once_and_moves_the_map_when_it_lands(placed, monkeypatch):
    class Slow:
        async def resolve(self, label: str) -> Point:
            await asyncio.sleep(0.05)
            return Point(*CHRISTIE_PITS, "geocoder")

    monkeypatch.setattr(tools, "PLACE_WAIT_S", 0.01)
    placed.runtime.places = Slow()
    assert await _say(placed, "focus_area", '{"place": "Christie Pits"}') == \
        "Finding Christie Pits on the map. It'll show in a moment."
    assert _view(placed).focus_area is None
    await asyncio.gather(*placed.lookups)
    area = _view(placed).focus_area
    assert (area.label, area.lat, area.lng) == ("Christie Pits", *CHRISTIE_PITS)


async def test_call_listings_plans_the_top_three_and_reads_them_back(ctx):
    top = [c.listing_id for c in _view(ctx).board[:3]]
    reply = await _say(ctx, "call_listings", '{"availability": [{"day": "saturday", "from": "13:00", '
                                             '"to": "17:00"}], "may_book": true}')
    assert _view(ctx).plan == top
    request = ctx.runtime.state(SID).request
    assert request.may_book and request.availability[0].from_ == "13:00"
    addresses = [CATALOGUE.by_id[lid].address for lid in top]
    assert reply == f"I'll call {addresses[0]}, {addresses[1]} and {addresses[2]} after you hang up, " \
                    "and text you what they say. Viewings Saturday 1 pm to 5 pm. I may book one."
    assert await _say(ctx, "call_listings", '{"listing_refs": ["Spadina"]}', "fc_2") == tools.WHICH


async def test_a_shared_address_asks_with_numbers_then_reads_the_number_back():
    twins = Catalogue([_listing(1, "30 Carabob Court", 1999, 1, 43.78, -79.30),
                       _listing(2, "30 Carabob Court", 1950, 1, 43.78, -79.30),
                       _listing(3, "701 Don Mills Rd", 2100, 1, 43.71, -79.34)], "test")
    runtime = SessionRuntime(twins)
    await runtime.start(SessionStarted(session_id=SID, at=T0, idem_key=f"{SID}:started", channel="judge"))
    ctx = tools.ToolContext(session_id=SID, call_id="c_tools00002", runtime=runtime)
    try:
        board = runtime.get(SID).board
        ranks = {c.listing_id: c.rank for c in board}
        reply = await _say(ctx, "call_listings", '{"listing_refs": ["30 Carabob Court"]}')
        assert reply.startswith("Which one? 2 places share 30 Carabob Court: number ")
        assert "$1,999" in reply and "$1,950" in reply and len(reply.split()) <= 25
        assert runtime.state(SID).request is None                      # nobody gets phoned on a guess
        await _say(ctx, "focus_listing", '{"listing_ref": "Carabob, the 1950 one"}', "fc_2")
        assert runtime.get(SID).focus_listing_id == "t:2"
        reply = await _say(ctx, "call_listings", '{"listing_refs": ["30 Carabob Court"]}', "fc_3")
        assert runtime.state(SID).request.listing_ids == ["t:2"]       # the one being talked about
        assert reply.startswith(f"I'll call 30 Carabob Court, number {ranks['t:2']}, after you hang up")
        await _say(ctx, "give_feedback", '{"reaction": "like", "listing_ref": "30 Carabob Court"}', "fc_4")
        assert runtime.state(SID).reactions["t:2"].reaction == "like"
    finally:
        await runtime.close()


async def test_rank_numbers_sent_as_numbers_count_as_ranks(ctx):
    board = _view(ctx).board
    await _say(ctx, "call_listings", '{"listing_refs": [1, 3]}')
    assert ctx.runtime.state(SID).request.listing_ids == [board[0].listing_id, board[2].listing_id]
    await _say(ctx, "focus_listing", '{"listing_ref": 2}', "fc_2")
    assert _view(ctx).focus_listing_id == board[1].listing_id


async def test_calling_again_updates_the_plan_instead_of_replacing_it(ctx):
    board = _view(ctx).board
    await _say(ctx, "call_listings", '{"listing_refs": ["2"], "extra_questions": ["Is parking included?"]}')
    reply = await _say(ctx, "call_listings", '{"availability": [{"day": "tuesday", "from": "18:00", '
                                             '"to": "20:30"}]}', "fc_2")
    request = ctx.runtime.state(SID).request
    assert request.listing_ids == [board[1].listing_id]          # not the top three
    assert request.questions == ["Is parking included?"] and not request.may_book
    assert reply.endswith("Viewings Tuesday 6 pm to 8:30 pm. I won't book.")
    await _say(ctx, "call_listings", '{"may_book": true, "extra_questions": ["Dogs ok?"]}', "fc_3")
    request = ctx.runtime.state(SID).request
    assert request.may_book and request.availability[0].day == "tuesday"
    assert request.questions == ["Is parking included?", "Dogs ok?"]


async def test_plan_fields_sent_to_update_preferences_say_where_they_belong(ctx):
    before = _view(ctx).version
    reply = await _say(ctx, "update_preferences", '{"availability": [{"day": "saturday", "from": "10:00", '
                                                  '"to": "14:00"}], "may_book": true}')
    assert reply == "Not saved: availability, may_book; call call_listings with them."
    assert _view(ctx).version == before and ctx.runtime.state(SID).request is None
    reply = await _say(ctx, "update_preferences", '{"max_monthly": 3000, "may_book": true}', "fc_2")
    assert _view(ctx).preferences.max_monthly == 3000
    assert reply.endswith("Not saved: may_book; call call_listings with them.")
    assert await _say(ctx, "end_session", '{"reason": "done"}', "fc_3") and ctx.hang_up


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
