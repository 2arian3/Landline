"""Read-side contract tests (U-05).

Each read endpoint answers in its contract's shape, the web app calls only the
endpoints docs/CONTRACTS.md lists (plus the demo hooks), and a call's captions
(/api/calls/{id}/turns) come from the demo store's script or, for any other
call, from call_turns. The database test skips without TEST_DATABASE_URL, as in
test_db.py.
"""
import os
import re
import secrets
import time
from datetime import UTC, datetime

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg import sql
from psycopg.conninfo import make_conninfo
from pydantic import TypeAdapter

from landline.api.demo import DemoSessions
from landline.api.routes import JudgeStart, Turn
from landline.app import create_app
from landline.config import ROOT, Settings
from landline.contracts import CataloguePoint, Listing, SessionView
from landline.db.catalogue import Catalogue
from landline.db.migrations import apply
from landline.db.rows import TurnRow

NOW = datetime(2026, 10, 22, tzinfo=UTC)
URL = os.environ.get("TEST_DATABASE_URL")
DISCLOSURE = "I'm an AI assistant calling for a renter"   # AGENTS.md rule 6: how every outbound call opens
TURN_KEYS = {"idx", "speaker", "text", "lang", "t_ms"}

# The web app's stand-ins for the voice agent (DemoControls, ?dev=1). Not in CONTRACTS.md's table.
DEMO_HOOKS = ("/api/sessions/{id}/preferences", "/api/sessions/{id}/calls", "/api/sessions/{id}/hang-up",
              "/api/sessions/{id}/reset", "/api/sessions/{id}/returning")
# Endpoints from open PRs that the table doesn't list yet: the area hook (#15, U-03), the judge's Done
# (#18, U-06) and the judge's line hang-up (#26, U-06). The web app may call them whichever PR merges first;
# they leave here once the table has them.
IN_FLIGHT = ("/api/sessions/{id}/area", "/api/judge/{session_id}/done", "/api/judge/{session_id}/hang-up")
WEB_SOURCES = ("web/app", "web/components", "web/lib")
API_PATH = re.compile(r"""(["'`])(/api/[^"'`]*)\1""")   # a quoted or template-literal /api path


def _listing(i: int, rent: int, beds: float) -> Listing:
    return Listing(listing_id=f"t:{i}", source="t", source_id=str(i), url=f"https://example.com/{i}",
                   first_seen=NOW, last_seen=NOW, address=f"{i} Test St", lat=43.65, lng=-79.38,
                   rent=rent, monthly_cost=rent, beds=beds, sqft=700)


def _one_bedrooms() -> Catalogue:
    """Five places that all fit the default $2,800 budget: four to call and one left pending."""
    return Catalogue([_listing(i, rent, 1) for i, rent in enumerate((2000, 2100, 2200, 2300, 2400), start=1)],
                     "test")


def _settings(**kw) -> Settings:
    return Settings(**{"database_url": None, "web_url": "http://localhost:3000", "transport": "stub", **kw})


def _client(**kw) -> TestClient:
    return TestClient(create_app(_settings(**kw), _one_bedrooms()))


def _until_done(c: TestClient, session_id: str) -> SessionView:
    deadline = time.monotonic() + 3
    view = SessionView.model_validate(c.get(f"/api/sessions/{session_id}").json())
    while view.phase != "done" and time.monotonic() < deadline:
        time.sleep(0.02)
        view = SessionView.model_validate(c.get(f"/api/sessions/{session_id}").json())
    assert view.phase == "done"
    return view


async def _walk(s: DemoSessions, session_id: str = "s1") -> SessionView:
    """Plan the top four, hang up, and wait for every scripted call to end."""
    await s.plan_top(session_id)
    await s.hang_up(session_id)
    await s.dialing[session_id]
    return s.get(session_id)


# Each read endpoint answers in its contract's shape


