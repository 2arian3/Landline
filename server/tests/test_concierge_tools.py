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


SPELT = Catalogue([
    _listing(1, "55 Christie St", 2400, 1, 43.664, -79.418),
    _listing(2, "120 Ossington Ave", 2600, 1, 43.651, -79.420),
    _listing(3, "900 Dufferin St", 2300, 1, 43.660, -79.435),
    _listing(4, "500 Bathurst St", 2500, 1, 43.657, -79.404),
    _listing(5, "30 College St", 2700, 1, 43.661, -79.385),
    _listing(6, "8 Cottage St", 2200, 1, 43.700, -79.390),
    _listing(7, "12 King St W", 2900, 1, 43.648, -79.384),
    _listing(8, "2 Lake Shore Blvd W", 3000, 1, 43.636, -79.410),
], "test")
SPELT_STATIONS = [{"name": "Ossington", "lat": 43.662, "lng": -79.426},
                  {"name": "Spadina", "lat": 43.667, "lng": -79.404}]


@pytest.mark.parametrize(("said", "how", "lat"), [
    ("Christy Street", "street", 43.664),        # #184: how speech spelled Christie
    ("christie st.", "street", 43.664),          # case and punctuation
    ("Osington Avenue", "street", 43.651),       # a doubled letter heard once
    ("Dufferen", "street", 43.660),              # one letter changed
    ("Bathurts Street", "street", 43.657),       # two letters swapped
    ("Lakeshore", "street", 43.636),             # one word for two
    ("Osington station", "station", 43.662),     # said as a station: the station
    ("Spadena", "station", 43.667),              # a station only
    ("Collage Street", None, None),              # College and Cottage are as near: never a guess
    ("Kong Street", None, None),                 # under five letters, a letter off is another word
    ("Narnia Street", None, None),               # nothing near
])
def test_a_street_or_station_spelled_a_little_off_is_still_found(said, how, lat):
    spot = refs.area(said, lambda _: [], SPELT_STATIONS, SPELT)
    assert (spot.how, spot.lat) == (how, pytest.approx(lat)) if how else spot is None, said


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
        def known(self, label: str) -> None:
            return None

        async def resolve(self, label: str, near=()) -> Point:
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


ASKED = "Would you like me to book a viewing if they have a time that works?"
TOMORROW_9_5 = [{"day": "tomorrow", "from": "09:00", "to": "17:00"}]


# The renter's line decides may_book, and the call's timing leads the reply (#154).
@pytest.mark.parametrize(("renter", "agent", "args", "stored", "says"), [
    ("I really like the first one. Let's call the landlord to see if it's available tomorrow for a check.",
     None, {"listing_refs": ["1"], "may_book": True, "availability": TOMORROW_9_5}, False,
     "Booking: not allowed yet. Ask whether you may book a viewing."),
    ("Call them and book a viewing Saturday afternoon.", None, {"listing_refs": ["1"], "may_book": True},
     True, "Booking: I may book one."),
    ("Yes, go ahead and book it.", ASKED, {"listing_refs": ["1"], "may_book": True}, True,
     "Booking: I may book one."),
    ("No, just ask, don't book.", ASKED, {"listing_refs": ["1"], "may_book": False}, False,
     "Booking: I won't book."),
    ("Call the top three.", None, {}, False, "right after you hang up"),
    ("Tomorrow 9 to 5.", None, {"listing_refs": ["1"], "availability": TOMORROW_9_5}, False,
     "Viewing window: tomorrow 9 am to 5 pm."),
])
async def test_call_listings_books_only_with_the_renters_yes_and_says_the_calls_go_out_now(
        ctx, renter, agent, args, stored, says):
    ctx.renter_said, ctx.agent_said = renter, agent
    reply = await _say(ctx, "call_listings", json.dumps(args))
    assert ctx.runtime.state(SID).request.may_book is stored
    assert reply.startswith("Calling ") and " right after you hang up, " in reply and says in reply
    assert "9 am to 5 pm, " not in reply.split(".")[0]   # never a time of day for the calls themselves


