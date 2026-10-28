"""ask_maps (O-07b): area questions answered from Google's Maps MCP server, under option B (Google's place is
never put on our map). A fake client returns O-07a's own answer types; nothing leaves the machine."""
import asyncio
from datetime import UTC, date, datetime

import pytest

from landline.agents.concierge import tools
from landline.contracts import Listing, ListingFocused, SessionStarted
from landline.db.catalogue import Catalogue
from landline.orchestrator import SessionRuntime
from landline.orchestrator.maps_mcp import DISCLOSURE, WALK_WARNING, Nearby, Route, Weather

T0 = datetime(2026, 10, 28, 15, 0, tzinfo=UTC)
SID = "s_askmaps001"
MAPS_URL = "https://maps.google.com/?cid=123"


def _listing(i: int, lat: float, lng: float) -> Listing:
    return Listing(listing_id=f"t:{i}", source="t", source_id=str(i), url=f"https://example.com/{i}",
                   first_seen=T0, last_seen=T0, address=f"{i} Bloor St W", lat=lat, lng=lng, rent=2000 + i,
                   monthly_cost=2000 + i, beds=1, sqft=600)


CATALOGUE = Catalogue([_listing(1, 43.6650, -79.4110), _listing(2, 43.6700, -79.3900),
                       _listing(3, 43.6550, -79.4000)], "test")


class FakeMaps:
    """MapsMCP's nearby / route / weather, answering after `delay` seconds."""

    def __init__(self, name: str | None = "Shoppers Drug Mart", minutes: int | None = 6, delay: float = 0):
        self.name, self.minutes, self.delay, self.asked = name, minutes, delay, []

    def _source(self) -> dict:
        return {"attribution": "Google Maps", "attribution_url": "https://maps.google.com", "fetched_at": T0}

    async def nearby(self, lat, lng, what):
        self.asked.append(("nearby", lat, lng, what))
        await asyncio.sleep(self.delay)
        return Nearby(**self._source(), name=self.name, place_id="p1", lat=43.66, lng=-79.41,
                      maps_url=MAPS_URL, summary="…", minutes=self.minutes,
                      distance_m=480 if self.minutes else None,
                      warning=WALK_WARNING if self.minutes is not None else None)

    async def route(self, origin, destination, mode="WALK"):
        self.asked.append(("route", origin, destination, mode))
        await asyncio.sleep(self.delay)
        return Route(**self._source(), mode=mode, minutes=24, distance_m=5200, km=5.2,
                     warning=WALK_WARNING if mode == "WALK" else None)

    async def weather(self, lat, lng, day):
        self.asked.append(("weather", lat, lng, day))
        return Weather(**self._source(), day=day,
                       summary="Light rain, high 12°C, low 6°C, 60% chance of rain.",
                       condition="Light rain", high_c=12, low_c=6, precip_pct=60)

    async def close(self):
        pass


@pytest.fixture
async def ctx():
    runtime = SessionRuntime(CATALOGUE)
    runtime.maps = FakeMaps()
    await runtime.start(SessionStarted(session_id=SID, at=T0, idem_key=f"{SID}:started", channel="phone"))
    texts: list[str] = []
    yield tools.ToolContext(session_id=SID, call_id="c_askmaps001", runtime=runtime, text=texts.append)
    await runtime.close()


async def _ask(ctx, args: str, tool_call_id: str = "fc_1") -> str:
    return await tools.handle("ask_maps", args, tool_call_id, ctx)


def _texts(ctx) -> list[str]:
    return ctx.text.__self__


async def _focus(ctx, n: int) -> None:
    await ctx.runtime.emit(ListingFocused(session_id=SID, at=T0, idem_key=f"{SID}:f{n}", listing_id=f"t:{n}",
                                          origin="ui"))


async def test_without_a_maps_client_the_tool_isnt_offered_or_run():
    runtime = SessionRuntime(CATALOGUE)
    assert runtime.maps is None
    assert "ask_maps" not in [t["name"] for t in tools.offered(runtime)]
    ctx = tools.ToolContext(session_id=SID, call_id="c_x", runtime=runtime)
    assert await _ask(ctx, '{"kind": "nearby", "what": "a pharmacy"}') == tools.UNKNOWN
    runtime.maps = FakeMaps()
    assert tools.offered(runtime)[-1]["name"] == "ask_maps"


async def test_the_closest_place_with_the_disclosure_once_the_walk_warning_and_the_texted_link(ctx):
    await _focus(ctx, 1)
    reply = await _ask(ctx, '{"kind": "nearby", "what": "pharmacy"}')
    assert reply == (f"{DISCLOSURE}. The closest pharmacy to 1 Bloor St W is Shoppers Drug Mart, about 6 "
                     "minutes' walk. Google says its walking routes are in beta. "
                     "I've texted you the Google Maps link.")
    texted = _texts(ctx)[0]
    assert texted.startswith("Landline: The closest pharmacy to 1 Bloor St W is Shoppers Drug Mart")
    assert f"Source: Google Maps {MAPS_URL}" in texted and WALK_WARNING in texted and DISCLOSURE in texted
    again = await _ask(ctx, '{"kind": "nearby", "what": "gym"}', "fc_2")
    assert DISCLOSURE not in again   # said once a call