def test_read_endpoints_answer_in_their_contract_shapes():
    with _client() as c:
        c.app.state.sessions.tick_s = 0
        assert {"status", "listings", "sessions", "transport", "higgs"} <= c.get("/health").json().keys()
        points = TypeAdapter(list[CataloguePoint]).validate_python(c.get("/api/catalogue").json())
        assert {p.listing_id for p in points} == {f"t:{i}" for i in range(1, 6)}
        assert Listing.model_validate(c.get("/api/listings/t:1").json()).listing_id == "t:1"
        assert SessionView.model_validate(c.get("/api/sessions/s_read").json()).session_id == "s_read"

        c.post("/api/sessions/s_read/calls")
        calls = SessionView.model_validate(c.post("/api/sessions/s_read/hang-up").json()).calls
        _until_done(c, "s_read")
        bodies = [c.get(f"/api/calls/{call.call_id}/turns").json() for call in calls]
        turns = [TypeAdapter(list[Turn]).validate_python(body) for body in bodies]
        assert [len(t) for t in turns] == [6, 3, 7, 0]
        assert all(set(line) == TURN_KEYS for body in bodies for line in body)


def test_judge_start_answers_in_its_contract_shape():
    with _client(judge_audio_ready=True) as c:
        body = c.post("/api/judge/start").json()
        assert set(body) == {"session_id", "ws_url"}
        JudgeStart.model_validate(body)


# The web app calls only documented endpoints


def _documented() -> list[tuple[str, str]]:
    """The (method, path) rows of docs/CONTRACTS.md's Web API table."""
    text = (ROOT / "docs" / "CONTRACTS.md").read_text()
    table = text.split("## Web API", 1)[1].split("\n## ", 1)[0]
    rows = re.findall(r"^\| (GET|POST) \| `([^`]+)` \|", table, flags=re.M)
    assert rows, "no Web API table in docs/CONTRACTS.md"
    return rows


def _pattern(path: str) -> re.Pattern:
    """A documented path as a pattern: each {placeholder} takes one whole segment."""
    return re.compile("[^/]+".join(re.escape(part) for part in re.split(r"\{\w+\}", path)) + r"\Z")


def _expand(url: str, source: str) -> set[str]:
    """A path as the web app builds it, with each dynamic part written as {}. A bare `${name}` becomes each
    `name: "..."` value set in the same file, so DemoControls' `${path}` is checked hook by hook."""
    for name in re.findall(r"\$\{(\w+)\}", url):
        if values := re.findall(rf"""\b{name}:\s*["']([^"']*)["']""", source):
            return {p for v in values for p in _expand(url.replace("${" + name + "}", v), source)}
    return {re.sub(r"\$\{[^}]*\}", "{}", url)}


def _web_paths() -> dict[str, set[str]]:
    """Every /api path in the web app's code (comments skipped, query dropped), with the files using it."""
    found: dict[str, set[str]] = {}
    files = sorted(p for d in WEB_SOURCES for p in (ROOT / d).rglob("*") if p.suffix in (".ts", ".tsx"))
    for f in files:
        source = f.read_text()
        code = "\n".join(line for line in source.splitlines()
                         if not line.lstrip().startswith(("//", "/*", "*")))
        for _, url in API_PATH.findall(code):
            for path in _expand(url.split("?", 1)[0], source):
                found.setdefault(path, set()).add(str(f.relative_to(ROOT)))
    return found


def test_the_web_app_calls_only_documented_endpoints():
    allowed = [_pattern(path) for _, path in _documented()] + [_pattern(p) for p in DEMO_HOOKS + IN_FLIGHT]
    found = _web_paths()
    assert {"/api/sessions/{}", "/api/catalogue"} <= found.keys()   # the scan sees the live board's calls
    undocumented = {path: files for path, files in found.items() if not any(a.match(path) for a in allowed)}
    assert not undocumented, f"the web app calls endpoints docs/CONTRACTS.md doesn't list: {undocumented}"