async def test_a_day_alone_keeps_default_bounds_and_is_never_spoken_as_hours(ctx):
    """#218: "can you check if it's available tomorrow?" The model sent 9 to 5. Code stores the day with
    the default bounds, and neither the reply nor the Caller says hours the renter never gave."""
    from datetime import date

    from landline.agents.caller.brief import brief, instructions
    from landline.agents.caller.slots import TORONTO, WEEKDAYS, within
    from landline.agents.caller.tools import _ask_time
    from landline.voice.dispatch import freeze

    said = "can you check if it's available tomorrow?"
    ctx.renter_said, ctx.renter_lines = said, [said]
    reply = await _say(ctx, "call_listings", json.dumps({
        "listing_refs": ["1"],
        "availability": [{"day": "tomorrow", "from": "09:00", "to": "17:00"}],
    }))
    request = ctx.runtime.state(SID).request
    assert [(a.day, a.from_, a.to) for a in request.availability] == [("tomorrow", "09:00", "18:00")]
    for phrase in ("9 am", "5 pm", "6 pm", "9:00", "09:00", "17:00", "18:00"):
        assert phrase not in reply.lower(), phrase
    assert "tomorrow" in reply.lower() and "don't name any" in reply

    now = datetime(2026, 10, 24, 15, 0, tzinfo=UTC)   # Saturday 11:00 in Toronto; tomorrow is Sunday
    plan = freeze(ctx.runtime.state(SID), now)
    start = plan.windows[0].start.astimezone(TORONTO)
    end = plan.windows[0].end.astimezone(TORONTO)
    assert (WEEKDAYS[start.weekday()], start.date(), start.hour, start.minute) == (
        "sunday", date(2026, 10, 25), 9, 0)
    assert (end.hour, end.minute) == (18, 0)
    sunday_10 = datetime(2026, 10, 25, 10, 0, tzinfo=TORONTO)
    sunday_8pm = datetime(2026, 10, 25, 20, 0, tzinfo=TORONTO)
    assert within(sunday_10, plan.windows) and not within(sunday_8pm, plan.windows)

    text = instructions(brief(ctx.runtime.catalogue.by_id[request.listing_ids[0]], plan))
    assert "The renter can view: Sunday." in text and "what time on Sunday works" in text
    for phrase in ("9 am", "5 pm", "6 pm", "9:00", "09:00", "17:00", "18:00", "between 9"):
        assert phrase not in text.lower(), phrase
    asked = _ask_time(date(2026, 10, 25), plan.windows, now)
    assert "Ask what time on Sunday works" in asked
    for phrase in ("9 am", "5 pm", "6 pm", "between 9"):
        assert phrase not in asked.lower(), phrase


async def test_a_booking_allowed_earlier_stays_when_a_later_call_leaves_it_out(ctx):
    ctx.renter_said, ctx.agent_said = "Yes, book one.", ASKED
    await _say(ctx, "call_listings", '{"listing_refs": ["1"], "may_book": true}')
    ctx.renter_said = "Also ask if parking is included."
    reply = await _say(ctx, "call_listings", '{"extra_questions": ["Is parking included?"]}', "fc_2")
    assert ctx.runtime.state(SID).request.may_book and reply.endswith("Booking: I may book one.")


async def test_a_phone_renters_calls_after_hours_go_out_at_9_am():
    runtime = SessionRuntime(CATALOGUE)
    await runtime.start(SessionStarted(session_id=SID, at=T0, idem_key=f"{SID}:started", channel="phone"))
    ctx = tools.ToolContext(session_id=SID, call_id="c_tools00009", runtime=runtime)
    try:
        night = datetime(2026, 10, 24, 21, 30, tzinfo=tools.TORONTO)
        meta = {"session_id": SID, "at": night, "idem_key": f"{SID}:night"}
        reply = await tools.call_listings(tools.CallListings(), ctx, meta)
        assert " at 9 am tomorrow, when calling hours start, and texting you" in reply
    finally:
        await runtime.close()