async def test_a_place_without_a_clear_name_is_never_given_one(ctx):
    ctx.runtime.maps = FakeMaps(name=None)
    await _focus(ctx, 1)
    reply = await _ask(ctx, '{"kind": "nearby", "what": "pharmacy"}')
    assert "There's a pharmacy about 6 minutes' walk from 1 Bloor St W." in reply
    assert "Shoppers" not in reply


async def test_the_listing_comes_from_the_ref_or_the_focus_never_from_the_model(ctx):
    maps = ctx.runtime.maps
    assert await _ask(ctx, '{"kind": "nearby", "what": "pharmacy"}') == \
        "Which listing? Ask the renter for its number or street."   # nothing focused, nothing named
    board = ctx.runtime.get(SID).board
    second = CATALOGUE.by_id[board[1].listing_id]
    await _ask(ctx, '{"kind": "nearby", "what": "pharmacy", "listing_ref": "2"}', "fc_2")
    assert maps.asked[-1] == ("nearby", second.lat, second.lng, "pharmacy")
    await _focus(ctx, 3)
    await _ask(ctx, '{"kind": "nearby", "what": "pharmacy"}', "fc_3")
    assert maps.asked[-1][1:3] == (43.6550, -79.4000)


async def test_routes_and_the_weather_in_words(ctx):
    await _focus(ctx, 1)
    walk = await _ask(ctx, '{"kind": "walk", "to": "Christie Pits"}')
    assert "From 1 Bloor St W, Christie Pits is about 24 minutes on foot." in walk and "in beta" in walk
    transit = await _ask(ctx, '{"kind": "transit", "to": "King and Bay"}', "fc_2")
    assert transit.startswith("From 1 Bloor St W, King and Bay is about 24 minutes by transit.")
    assert "in beta" not in transit
    assert ctx.runtime.maps.asked[-1] == ("route", (43.6650, -79.4110), "King and Bay", "TRANSIT")
    rain = await _ask(ctx, '{"kind": "weather", "day": "Saturday"}', "fc_3")
    assert rain.startswith("Saturday near 1 Bloor St W: Light rain, high 12°C, low 6°C, 60% chance of rain.")
    assert ctx.runtime.maps.asked[-1][3].weekday() == 5
    assert await _ask(ctx, '{"kind": "walk"}', "fc_4") == "Ask the renter where to."


def test_a_forecast_day_in_words():
    wednesday = date(2026, 10, 28)
    assert tools._day("Saturday", wednesday) == date(2026, 10, 31)
    assert tools._day("tomorrow", wednesday) == date(2026, 10, 29)
    assert tools._day(None, wednesday) == wednesday


async def test_in_the_browser_nothing_is_texted_but_the_disclosure_is_still_said(ctx):
    ctx.text = None
    await _focus(ctx, 1)
    reply = await _ask(ctx, '{"kind": "nearby", "what": "pharmacy"}')
    assert reply.startswith(DISCLOSURE) and "texted" not in reply


async def test_a_slow_answer_comes_between_turns(ctx, monkeypatch):
    monkeypatch.setattr(tools, "MAPS_WAIT_S", 0.01)
    ctx.runtime.maps = FakeMaps(delay=0.05)
    notes: list[str] = []

    async def note(said: str) -> None:
        notes.append(said)

    ctx.note = note
    await _focus(ctx, 1)
    assert await _ask(ctx, '{"kind": "nearby", "what": "pharmacy"}') == tools.CHECKING_MAP
    await ctx.runtime.idle()
    assert len(notes) == 1 and notes[0].startswith(f"{tools.MAP_ANSWER} {DISCLOSURE}. The closest pharmacy")


async def test_a_late_answer_after_hang_up_or_a_newer_question_is_dropped(ctx, monkeypatch):
    monkeypatch.setattr(tools, "MAPS_WAIT_S", 0.01)
    ctx.runtime.maps = FakeMaps(delay=0.05)
    notes: list[str] = []

    async def note(said: str) -> None:
        notes.append(said)

    ctx.note = note
    await _focus(ctx, 1)
    await _ask(ctx, '{"kind": "nearby", "what": "pharmacy"}')
    await _ask(ctx, '{"kind": "nearby", "what": "gym"}', "fc_2")   # a newer question
    await ctx.runtime.idle()
    assert len(notes) == 1 and "The closest gym" in notes[0]   # only the newer question's answer
    notes.clear()
    texted = len(_texts(ctx))
    await _ask(ctx, '{"kind": "nearby", "what": "pharmacy"}', "fc_3")
    ctx.hang_up = True
    await ctx.runtime.idle()
    assert notes == [] and len(_texts(ctx)) == texted   # nothing said or texted after the goodbye
