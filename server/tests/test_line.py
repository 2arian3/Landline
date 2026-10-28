"""The Line (voice/line.py): map taps and background board lines reach the live Concierge only between
turns, coalesced, quoted as data. Unit tests on its own, then a tap on /try's board reaching a browser
call."""
import asyncio
import time
from datetime import UTC, datetime
from types import SimpleNamespace

from fastapi.testclient import TestClient

from landline.app import create_app
from landline.config import Settings
from landline.contracts import (
    AnchorResolved,
    Listing,
    ListingFocused,
    PreferenceDelta,
    PreferencesUpdated,
    SessionStarted,
)
from landline.db.catalogue import Catalogue
from landline.orchestrator import SessionRuntime
from landline.voice import bridge
from landline.voice.line import BOARD, LOOKING, STALE_S, Item, Line, Lines, describe

T0 = datetime(2026, 10, 24, tzinfo=UTC)
CREATED = {"type": "response.created"}
DONE = {"type": "response.done", "response": {"output": [{"type": "message"}]}}
TOOL_DONE = {"type": "response.done", "response": {"output": [{"type": "function_call"}]}}


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


def _line():
    sent, clock = [], Clock()

    async def send(event):
        sent.append(event)
    return Line(send, clock), sent, clock


def _texts(sent):
    return [e["item"]["content"][0]["text"] for e in sent if e["type"] == "conversation.item.create"]


def _taps(sent):
    """The tap notes alone: a call through the bridge also opens with the connected note (#129)."""
    return [t for t in _texts(sent) if t.startswith(LOOKING)]


def _tap(clock, n, key=None):
    return Item("focus", f"{LOOKING} {n} Test St", clock(), key=key or f"t:{n}")


async def test_a_tap_while_the_agent_speaks_waits_for_response_done():
    line, sent, clock = _line()
    await line.seen(CREATED)
    await line.seen({"type": "response.output_audio.delta", "delta": "AAAA"})
    await line.push(_tap(clock, 1))
    assert sent == []                                   # never in the middle of a response
    await line.seen(DONE)
    tap = {"type": "input_text", "text": f"{LOOKING} 1 Test St"}
    assert sent == [{"type": "conversation.item.create",
                     "item": {"type": "message", "role": "user", "content": [tap]}},
                    {"type": "response.create"}]


async def test_a_response_that_called_a_tool_is_not_the_end_of_the_turn():
    line, sent, clock = _line()
    await line.seen(CREATED)
    await line.push(_tap(clock, 1))
    await line.seen(TOOL_DONE)                          # run_tool asks for the next response
    await line.seen(CREATED)
    assert sent == []
    await line.seen(DONE)
    assert _texts(sent) == [f"{LOOKING} 1 Test St"]


async def test_three_taps_in_one_turn_make_one_delivery_of_the_last():
    line, sent, clock = _line()
    await line.seen(CREATED)
    for n in (1, 2, 3):
        await line.push(_tap(clock, n))
    await line.seen(DONE)
    assert _texts(sent) == [f"{LOOKING} 3 Test St"] and sent[-1] == {"type": "response.create"}


async def test_never_while_the_renter_speaks_or_a_response_is_about_to_start():
    line, sent, clock = _line()
    await line.seen({"type": "input_audio_buffer.speech_started"})
    await line.push(_tap(clock, 1))
    await line.seen({"type": "input_audio_buffer.speech_stopped"})
    await line.push(_tap(clock, 2))
    assert sent == []                                   # the server is about to answer the renter
    await line.seen(CREATED)
    await line.seen(DONE)
    assert _texts(sent) == [f"{LOOKING} 2 Test St"]


async def test_a_tap_on_a_quiet_line_goes_in_at_once_and_only_once():
    line, sent, clock = _line()
    await line.push(_tap(clock, 1))
    await line.push(_tap(clock, 2))                     # our own response hasn't started yet: it waits
    assert _texts(sent) == [f"{LOOKING} 1 Test St"]
    await line.seen(CREATED)
    await line.seen(DONE)
    assert _texts(sent) == [f"{LOOKING} 1 Test St", f"{LOOKING} 2 Test St"]


async def test_a_renter_who_stopped_without_an_answer_does_not_block_the_line_for_good():
    line, sent, clock = _line()
    await line.seen({"type": "input_audio_buffer.speech_stopped"})   # noise: no response follows
    clock.now += 3
    await line.push(_tap(clock, 1))
    assert _texts(sent) == [f"{LOOKING} 1 Test St"]