async def test_call_listings_plans_the_top_three_and_reads_them_back(ctx):
    top = [c.listing_id for c in _view(ctx).board[:3]]
    reply = await _say(ctx, "call_listings", '{"availability": [{"day": "saturday", "from": "13:00", '
                                             '"to": "17:00"}], "may_book": true}')
    assert _view(ctx).plan == top
    request = ctx.runtime.state(SID).request
    assert request.may_book and request.availability[0].from_ == "13:00"
    addresses = [CATALOGUE.by_id[lid].address for lid in top]
    assert reply == f"Calling {addresses[0]}, {addresses[1]} and {addresses[2]} right after you hang up, " \
                    "and texting you what they say. Viewing window: Saturday 1 pm to 5 pm. " \
                    "Booking: I may book one. The plan is complete: tell them you'll call as soon as you " \
                    "hang up and text the results, say goodbye, and call end_session."
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
        assert reply.startswith(f"Calling 30 Carabob Court, number {ranks['t:2']}, right after you hang up")
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
    # New times, and nothing said about booking (#189): the agent asks.
    assert reply.endswith("Viewing window: Tuesday 6 pm to 8:30 pm. Booking: not allowed yet. Ask whether "
                          "you may book a viewing in that window.")
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
                                                "focus_area", "call_listings", "listing_status",
                                                "end_session"}


def test_a_place_to_live_near_is_saved_and_focus_area_is_only_for_show_me():
    """The demo opens with "near Christie station": only moving the map wouldn't narrow the pins."""
    from landline.agents.concierge.prompt import INSTRUCTIONS
    text = " ".join(INSTRUCTIONS.split())
    assert ("A place they want to live in or near always goes in update_preferences, never only in "
            "focus_area: focus_area just moves the map, and the list doesn't change.") in text
    assert ('Call focus_area only when they ask to see a part of the city ("show me", "look at", '
            '"zoom to").') in text
    assert "When they name a part of the city to look at, call focus_area." not in text


def test_a_question_that_brings_up_a_new_place_is_a_search_there_not_a_chat():
    """In the judge run "What about the Annex?" got a voice answer and no tool, so the board didn't move."""
    from landline.agents.concierge.prompt import INSTRUCTIONS
    text = " ".join(INSTRUCTIONS.split())
    assert ('A question that brings up a new place ("what about the Annex?", "anything in Leslieville?") is '
            "them asking to look there: put it in areas with update_preferences, as above, and never answer "
            "it without a tool.") in text


# ── a new place replaces the old one unless the renter adds it (#183) ──────

def _places_now(ctx) -> list[tuple[str, bool]]:
    prefs = ctx.runtime.state(SID).preferences
    return [(a, False) for a in prefs.areas] + [(a.label, a.lat is not None) for a in prefs.anchors]


async def test_a_correction_replaces_the_old_place_and_the_list_follows_the_new_one(ctx):
    ctx.renter_said = "Somewhere over by Dupont, please."
    await _say(ctx, "update_preferences", '{"areas": ["over by Dupont"]}')
    assert _places_now(ctx) == [("over by Dupont", True)] and _view(ctx).board[0].address == "322 Dupont St"
    ctx.renter_said = "Sorry, I meant near Bloor."
    reply = await _say(ctx, "update_preferences", '{"areas": ["Bloor"]}', "fc_2")
    assert _places_now(ctx) == [("Bloor", True)]                  # Dupont no longer steers the list
    assert "Bloor" in _view(ctx).board[0].address
    assert "near Bloor" in reply and "over by Dupont" not in reply   # a listing on Dupont St may be named


async def test_also_adds_a_place_to_the_ones_before(ctx):
    ctx.renter_said = "Near Dupont."
    await _say(ctx, "update_preferences", '{"areas": ["Dupont"]}')
    ctx.renter_said = "And also near Bloor."
    await _say(ctx, "update_preferences", '{"areas": ["Bloor"]}', "fc_2")
    assert _places_now(ctx) == [("Dupont", True), ("Bloor", True)]


