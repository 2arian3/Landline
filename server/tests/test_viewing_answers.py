"""O-08c: the listing agent confirms or declines a booked viewing. A confirm sends one Google Calendar
invite (to the agent, and to the renter when their profile has an email) and texts the renter; a decline
texts the renter. A token decides once. A fake Calendar stands in for Google: no real invite is sent. The
Postgres tests run in a throwaway schema (conftest.py) and skip without TEST_DATABASE_URL."""
from datetime import UTC, datetime

import pytest

from landline.contracts import CallOutcome, Fact, OutcomeReady
from landline.orchestrator.calendar import RENDER_URL, CalendarError
from landline.orchestrator.viewings import Answer, Decisions, heard_lines, valid_email
from tests.test_viewings import CID, LANDLINE, NOW, SAT_2PM, SID, _booking, _settings, db  # noqa: F401

RENTER = "+14165550100"
AGENT_EMAIL, RENTER_EMAIL = "agent@example.com", "renter@example.com"
CONFIRMED = "Landline: Confirmed: 112 Markham St, Saturday, Oct 31 at 2 pm."


class FakeCalendar:
    """Calendar.create_event's shape: returns an event id, or raises CalendarError like Google refusing."""

    def __init__(self, fail: bool = False):
        self.fail, self.events = fail, []

    async def create_event(self, summary, location, description, starts_at, attendees):
        if self.fail:
            raise CalendarError("event insert refused (403): insufficient permissions", 403)
        self.events.append({"summary": summary, "location": location, "description": description,
                            "starts_at": starts_at, "attendees": list(attendees)})
        return f"evt_{len(self.events)}"


async def _booked(db, *, email: str | None = RENTER_EMAIL, calendar=None):   # noqa: F811
    """A booked viewing whose call heard two facts, and the renter on file (with an email or not)."""
    rt, texter = await _booking(db)
    facts = [Fact(field="parking_cost", value="$150 a month", quote="It's 150 a month for parking.",
                  speaker="Agent heard", at=NOW),
             Fact(field="pets", value="dogs ok", quote="Dogs are fine.", speaker="Agent heard", at=NOW)]
    outcome = CallOutcome(call_id=CID, listing_id="t:1", end_reason="done", facts=facts)
    await rt.emit(OutcomeReady(session_id=SID, at=NOW, idem_key=f"{CID}:outcome", outcome=outcome))
    user = await db.users.upsert(RENTER)
    if email:
        async with db.transaction() as conn:
            await conn.execute("update users set email = %s where user_id = %s", (email, user.user_id))
    token = (await db.confirmations.for_call(CID)).token
    texter.sent.clear()   # the agent's confirm link (O-08b)
    return rt, texter, Decisions(rt, _settings(), texter, calendar), token


async def test_a_confirm_invites_the_agent_and_the_renter_and_texts_the_renter(db):   # noqa: F811
    calendar = FakeCalendar()
    rt, texter, decisions, token = await _booked(db, calendar=calendar)
    answer = await decisions.confirm(token, f"  {AGENT_EMAIL} ")
    assert answer == Answer("confirmed", "confirmed", (AGENT_EMAIL, RENTER_EMAIL), "evt_1")
    assert calendar.events == [{
        "summary": "Viewing: 112 Markham St", "location": "112 Markham St", "starts_at": SAT_2PM,
        "attendees": [AGENT_EMAIL, RENTER_EMAIL],
        "description": 'parking cost: $150 a month ("It\'s 150 a month for parking.")\n'
                       'pets: dogs ok ("Dogs are fine.")'}]
    row = await db.confirmations.get(token)
    assert (row.status, row.agent_email, row.calendar_event_id) == ("confirmed", AGENT_EMAIL, "evt_1")
    assert row.decided_at is not None
    assert texter.sent == [(RENTER, LANDLINE, f"{CONFIRMED} Invite sent to {RENTER_EMAIL}.")]


async def test_a_renter_with_no_email_gets_the_add_to_calendar_link(db):   # noqa: F811
    calendar = FakeCalendar()
    _, texter, decisions, token = await _booked(db, email=None, calendar=calendar)
    answer = await decisions.confirm(token, AGENT_EMAIL)
    assert answer.invited == (AGENT_EMAIL,) and calendar.events[0]["attendees"] == [AGENT_EMAIL]
    (to, _, text), = texter.sent
    assert to == RENTER
    assert text.startswith(f"{CONFIRMED} Add it to your calendar: {RENDER_URL}?action=TEMPLATE")


@pytest.mark.parametrize("calendar", [None, FakeCalendar(fail=True)], ids=["no keys", "Calendar refuses"])
async def test_without_an_invite_the_confirm_stands_and_the_renter_gets_the_link(db, calendar):   # noqa: F811
    _, texter, decisions, token = await _booked(db, calendar=calendar)
    answer = await decisions.confirm(token, AGENT_EMAIL)
    assert (answer.outcome, answer.event_id, answer.invited) == ("confirmed", None, ())
    row = await db.confirmations.get(token)
    assert (row.status, row.calendar_event_id) == ("confirmed", None)
    assert texter.sent[0][2].startswith(f"{CONFIRMED} Add it to your calendar: ")


async def test_a_decline_texts_the_renter(db):   # noqa: F811
    calendar = FakeCalendar()
    _, texter, decisions, token = await _booked(db, calendar=calendar)
    assert await decisions.decline(token) == Answer("declined", "declined")
    assert (await db.confirmations.get(token)).status == "declined" and calendar.events == []
    assert texter.sent == [(RENTER, LANDLINE, "Landline: 112 Markham St can't do Saturday, Oct 31 at 2 pm. "
                                              "Want me to find another time?")]


async def test_a_token_decides_once(db):   # noqa: F811
    calendar = FakeCalendar()
    _, texter, decisions, token = await _booked(db, calendar=calendar)
    await decisions.confirm(token, AGENT_EMAIL)
    sent = len(texter.sent)
    assert await decisions.decline(token) == Answer("already_decided", "confirmed")
    assert await decisions.confirm(token, AGENT_EMAIL) == Answer("already_decided", "confirmed")
    assert len(calendar.events) == 1 and len(texter.sent) == sent
    assert (await db.confirmations.get(token)).status == "confirmed"
    assert await decisions.confirm("no-such-token", AGENT_EMAIL) == Answer("not_found")


async def test_an_email_that_isnt_one_decides_nothing(db):   # noqa: F811
    _, texter, decisions, token = await _booked(db, calendar=FakeCalendar())
    assert await decisions.confirm(token, "not an email") == Answer("bad_email")
    assert (await db.confirmations.get(token)).status == "pending" and texter.sent == []


def test_emails_and_the_two_lines_are_made_in_code():
    assert valid_email(" a.b@example.co ") == "a.b@example.co"
    assert [valid_email(x) for x in ("", "a@b", "a b@example.com", None)] == [None, None, None, None]
    assert heard_lines(None) == "Booked by Landline on a call with the listing agent."
    fact = Fact(field="rent", value="$2,000", quote="", speaker="Agent heard", at=datetime.now(UTC))
    assert heard_lines(CallOutcome(call_id=CID, listing_id="t:1", end_reason="done", facts=[fact])) == \
        "Booked by Landline on a call with the listing agent."   # a fact without its quote is never shown