async def test_stale_taps_repeats_and_a_closed_line_are_dropped():
    line, sent, clock = _line()
    await line.seen(CREATED)
    await line.push(_tap(clock, 1))
    clock.now += STALE_S + 1
    await line.seen(DONE)
    assert sent == []                                   # tapped too long ago
    await line.push(_tap(clock, 2))
    await line.seen(CREATED)
    await line.seen(DONE)
    await line.push(_tap(clock, 2))                     # already told
    assert _texts(sent) == [f"{LOOKING} 2 Test St"]
    line.close()
    await line.push(_tap(clock, 3))
    assert _texts(sent) == [f"{LOOKING} 2 Test St"]


async def test_a_board_line_and_a_tap_in_one_turn_go_in_together():
    line, sent, clock = _line()
    await line.seen(CREATED)
    await line.push(Item("board", "[the board changed] Closest to work first.", clock()))
    clock.now += 1
    await line.push(_tap(clock, 1))
    await line.seen(DONE)
    assert _texts(sent) == [f"[the board changed] Closest to work first.\n{LOOKING} 1 Test St"]


async def test_a_board_line_from_a_tool_round_is_in_its_reply_and_never_answered_again():
    """#164: update_preferences placed a street in its own commit, so its board line came while the reply was
    coming, and went in after it with a response.create of its own: two replies to one renter line."""
    line, sent, clock = _line()
    await line.seen(CREATED)
    await line.seen({"type": "response.function_call_arguments.done", "name": "update_preferences"})
    await line.push(Item("board", "[the board changed] Closest to Christie Street first.", clock()))
    await line.seen(TOOL_DONE)
    await line.seen(CREATED)                            # the tool's reply, which says the board line
    await line.seen(DONE)
    assert sent == []                                   # one reply, nothing answered twice
    # A re-rank after it (a place the geocoder found later) still goes in, once.
    later = Item("board", "[the board changed] Closest to King and Bay first.", clock())
    await line.push(later)
    assert _texts(sent) == [later.text]
    await line.seen(CREATED)
    await line.seen(DONE)
    await line.push(Item("board", later.text, clock()))   # the same line again: already told
    assert _texts(sent) == [later.text]


def _listing(i, **kw):
    return Listing(listing_id=f"t:{i}", source="t", source_id=str(i), url=f"https://example.com/{i}",
                   first_seen=T0, last_seen=T0, address=f"{i} Test St", lat=43.65, lng=-79.38,
                   rent=2400, monthly_cost=2400 + i, beds=1, **kw)


async def test_lines_route_ui_taps_by_session_with_the_board_address_and_numbers():
    rt = SessionRuntime(Catalogue([_listing(1, floor=14, sqft=640), _listing(2)], "test"))
    lines = Lines(rt)
    sid = "s_line000001"
    await rt.start(SessionStarted(session_id=sid, at=T0, idem_key=f"{sid}:start", channel="browser"))
    sent = []

    async def send(event):
        sent.append(event)
    line = lines.open(sid, send)
    await rt.emit(ListingFocused(session_id=sid, at=T0, idem_key="tap1", listing_id="t:1", origin="ui"))
    await rt.idle()
    assert _texts(sent) == [f"{LOOKING} 1 Test St: 1 bed, $2,401 a month, floor 14, 640 sq ft"]
    await line.seen(CREATED)
    await line.seen(DONE)
    # The Concierge's own focus is said in its tool reply: it isn't queued, only remembered.
    await rt.emit(ListingFocused(session_id=sid, at=T0, idem_key="voice2", listing_id="t:2", origin="voice"))
    await rt.emit(ListingFocused(session_id=sid, at=T0, idem_key="tap2", listing_id="t:2", origin="ui"))
    await rt.idle()
    assert len(_texts(sent)) == 1
    lines.close(sid, line)
    await rt.emit(ListingFocused(session_id=sid, at=T0, idem_key="tap3", listing_id="t:1", origin="ui"))
    await rt.emit(AnchorResolved(session_id=sid, at=T0, idem_key="anchor", label="work", lat=43.6, lng=-79.4))
    await rt.idle()
    assert len(_texts(sent)) == 1 and sid not in lines.live   # the call is over