def test_the_scan_reads_template_paths():
    source = 'const steps = [{ path: "calls" }, { path: "hang-up" }];'
    assert _expand("/api/sessions/${encodeURIComponent(sessionId)}/${path}", source) == {
        "/api/sessions/{}/calls", "/api/sessions/{}/hang-up"}
    assert _expand("/api/calls/${id}/turns", source) == {"/api/calls/{}/turns"}
    assert _pattern("/api/calls/{call_id}/turns").match("/api/calls/{}/turns")
    assert not _pattern("/api/sessions/{id}/focus").match("/api/sessions/{}/{}")


def test_the_api_serves_every_documented_endpoint():
    shape = re.compile(r"\{\w+\}")
    paths = create_app(_settings(), _one_bedrooms()).openapi()["paths"]
    served = {(method.upper(), shape.sub("{}", path)) for path, ops in paths.items() for method in ops}
    hooks = [("POST", path) for path in DEMO_HOOKS]
    assert {(method, shape.sub("{}", path)) for method, path in [*_documented(), *hooks]} <= served


# Captions of the demo's scripted calls


async def test_demo_captions_wait_for_a_call_to_go_live():
    s = DemoSessions(_one_bedrooms(), tick_s=0)
    assert s.turns("c_unknown000") is None
    await s.plan_top("s1")
    queued = await s.hang_up("s1")
    assert [s.turns(c.call_id) for c in queued.calls] == [[], [], [], []]
    await s.dialing["s1"]


async def test_demo_captions_are_the_whole_script_once_the_calls_end():
    s = DemoSessions(_one_bedrooms(), tick_s=0)
    done = await _walk(s)
    address = {c.listing_id: c.address for c in done.board}

    def opener(lid: str) -> tuple[str, str]:
        return "agent", f"Hi, {DISCLOSURE} about the place at {address[lid]}. Is it still available?"

    verified, suspect, booked, no_answer = done.plan
    said = [[(t.speaker, t.text) for t in s.turns(c.call_id)] for c in done.calls]
    assert said == [
        [opener(verified),
         ("landlord", "Yes, it is."),
         ("agent", "What does parking cost?"),
         ("landlord", "Parking's a hundred and eighty a month. It comes with the unit, it's not optional."),
         ("landlord", "Would they sign a 12-month lease?"),
         ("agent", "I'll check with my client and get back to you.")],
        [opener(suspect),
         ("landlord", "I'll need first and last by e-transfer before I can show it to you."),
         ("agent", "Thanks. I'll pass that on.")],
        [opener(booked),
         ("landlord", "Yes."),
         ("agent", "Are pets okay? My client has a dog."),
         ("landlord", "Dogs are fine. There are a couple in the building already."),
         ("agent", "Could they see it Saturday afternoon?"),
         ("landlord", "Saturday at two works."),
         ("agent", "Saturday at 2 pm, then. I'll let my client know.")],
        [],   # nobody picked up
    ]
    for call in done.calls:
        lines = s.turns(call.call_id)
        assert [(t.idx, t.lang, t.t_ms) for t in lines] == [(i, "en", 0) for i in range(len(lines))]
    first = done.calls[0].call_id
    assert [t.idx for t in s.turns(first, after=2)] == [3, 4, 5]
    assert s.turns(first, after=5) == []


async def test_demo_calls_open_with_the_disclosure_and_every_quote_was_said():
    s = DemoSessions(_one_bedrooms(), tick_s=0)
    done = await _walk(s)
    call_of = {c.listing_id: c.call_id for c in done.calls}

    def landlord(lid: str) -> list[str]:
        return [t.text for t in s.turns(call_of[lid]) if t.speaker == "landlord"]

    for call in done.calls:
        if lines := s.turns(call.call_id):
            assert lines[0].speaker == "agent" and DISCLOSURE in lines[0].text
    quotes = [(card.listing_id, fact.quote) for card in done.board for fact in card.facts]
    assert len(quotes) == 3   # parking, the e-transfer deposit, the dogs
    for lid, quote in quotes:
        assert any(quote in line for line in landlord(lid)), quote
    # The fee behind the card's price and the question in the results were said word for word too.
    for lid, out in s.runs["s1"].outcomes.items():
        for said in [*out.questions, *(f.quote for f in out.fees if f.quote)]:
            assert any(said in line for line in landlord(lid)), said
    cards = {c.listing_id: c for c in done.board}
    assert cards[done.plan[3]].status == "no_answer" and s.turns(call_of[done.plan[3]]) == []


