"""The live-call script (evals/run_live_call.py) with no network: its checks before calling, and that the
session it records goes to the phone runner and the allowlisted phone. A real call needs Twilio and ngrok."""
import asyncio
from dataclasses import replace
from datetime import UTC, datetime

from evals.run_live_call import SAMPLE, place_call, problems
from landline.config import Settings
from landline.contracts import Listing
from landline.db.catalogue import Catalogue
from landline.orchestrator import SessionRuntime
from landline.voice.dispatch import CallResult, Dispatcher
from landline.voice.routing import ByChannel

NOON = datetime(2026, 10, 24, 16, 0, tzinfo=UTC)    # Saturday 12:00 in Toronto
NIGHT = datetime(2026, 10, 25, 3, 0, tzinfo=UTC)    # Saturday 23:00 in Toronto
MINE = "+14165550123"
READY = replace(Settings(database_url=None, web_url="http://localhost:3000", transport="voice"),
                public_url="https://example.ngrok-free.dev", twilio_account_sid="AC1", twilio_auth_token="t",
                twilio_phone_number="+12892765257", boson_api_key="k", demo_agent_phones=(MINE,))


class FakeRunner:
    def __init__(self, uses_phone):
        self.uses_phone, self.transport, self.dials = uses_phone, "voice" if uses_phone else "sim", []

    async def place(self, dial, report):
        self.dials.append(dial)
        await report("ringing")
        await report("live")
        return CallResult("done")


def test_a_ready_env_has_no_problems_and_each_gap_is_named():
    assert problems(READY, NOON) == []
    said = " ".join(problems(replace(READY, transport="stub", public_url=None,
                                     demo_agent_phones=(MINE, "+14165550124")), NIGHT))
    assert "TRANSPORT" in said and "PUBLIC_URL" in said and "2 numbers" in said
    assert "FORCE_BUSINESS_HOURS" in said
    assert problems(replace(READY, force_business_hours=True), NIGHT) == []


async def test_the_session_goes_to_the_phone_runner_and_rings_the_allowlisted_phone():
    x = Listing(listing_id="t:1", source="t", source_id="1", url="https://example.com/1", first_seen=NOON,
                last_seen=NOON, address="1 Dial St", lat=43.65, lng=-79.38, rent=2000, monthly_cost=2000,
                beds=1)
    rt = SessionRuntime(Catalogue([x], "test"))
    phone, sim = FakeRunner(True), FakeRunner(False)
    Dispatcher(rt, replace(READY, force_business_hours=True), runner=ByChannel(rt, phone=phone, sim=sim))
    said = []
    state = await asyncio.wait_for(place_call(rt, READY, "t:1", SAMPLE, text=True, wait_s=5,
                                              say=said.append), 10)
    assert state.phase == "done" and state.channel == "phone" and state.caller_phone == MINE
    assert sim.dials == [] and [d.to for d in phone.dials] == [MINE]
    plan = phone.dials[0].plan
    assert plan.may_book and plan.questions == SAMPLE.questions and len(plan.windows) == 1
    assert said[0].startswith("Dialing your phone (...0123)") and said[-1].startswith("Connected")