def test_describe_says_studio_and_unit():
    card = SimpleNamespace(address="1 Test St", unit_label="Unit 1405", beds=0, monthly_cost=1900, floor=None,
                           sqft=None, neighbourhood=None)
    assert describe(card) == "1 Test St, Unit 1405: studio, $1,900 a month"


# Through the app: a tap on the board during a browser call (/try)

class FakeHiggs:
    """Higgs that says nothing and stays open, keeping what the bridge sends it."""

    def __init__(self):
        self.sent, self.session, self.closed = [], None, False

    async def connect(self, api_key, session):
        self.session, self._done = session, asyncio.Event()
        return self

    async def send(self, event):
        self.sent.append(event)

    async def events(self):
        await self._done.wait()
        return
        yield

    async def close(self):
        self.closed = True
        self._done.set()


def test_a_tap_on_the_board_reaches_a_browser_call():
    settings = Settings(database_url=None, web_url="http://localhost:3000", transport="stub",
                        boson_api_key="k", judge_audio_ready=True)
    app = create_app(settings, Catalogue([_listing(1), _listing(2), _listing(3)], "test"))
    fake = FakeHiggs()
    with TestClient(app) as c:
        c.app.state.connect_higgs = fake.connect
        start = c.post("/api/judge/start").json()
        sid = start["session_id"]
        with c.websocket_connect(start["ws_url"]) as ws:
            deadline = time.monotonic() + 5
            while sid not in c.app.state.lines.live and time.monotonic() < deadline:
                time.sleep(0.01)
            assert c.post(f"/api/sessions/{sid}/focus", json={"listing_id": "t:2"}).status_code == 200
            while not _taps(fake.sent) and time.monotonic() < deadline:
                time.sleep(0.01)
            ws.close(1000)
        assert _taps(fake.sent) == [f"{LOOKING} 2 Test St: 1 bed, $2,402 a month"]
        assert fake.sent[-1] == {"type": "response.create"}


# U-12d: a call that starts from the renter's saved search

SAVED = "I've got your saved search: 1 bedroom under $2,500. Want to start there?"


async def _saved_search_session(rt, sid):
    await rt.start(SessionStarted(session_id=sid, at=T0, idem_key=f"{sid}:start", channel="phone",
                                  caller_phone="+14165550100"))
    await rt.emit(PreferencesUpdated(session_id=sid, at=T0, idem_key=f"{sid}:saved-search",
                                     delta=PreferenceDelta(max_monthly=2500, min_beds=1)))


async def test_a_saved_search_that_lands_after_the_greeting_reaches_the_concierge_once():
    rt = SessionRuntime(Catalogue([_listing(1), _listing(2)], "test"))
    lines = Lines(rt)
    sid = "s_line000002"
    sent = []

    async def send(event):
        sent.append(event)
    await rt.start(SessionStarted(session_id=sid, at=T0, idem_key=f"{sid}:start", channel="phone",
                                  caller_phone="+14165550100"))
    lines.open(sid, send)
    # A preference the Concierge set through its tool is said in the tool's reply: nothing queued.
    await rt.emit(PreferencesUpdated(session_id=sid, at=T0, idem_key="tool1",
                                     delta=PreferenceDelta(min_beds=1)))
    await rt.idle()
    assert _texts(sent) == []
    await rt.emit(PreferencesUpdated(session_id=sid, at=T0, idem_key=f"{sid}:saved-search",
                                     delta=PreferenceDelta(max_monthly=2500)))
    await rt.idle()
    assert _texts(sent) == [f"{BOARD} {SAVED}"]


async def test_a_saved_search_already_on_the_map_goes_in_the_greeting_and_not_again():
    rt = SessionRuntime(Catalogue([_listing(1), _listing(2)], "test"))
    sid = "s_line000003"
    await _saved_search_session(rt, sid)
    saved = rt.state(sid).saved_search
    assert saved == SAVED and rt.get(sid).agent_says == SAVED
    _, start = bridge.opening()
    note, create = bridge.with_saved_search(start, saved)
    text = note["item"]["content"][0]["text"]
    assert text == f"[The call just connected.] [their saved search is on the map] {SAVED}"
    assert create == {"type": "response.create"}   # no response object (#129)
    line, sent, _ = _line()
    line.board_told = f"{BOARD} {saved}"           # what the bridge marks once the greeting carries it
    await line.push(Item("board", f"{BOARD} {saved}", 100.0))
    assert sent == []
