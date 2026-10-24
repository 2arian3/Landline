"""The mock listing call (evals/run_mock_call.py) with no network: the landlord is a script and the Caller is
a fake Higgs session, so this checks the wiring, not the Caller. The live run needs BOSON_API_KEY."""
import asyncio
import json

from evals.run_concierge import CATALOGUE_PATH
from evals.run_mock_call import run_mock
from evals.sim.scripted_call import GOODBYE, Line, MockCall, ScriptedLandlord, load_mock_calls
from landline.config import Settings
from landline.db.catalogue import Catalogue, _from_fixture
from landline.voice.personas import HANG_UP

LISTING = "livrent:145662"   # 30 Carabob Court in the frozen catalogue
AVAILABLE = "Yes, it's still available, from November 1st."
PETS = "Cats are fine, but no dogs, sorry."


class FakeCaller:
    """The Caller's Higgs session: each response.create plays the next list of events."""

    def __init__(self, turns):
        self.turns, self.sent, self.cfg = list(turns), [], None
        self.out: asyncio.Queue = asyncio.Queue()

    async def connect(self, key, cfg):
        self.cfg = cfg
        return self

    async def send(self, event):
        self.sent.append(event)
        if event.get("type") == "response.create" and self.turns:
            for e in self.turns.pop(0):
                self.out.put_nowait(e)

    async def events(self):
        while True:
            yield await self.out.get()

    async def close(self):
        pass


def _says(text):
    return [{"type": "response.created"}, {"type": "response.output_text.done", "text": text},
            {"type": "response.done", "response": {"output": [{"type": "message"}]}}]


def _records(field, value, quote, said):
    return [{"type": "response.created"}, {"type": "response.output_text.delta", "delta": said},
            {"type": "response.function_call_arguments.done", "name": "record_fact", "call_id": "fc1",
             "arguments": json.dumps({"field": field, "value": value, "quote": quote})},
            {"type": "response.output_text.done", "text": said},
            {"type": "response.done", "response": {"output": [{"type": "function_call"}]}}]


def test_the_landlord_answers_what_was_asked_then_goes_in_order_then_hangs_up():
    landlord = ScriptedLandlord([Line(say="Hello."), Line(say=AVAILABLE, when=["available"]),
                                 Line(say=PETS, when=["pet", "cat"])])
    assert landlord.next_line() == "Hello."
    landlord.heard = "Are cats allowed?"
    assert landlord.next_line() == PETS                 # matched, ahead of its turn
    landlord.heard = "Thanks, got it."
    assert landlord.next_line() == AVAILABLE            # nothing matched: the next one in order
    assert landlord.next_line() == GOODBYE and HANG_UP in GOODBYE


def test_every_mock_call_fixture_loads_and_names_a_listing_in_the_catalogue():
    ids = Catalogue(_from_fixture(CATALOGUE_PATH), "eval").by_id
    cases = load_mock_calls()
    assert cases and all(c.listing_id in ids for c in cases)


async def test_a_scripted_conversation_goes_through_the_sim_call_to_an_outcome():
    case = MockCall(id="wiring", listing_id=LISTING, name="Linda", lines=[
        "Hello, Linda speaking.", {"when": ["available"], "say": AVAILABLE}, {"when": ["cat"], "say": PETS}],
        plan={"shareable": {"pets": ["cat"]}})
    caller = FakeCaller([
        _says("Hi, I'm an AI assistant calling for a renter about 30 Carabob Court. Is it still available?"),
        _records("available_from", "November 1st", "still available, from November 1st", "Great."),
        _says("November 1st, got it. Are cats allowed? The renter has a cat."),
        # never said by the landlord, so the outcome must not have it
        _records("parking", "included", "parking is included", "Okay."),
        _says("Thanks, that's all I needed."),
        _says("Bye!"),
    ])
    settings = Settings(database_url=None, web_url="http://localhost:3000", transport="stub")
    r = await asyncio.wait_for(run_mock(case, 1, Catalogue(_from_fixture(CATALOGUE_PATH), "eval"), settings,
                                        connect=caller.connect, wait_s=10), 15)
    assert caller.cfg["session"]["tools"]                                    # the Caller got its tools
    assert [t.text for t in r.trace.turns if t.speaker == "landlord"] == [
        "Hello, Linda speaking.", AVAILABLE, PETS, GOODBYE.replace(HANG_UP, "").strip()]
    assert r.trace.end_reason == "hangup" and r.state.phase == "done"
    facts = r.state.outcomes[LISTING].facts
    assert ("available_from", "still available, from November 1st") in [(f.field, f.quote) for f in facts]
    assert "parking" not in [f.field for f in facts]
    assert r.score is None                                                   # no gold, nothing scored