async def test_demo_captions_come_line_by_line_while_a_call_is_live():
    now = [100.0]
    s = DemoSessions(_one_bedrooms(), tick_s=0.01, clock=lambda: now[0])   # a line every 0.005 s
    seen, swept = [], []
    publish = s.update

    async def spy(session_id, **kw):
        view = await publish(session_id, **kw)
        states = tuple(c.state for c in view.calls)
        if states == ("live", "live", "live", "ringing"):   # the answered calls went live at 100.0
            booked = view.calls[2].call_id                  # seven lines
            for dt in (0, 0.004, 0.006, 0.0149, 0.016, 0.031):
                now[0] = 100.0 + dt
                swept.append(len(s.turns(booked)))
            now[0] = 100.0
        seen.append((states, tuple(len(s.turns(c.call_id)) for c in view.calls)))
        return view

    s.update = spy
    await _walk(s)
    assert swept == [1, 1, 2, 3, 4, 7]
    # Nothing before a call is live, its first line once it is, every line once it has ended.
    assert seen == [
        ((), ()),
        (("queued",) * 4, (0, 0, 0, 0)),
        (("dialing",) * 4, (0, 0, 0, 0)),
        (("ringing",) * 4, (0, 0, 0, 0)),
        (("live", "live", "live", "ringing"), (1, 1, 1, 0)),
        (("ended", "live", "live", "ringing"), (6, 1, 1, 0)),
        (("ended", "ended", "live", "ringing"), (6, 3, 1, 0)),
        (("ended", "ended", "ended", "ringing"), (6, 3, 7, 0)),
        (("ended",) * 4, (6, 3, 7, 0)),
        (("ended",) * 4, (6, 3, 7, 0)),
    ]


# The endpoint: demo calls, then the database, then 404


def test_turns_endpoint_serves_demo_captions_and_404s_an_unknown_call():
    with _client() as c:
        c.app.state.sessions.tick_s = 0
        missing = c.get("/api/calls/c_unknown000/turns")
        assert (missing.status_code, missing.json()) == (404, {"detail": "unknown call"})
        c.post("/api/sessions/s_cap/calls")
        first = c.post("/api/sessions/s_cap/hang-up").json()["calls"][0]["call_id"]
        _until_done(c, "s_cap")
        lines = c.get(f"/api/calls/{first}/turns").json()
        assert [t["idx"] for t in lines] == [0, 1, 2, 3, 4, 5]
        assert lines[1] == {"idx": 1, "speaker": "landlord", "text": "Yes, it is.", "lang": "en", "t_ms": 0}
        assert [t["idx"] for t in c.get(f"/api/calls/{first}/turns", params={"after": 3}).json()] == [4, 5]
        assert c.get(f"/api/calls/{first}/turns", params={"after": "x"}).status_code == 422
        c.post("/api/sessions/s_cap/reset")
        assert c.get(f"/api/calls/{first}/turns").status_code == 404   # a reset forgets the calls


class _Turns:
    def __init__(self, rows: list[TurnRow]):
        self.rows = rows

    async def list(self, call_id: str, after_idx: int = -1) -> list[TurnRow]:
        return [r for r in self.rows if r.call_id == call_id and r.idx > after_idx]


class _Calls:
    def __init__(self, call_ids: set[str]):
        self.call_ids = call_ids

    async def get(self, call_id: str) -> str | None:
        return call_id if call_id in self.call_ids else None


class _Db:
    """The two repositories of landline.db.pool.Database that the turns endpoint reads."""

    def __init__(self, rows: list[TurnRow], call_ids: set[str]):
        self.turns, self.calls = _Turns(rows), _Calls(call_ids)

    async def close(self) -> None:
        pass


