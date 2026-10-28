"""O-08b: a booked viewing gets one viewing_confirmations row and one confirm link, texted to the number the
call dialled, and only when that number is on the allowlist. The card shows the viewing as booked. The
Postgres tests run in a throwaway schema (conftest.py) and skip without TEST_DATABASE_URL."""
from datetime import UTC, datetime

import pytest

from landline.config import Settings
from landline.contracts import (
    CallPlan,
    CallsRequested,
    CallsScheduled,
    CardViewing,
    Listing,
    SessionEnded,
    SessionStarted,
    ViewingBooked,
)
from landline.db.catalogue import Catalogue
from landline.db.pool import Database
from landline.orchestrator import SessionRuntime
from landline.orchestrator.viewings import ConfirmLinks, when

NOW = datetime(2026, 10, 24, 15, 0, tzinfo=UTC)          # Saturday 11:00 in Toronto
SAT_2PM = datetime(2026, 10, 31, 18, 0, tzinfo=UTC)      # Saturday 31 Oct, 14:00 in Toronto
SID, CID = "s_view000001", "c_view000001"
AGENT, OFF_LIST, LANDLINE = "+16475550101", "+16475550199", "+12895122338"
LISTINGS = [Listing(listing_id=f"t:{n}", source="t", source_id=str(n), url=f"https://example.com/{n}",
                    first_seen=NOW, last_seen=NOW, address=f"{n}12 Markham St", lat=43.65, lng=-79.41,
                    rent=2000, monthly_cost=2000, beds=1) for n in (1, 2)]


class FakeTexter:
    ready = True

    def __init__(self):
        self.sent = []

    def send_soon(self, to, from_, body):
        self.sent.append((to, from_, body))


def _settings() -> Settings:
    return Settings(database_url=None, web_url="https://landline.example/", transport="voice",
                    twilio_phone_number=LANDLINE, demo_agent_phones=(AGENT,))


def _booked(at: datetime = SAT_2PM, key: str = "booked", slot: str = "Saturday at 2 pm") -> ViewingBooked:
    return ViewingBooked(session_id=SID, at=NOW, idem_key=f"{CID}:{key}", call_id=CID, listing_id="t:1",
                         starts_at=at, slot_text=slot)


async def _call_placed(rt: SessionRuntime) -> None:
    """A phone renter's brief ends with a plan of one listing, and its call is scheduled."""
    await rt.start(SessionStarted(session_id=SID, at=NOW, idem_key=f"{SID}:start", channel="phone",
                                  caller_phone="+14165550100"))
    await rt.emit(CallsRequested(session_id=SID, at=NOW, idem_key=f"{SID}:plan", listing_ids=["t:1"],
                                 may_book=True))
    await rt.emit(SessionEnded(session_id=SID, at=NOW, idem_key=f"{SID}:end", reason="hangup"))
    plan = CallPlan(session_id=SID, listing_ids=["t:1"], may_book=True, created_at=NOW)
    await rt.emit(CallsScheduled(session_id=SID, at=NOW, idem_key=f"{SID}:scheduled", plan=plan,
                                 call_ids=[CID]))


@pytest.fixture
async def db(schema):
    import os
    database = await Database.open(os.environ["TEST_DATABASE_URL"], schema=schema[0], max_size=4)
    async with database.transaction() as conn:
        await conn.execute("truncate users, sessions, events, calls, viewing_confirmations, listings cascade")
        for x in LISTINGS:
            await conn.execute(
                "insert into listings (listing_id, source, source_id, url, first_seen, last_seen, status, "
                "address, rent, monthly_cost, beds) values "
                "(%s, 't', %s, %s, %s, %s, 'active', %s, 2000, 2000, 1)",
                (x.listing_id, x.source_id, x.url, x.first_seen, x.last_seen, x.address))
    yield database
    await database.close()


async def _booking(db: Database, transport: str = "voice", to: str | None = AGENT):
    """A booked listing call: its row as the Dispatcher makes it, the number it dialled (A-13a's job), then
    the booking. Returns the runtime and what was texted."""
    rt, texter = SessionRuntime(Catalogue(LISTINGS, "test"), db), FakeTexter()
    ConfirmLinks(rt, _settings(), texter)
    await _call_placed(rt)
    await db.calls.create(CID, session_id=SID, role="listing", transport=transport, state="live",
                          listing_id="t:1")
    if to is not None:
        async with db.transaction() as conn:
            await conn.execute("update calls set to_e164 = %s where call_id = %s", (to, CID))
    await rt.emit(_booked())
    await rt.idle()
    return rt, texter


async def test_a_booking_texts_the_number_the_call_dialled_one_confirm_link(db):
    rt, texter = await _booking(db)
    row = await db.confirmations.for_call(CID)
    assert (row.session_id, row.listing_id, row.starts_at, row.status) == (SID, "t:1", SAT_2PM, "pending")
    assert len(row.token) >= 20
    assert texter.sent == [(AGENT, LANDLINE, "Landline: please confirm the viewing at 112 Markham St on "
                            f"Saturday, Oct 31 at 2 pm: https://landline.example/v/{row.token}")]
    card = next(c for c in rt.get(SID).board if c.listing_id == "t:1")
    assert card.viewing == CardViewing(starts_at=SAT_2PM, slot_text="Saturday at 2 pm", status="booked")
    # A second booking on the same call (or the same event again) makes no second row and no second text.
    await rt.emit(_booked(SAT_2PM.replace(hour=19), key="rebooked", slot="Saturday at 3 pm"))
    await rt.idle()
    assert (await db.confirmations.for_call(CID)).token == row.token and len(texter.sent) == 1


@pytest.mark.parametrize(("transport", "to"), [
    ("sim", AGENT),        # a judge's or a sim call never texts anyone
    ("voice", None),       # no dialled number stored yet (A-13a fills it)
    ("voice", OFF_LIST),   # a number off the allowlist is never texted (hard rule 6)
])
async def test_without_an_allowlisted_dialled_number_the_row_is_made_and_nothing_is_texted(db, transport, to):
    rt, texter = await _booking(db, transport=transport, to=to)
    assert (await db.confirmations.for_call(CID)).status == "pending" and texter.sent == []
    card = next(c for c in rt.get(SID).board if c.listing_id == "t:1")
    assert card.viewing.status == "booked"


async def test_the_card_shows_the_booked_viewing_without_a_database():
    rt = SessionRuntime(Catalogue(LISTINGS, "test"))
    await _call_placed(rt)
    assert all(c.viewing is None for c in rt.get(SID).board)
    await rt.emit(_booked())
    card = next(c for c in rt.get(SID).board if c.listing_id == "t:1")
    assert card.viewing == CardViewing(starts_at=SAT_2PM, slot_text="Saturday at 2 pm", status="booked")


def test_the_viewing_is_said_as_a_day_a_date_and_a_time_in_toronto():
    assert when(SAT_2PM) == "Saturday, Oct 31 at 2 pm"
    # After the clocks go back (Nov 1), Toronto is UTC-5.
    assert when(datetime(2026, 11, 2, 15, 30, tzinfo=UTC)) == "Monday, Nov 2 at 10:30 am"
