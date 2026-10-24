"""The nearest station and its walk reach the Concierge (#166): in focus_listing's reply and in the tap note,
worded from the listing's own features, so "how far is this from the TTC?" is never guessed."""
from datetime import UTC, datetime

import pytest

from landline.agents.concierge import tools
from landline.contracts import Card, Listing, SessionStarted
from landline.db.catalogue import Catalogue
from landline.orchestrator import SessionRuntime
from landline.scoring.reasons import to_station
from landline.voice.line import describe

T0 = datetime(2026, 10, 24, 15, 26, tzinfo=UTC)
SID = "s_station001"


@pytest.mark.parametrize(("minutes", "station", "words"), [
    (4.2, "Wellesley", "4 min walk to Wellesley station"),
    (0.4, "Union", "next to Union station"),
    (22.0, "Christie", "22 min walk to Christie station"),   # over 15 minutes is still said as it is
    (None, None, None),                                       # outside the city's data: nothing made up
    (5.0, None, None),
])
def test_the_walk_to_the_nearest_station_in_words(minutes, station, words):
    assert to_station(minutes, station) == words


def _listing(station: str | None = "Wellesley", minutes: float | None = 4.2) -> Listing:
    return Listing(listing_id="t:1", source="t", source_id="1", url="https://example.com/1", first_seen=T0,
                   last_seen=T0, address="77 Wellesley St E", lat=43.665, lng=-79.381, rent=1849,
                   monthly_cost=1849, beds=1, sqft=550, neighbourhood="Church-Yonge Corridor",
                   transit_station=station, transit_walk_min=minutes)


def _card(neighbourhood: str | None = "Church-Yonge Corridor") -> Card:
    return Card(listing_id="t:1", address="77 Wellesley St E", rent=1849, monthly_cost=1849, beds=1,
                neighbourhood=neighbourhood, source="t", url="https://example.com/1", rank=1)


def test_the_tap_note_names_the_neighbourhood_and_the_station():
    assert describe(_card(), _listing()) == (
        "77 Wellesley St E: 1 bed, $1,849 a month, in Church-Yonge Corridor, 4 min walk to Wellesley station")


def test_the_tap_note_says_nothing_about_a_station_it_doesnt_know():
    said = describe(_card(neighbourhood=None), _listing(station=None, minutes=None))
    assert said == "77 Wellesley St E: 1 bed, $1,849 a month" and "station" not in said


@pytest.mark.parametrize(("station", "minutes", "has"), [
    ("Wellesley", 4.2, "4 min walk to Wellesley station"),
    ("Union", 0.3, "next to Union station"),
    (None, None, None),
])
async def test_focus_listing_reads_the_station_back(station, minutes, has):
    runtime = SessionRuntime(Catalogue([_listing(station, minutes)], "test"))
    await runtime.start(SessionStarted(session_id=SID, at=T0, idem_key=f"{SID}:started", channel="judge"))
    ctx = tools.ToolContext(session_id=SID, call_id="c_station001", runtime=runtime)
    try:
        reply = await tools.handle("focus_listing", '{"listing_ref": "77 Wellesley St E"}', "fc_1", ctx)
    finally:
        await runtime.close()
    assert reply.startswith("77 Wellesley St E: 1 bedroom, $1,849 a month")
    assert "in Church-Yonge Corridor" in reply
    assert (has in reply) if has else ("station" not in reply and "walk" not in reply)