def test_turns_endpoint_reads_the_database_for_calls_the_demo_did_not_dial():
    with _client() as c:
        c.app.state.db = _Db([TurnRow("c_live000001", 0, "agent", "Hi.", "en", 0),
                              TurnRow("c_live000001", 1, "landlord", "Hello?", None, None)],
                             {"c_live000001", "c_queued0001"})
        assert c.get("/api/calls/c_live000001/turns").json() == [
            {"idx": 0, "speaker": "agent", "text": "Hi.", "lang": "en", "t_ms": 0},
            {"idx": 1, "speaker": "landlord", "text": "Hello?", "lang": None, "t_ms": None}]
        assert [t["idx"] for t in c.get("/api/calls/c_live000001/turns?after=0").json()] == [1]
        assert c.get("/api/calls/c_queued0001/turns").json() == []   # a known call with nothing said yet
        assert c.get("/api/calls/c_unknown000/turns").status_code == 404
        # A demo call still answers from its script while there is a database.
        c.app.state.sessions.tick_s = 0
        c.post("/api/sessions/s_both/calls")
        first = c.post("/api/sessions/s_both/hang-up").json()["calls"][0]["call_id"]
        _until_done(c, "s_both")
        assert len(c.get(f"/api/calls/{first}/turns").json()) == 6


@pytest.fixture(scope="module")
def db_url():
    """A throwaway schema with the migrations applied (as test_db.py makes one), as a conninfo whose
    connections use it."""
    if not URL:
        pytest.skip("TEST_DATABASE_URL is not set")
    try:
        conn = psycopg.connect(URL, autocommit=True, connect_timeout=3)
    except psycopg.OperationalError as e:
        pytest.skip(f"no database at TEST_DATABASE_URL: {e}")
    name = f"t_{secrets.token_hex(4)}"
    with conn:
        conn.execute("create extension if not exists postgis with schema public")
        conn.execute("create extension if not exists vector with schema public")
        conn.execute(sql.SQL("create schema {}").format(sql.Identifier(name)))
        try:
            apply(conn, schema=name)
            yield make_conninfo(URL, options=f"-c search_path={name},public")
        finally:
            conn.execute(sql.SQL("drop schema {} cascade").format(sql.Identifier(name)))


def test_turns_endpoint_reads_call_turns_from_postgres(db_url):
    with psycopg.connect(db_url, autocommit=True) as conn:
        conn.execute("insert into sessions (session_id, channel) values ('s_turnsapi01', 'phone')")
        for call_id, state in (("c_turnsapi01", "live"), ("c_turnsapi02", "queued")):
            conn.execute("insert into calls (call_id, session_id, role, transport, state) "
                         "values (%s, 's_turnsapi01', 'listing', 'sim', %s)", (call_id, state))
        for row in (("c_turnsapi01", 0, "agent", f"Hi, {DISCLOSURE}.", "en", 0),
                    ("c_turnsapi01", 1, "landlord", "Parking is one eighty a month.", None, 4200)):
            conn.execute("insert into call_turns (call_id, idx, speaker, text, lang, t_ms) "
                         "values (%s, %s, %s, %s, %s, %s)", row)
    with TestClient(create_app(_settings(database_url=db_url), _one_bedrooms())) as c:
        assert c.get("/api/calls/c_turnsapi01/turns").json() == [
            {"idx": 0, "speaker": "agent", "text": f"Hi, {DISCLOSURE}.", "lang": "en", "t_ms": 0},
            {"idx": 1, "speaker": "landlord", "text": "Parking is one eighty a month.", "lang": None,
             "t_ms": 4200}]
        assert [t["idx"] for t in c.get("/api/calls/c_turnsapi01/turns?after=0").json()] == [1]
        assert c.get("/api/calls/c_turnsapi02/turns").json() == []
        assert c.get("/api/calls/c_unknown000/turns").status_code == 404