async def test_with_no_renter_line_a_new_place_replaces_by_default(ctx):
    await _say(ctx, "update_preferences", '{"areas": ["Dupont"]}')
    await _say(ctx, "update_preferences", '{"anchors": [{"label": "Bloor"}]}', "fc_2")
    assert _places_now(ctx) == [("Bloor", True)]


async def test_show_me_the_options_near_a_place_ranks_by_it_where_the_map_went(ctx):
    ctx.renter_said = "Near Dupont."
    await _say(ctx, "update_preferences", '{"areas": ["Dupont"]}')
    ctx.renter_said = "Show me Bloor on the map."                  # only the map: the search stays
    assert await _say(ctx, "focus_area", '{"place": "Bloor"}', "fc_2") == "Showing Bloor."
    assert _places_now(ctx) == [("Dupont", True)]
    ctx.renter_said = "Can you show me the options near Bloor?"   # listings there: the list follows
    reply = await _say(ctx, "focus_area", '{"place": "Bloor"}', "fc_3")
    assert reply.startswith("Showing Bloor, and the list now ranks by it.")
    area, anchor = _view(ctx).focus_area, ctx.runtime.state(SID).preferences.anchors[0]
    assert _places_now(ctx) == [("Bloor", True)] and (anchor.lat, anchor.lng) == (area.lat, area.lng)
    assert "Bloor" in _view(ctx).board[0].address



# ── "what are my top options?" names the top three (#183) ────────────────────

def _three(ctx) -> str:
    return "Top three: " + "; ".join(f"{c.rank}. {c.address}, ${c.monthly_cost:,}, "
                                     f"{'1 bedroom' if c.beds == 1 else f'{int(c.beds)} bedrooms'}"
                                     for c in _view(ctx).board[:3]) + "."


async def test_asking_for_the_top_options_gets_the_top_three_named(ctx):
    ctx.renter_said = "Up to 3,000. What are my top options now?"
    reply = await _say(ctx, "update_preferences", '{"max_monthly": 3000}')
    assert reply.endswith(_three(ctx)) and reply.count(";") == 2


async def test_a_board_change_not_asking_for_options_names_none(ctx):
    ctx.renter_said = "Up to 3,000 a month."
    assert "Top three" not in await _say(ctx, "update_preferences", '{"max_monthly": 3000}')


async def test_a_reaction_and_a_question_for_the_best_ones_names_them(ctx):
    ctx.renter_said = "The first one is too pricey. What are the best ones now?"
    reply = await _say(ctx, "give_feedback",
                       '{"reaction": "dislike", "attribute": "cost", "listing_ref": "1"}')
    assert reply.startswith("Noted for ") and reply.endswith(_three(ctx))


async def test_show_me_the_options_near_a_place_names_the_new_top_three(ctx):
    ctx.renter_said = "Can you show me the options near Bloor?"
    reply = await _say(ctx, "focus_area", '{"place": "Bloor"}')
    assert reply.startswith("Showing Bloor, and the list now ranks by it.") and reply.endswith(_three(ctx))
    assert "Bloor" in _view(ctx).board[0].address



# ── once the plan is complete, the Concierge says goodbye and ends ───────────

def test_the_prompt_wraps_up_once_the_brief_is_done():
    from landline.agents.concierge.prompt import INSTRUCTIONS
    text = " ".join(INSTRUCTIONS.split())
    assert ("When call_listings' reply says to say goodbye, the brief is done: don't wait for them to "
            "say bye. In that same turn say \"I'll call them as soon as we hang up and text you what they "
            "say. Bye for now!\", then call end_session. If they add something first, handle it, then do "
            "this.") in text
    assert ("When the renter's words are only bye or goodbye, call end_session and say nothing. Don't "
            "answer again.") in text
    assert "Never say goodbye more than once." in text
