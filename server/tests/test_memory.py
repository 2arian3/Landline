"""Shared listing memory (O-09a): each listing call's facts go to SurrealDB Agent Memory after OutcomeReady.
O-09b: they're read back, as known facts from the last week, when the next call is dialled.

Against a fake Agent Memory context (httpx.MockTransport). Nothing leaves the machine.
"""
import asyncio
import json
import logging
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest

from landline.agents.auditor import CallRecord, ListingContext, ToolFact, Turn, build
from landline.config import Settings
from landline.contracts import (
    Anchor,
    CallOutcome,
    CallPlan,
    CallsRequested,
    CallsScheduled,
    Fact,
    Listing,
    OutcomeReady,
    Preferences,
    PreferencesUpdated,
    SessionEnded,
    SessionStarted,
)
from landline.contracts.tools import PreferenceDelta
from landline.db.catalogue import Catalogue
from landline.orchestrator import SessionRuntime
from landline.orchestrator.memory import (
    RECALL_S,
    TIMEOUT_S,
    Known,
    Memory,
    MemoryUnavailable,
    backfill,
    from_settings,
    install,
    listing_knowledge,
    listing_scope,
    recall,
    recent_outcomes,
    renter_scope,
    renter_texts,
)
from landline.orchestrator.state import Said
from scripts.memory_backfill import SKIP as BACKFILL_SKIP
from scripts.memory_backfill import run as run_backfill

URL, CTX, KEY = "https://ctx-123.aws-use1.surreal.cloud", "landline_demo", "sp-test-secret-key"
BASE = f"/api/v1/{CTX}"
T0 = datetime(2026, 10, 24, 2, 0, tzinfo=UTC)   # Friday 23 October, 22:00 in Toronto
SID = "s_memory00001"
CALLS = ["c_memory00001", "c_memory00002"]
PARKING = ('On 2026-10-23, the listing agent said parking_cost: $150 a month '
           '("It\'s 150 a month for parking.").')


class FakeMemory:
    """A fake Agent Memory context. It registers scope paths (409 for one it has), keeps each fact with its
    scopes, and answers a query with the facts inside its lens. `fail` maps a route to a status to answer,
    an exception to raise, or "slow"."""

    def __init__(self, fail: dict[str, object] | None = None):
        self.fail = fail or {}
        self.requests: list[httpx.Request] = []
        self.scopes: list[str] = []
        self.facts: list[dict] = []
        self.inferred: list[dict] = []   # hits the server made up from what it was told (entity, attribute)
        self.now = T0   # the server's own time for a record it stores, which it returns as occurredAt

    def sent(self, route: str) -> list[dict]:
        return [json.loads(r.content) for r in self.requests if r.url.path == BASE + route]

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        assert request.headers["authorization"] == f"Bearer {KEY}"
        route = request.url.path.removeprefix(BASE)
        failure = self.fail.get(route)
        if failure == "slow":
            await asyncio.sleep(5)
        elif isinstance(failure, float):   # answers after this many seconds (the live server's ~6 s reads)
            await asyncio.sleep(failure)
        elif isinstance(failure, Exception):
            raise failure
        elif isinstance(failure, int):
            return httpx.Response(failure, json={"message": "upstream unavailable"})
        body = json.loads(request.content)
        if route == "/scopes":
            if body["path"] in self.scopes:
                return httpx.Response(409, json={"message": "scope already exists"})
            self.scopes.append(body["path"])
            return httpx.Response(201, json={"path": body["path"], "createdAt": "2026-10-24T02:00:00Z"})
        if route == "/facts":
            for path in (p for clause in body["scopes"] for p in clause):
                assert path in self.scopes, f"{path} written before it was registered"
            if "observed_at" in body:   # the live server's answer to a literal write with a time
                refused = "observed_at is only supported for fact-producing writes (infer = full or triples)"
                return httpx.Response(400, json={"message": refused})
            self.facts.append({**body, "occurredAt": self.now.isoformat()})
            n = len(self.facts)
            return httpx.Response(200, json={"chunkId": f"memory_chunk:{n}", "sessionId": "sess_1",
                                             "turnId": f"turn_{n}", "mode": "none"})
        if route == "/query":
            lens = {p for clause in body["lens"] for p in clause}
            hits = [{"id": f"memory_chunk:{i}", "score": 0.91, "source": "memory_chunk", "text": f["text"],
                     "occurredAt": f["occurredAt"]}
                    for i, f in enumerate(self.facts, 1) if lens & {p for c in f["scopes"] for p in c}]
            hits += self.inferred
            return httpx.Response(200, json={"tier": "hybrid", "hits": hits, "queryMs": 14,
                                             "trace": {"traceId": "trace_1"}})
        return httpx.Response(404, json={"message": "no such route"})


def _settings(**keys) -> Settings:
    keys = {"spectron_url": URL, "spectron_context_id": CTX, "spectron_api_key": KEY} | keys
    return Settings(database_url=None, web_url="http://localhost:3000", transport="stub", **keys)


def _client(fake: FakeMemory) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(fake))


def _listing(i: int) -> Listing:
    return Listing(listing_id=f"t:{i}", source="t", source_id=str(i), url=f"https://example.com/{i}",
                   first_seen=T0, last_seen=T0, address=f"{i} Test St", lat=43.65, lng=-79.38,
                   rent=2000 + i, monthly_cost=2000 + i, beds=1, sqft=700)


@pytest.fixture
async def rt():
    runtime = SessionRuntime(Catalogue([_listing(1), _listing(2)], "test"))
    yield runtime
    await runtime.close()


async def _calling(rt: SessionRuntime) -> None:
    """A session in the calls flow, with one call to t:1 and one to t:2."""
    await rt.start(SessionStarted(session_id=SID, at=T0, idem_key=f"{SID}:start", channel="judge"))
    await rt.emit(CallsRequested(session_id=SID, at=T0, idem_key=f"{SID}:plan", listing_ids=["t:1", "t:2"]))
    await rt.emit(SessionEnded(session_id=SID, at=T0, idem_key=f"{SID}:ended", reason="hangup"))
    plan = CallPlan(session_id=SID, listing_ids=["t:1", "t:2"], created_at=T0)
    await rt.emit(CallsScheduled(session_id=SID, at=T0, idem_key=f"{SID}:scheduled", plan=plan,
                                 call_ids=CALLS))


def _fact(field: str, value: str, quote: str, at: datetime | None = T0) -> Fact:
    return Fact(field=field, value=value, quote=quote, speaker="Agent heard", at=at)


def _parking(at: datetime | None = T0) -> Fact:
    return _fact("parking_cost", "$150 a month", "It's 150 a month for parking.", at)


async def _outcome(rt: SessionRuntime, call: int, *facts: Fact, at: datetime = T0) -> None:
    out = CallOutcome(call_id=CALLS[call], listing_id=f"t:{call + 1}", end_reason="done", facts=list(facts))
    await rt.emit(OutcomeReady(session_id=SID, at=at, idem_key=f"{CALLS[call]}:outcome", outcome=out))
    await rt.idle()


async def test_each_grounded_fact_is_one_write_with_its_date_quote_and_listing_scope(rt):
    fake = FakeMemory()
    memory = install(rt, _settings(), client=_client(fake))
    await _calling(rt)
    await _outcome(rt, 0, _parking(), _fact("pets", "dogs allowed", "Dogs are fine, cats too.",
                                            at=datetime(2026, 10, 24, 4, 5, tzinfo=UTC)))
    assert fake.sent("/facts") == [   # no observed_at: the server refuses it on a literal write
        {"text": PARKING, "infer": "none", "scopes": [["landline/listing/t:1"]]},
        # 00:05 on Saturday in Toronto: the date is Toronto's, not UTC's, and it's in the sentence
        {"text": 'On 2026-10-24, the listing agent said pets: dogs allowed ("Dogs are fine, cats too.").',
         "infer": "none", "scopes": [["landline/listing/t:1"]]},
    ]
    writes = [r for r in fake.requests if r.url.path.endswith("/facts")]
    assert {str(r.url) for r in writes} == {f"{URL}/api/v1/{CTX}/facts"}
    keys = [r.headers["idempotency-key"] for r in writes]
    assert all(k.startswith("landline-") for k in keys) and len(set(keys)) == 2
    assert "scope" not in fake.sent("/facts")[0]   # the server would ignore it: writes take `scopes`
    await memory.close()


async def test_scopes_are_registered_parents_first_once_and_one_already_there_counts(rt):
    fake = FakeMemory()
    memory = install(rt, _settings(), client=_client(fake))
    await _calling(rt)
    await _outcome(rt, 0, _parking())
    await _outcome(rt, 1, _parking())
    routes = [r.url.path.removeprefix(BASE) for r in fake.requests]
    assert routes[:4] == ["/scopes", "/scopes", "/scopes", "/facts"]   # registered before the first write
    assert [b["path"] for b in fake.sent("/scopes")] == [
        "landline", "landline/listing", "landline/listing/t:1", "landline/listing/t:2"]
    # A restart forgets what it registered; the server's 409 for a path it has is not a failure.
    again = Memory(URL, CTX, KEY, client=_client(fake))
    await again.register(listing_scope("t:1"))
    await again.write(PARKING, listing_scope("t:1"))
    assert len(fake.facts) == 3
    await memory.close()
    await again.close()


async def test_a_fact_the_auditor_dropped_or_without_a_quote_is_never_written(rt):
    fake = FakeMemory()
    memory = install(rt, _settings(), client=_client(fake))
    await _calling(rt)
    turns = [Turn(0, "agent", "Hi, I'm an AI assistant calling for a renter. How much is parking?"),
             Turn(1, "landlord", "Parking is 150 a month for one spot."),
             Turn(2, "agent", "Great, and dogs are allowed in the building, right?"),
             Turn(3, "landlord", "I'd have to check with the owner about that.")]
    live = [ToolFact("parking_cost", "$150 a month", "Parking is 150 a month for one spot", 1),
            ToolFact("pets", "allowed", "dogs are allowed in the building", 2),           # the Caller's words
            ToolFact("laundry", "in_unit", "there is laundry in the unit itself", None)]   # nobody's
    listing = ListingContext.of({"listing_id": "t:1", "address": "1 Test St", "rent": 2001})
    record = CallRecord(call_id=CALLS[0], listing_id="t:1", end_reason="done", turns=turns, ended_at=T0,
                        listing=listing, tool_facts=live)
    audit = build(record, [])   # no transcript extractor: the Caller's live facts, grounded or dropped
    assert {d.why for d in audit.dropped} == {"agent_words", "ungrounded"}
    await rt.emit(OutcomeReady(session_id=SID, at=T0, idem_key=f"{CALLS[0]}:outcome", outcome=audit.outcome))
    await rt.idle()
    assert [b["text"] for b in fake.sent("/facts")] == [
        'On 2026-10-23, the listing agent said parking_cost: $150 a month per spot '
        '("Parking is 150 a month for one spot").']
    # A fact that lost its quote is never written, and a call that heard nothing sends nothing at all.
    before = len(fake.requests)
    await _outcome(rt, 1, _fact("rent", "$2,400", "  "), _fact("ac", "  ", "There's central air."))
    assert len(fake.requests) == before
    await memory.close()


@pytest.mark.parametrize("missing", ["spectron_url", "spectron_context_id", "spectron_api_key"])
async def test_without_all_three_keys_nothing_is_subscribed_or_sent(rt, missing):
    fake = FakeMemory()
    settings = _settings(**{missing: None})
    assert from_settings(settings) is None
    assert install(rt, settings, client=_client(fake)) is None
    await _calling(rt)
    await _outcome(rt, 0, _parking())
    assert fake.requests == [] and rt.get(SID).version > 1


@pytest.mark.parametrize("fail", [
    {"/facts": 503},
    {"/scopes": 500},
    {"/facts": httpx.ReadTimeout("timed out")},
    {"/scopes": httpx.ConnectError("connection refused")},
])
async def test_a_5xx_or_timeout_is_one_log_line_and_never_reaches_the_runtime(rt, fail, caplog):
    fake = FakeMemory(fail)
    memory = install(rt, _settings(), client=_client(fake))
    await _calling(rt)
    caplog.set_level(logging.INFO)
    view_before = rt.get(SID).version
    await _outcome(rt, 0, _parking(), _fact("pets", "dogs allowed", "Dogs are fine, cats too."))
    assert rt.get(SID).version == view_before + 1   # the outcome committed; the write failed after it
    mine = [r for r in caplog.records if r.name == "landline.orchestrator.memory"
            and "facts from call" in r.getMessage()]   # the plan's prefetch logs its own reads
    assert [r.levelno for r in mine] == [logging.WARNING]
    assert "2 of 2 facts from call c_memory00001 not saved to landline/listing/t:1" in mine[0].getMessage()
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]   # no subscriber traceback
    assert KEY not in caplog.text
    assert len(fake.sent("/facts")) <= 1   # it stops at the first failure: one wait, not one per fact
    await memory.close()


async def test_every_request_gives_up_after_its_own_timeout():
    default = Memory(URL, CTX, KEY)
    assert (TIMEOUT_S, default.timeout_s, default.query_timeout_s) == (5.0, 5.0, 12.0)   # reads take ~6 s
    await default.close()
    fake = FakeMemory({"/query": "slow", "/facts": "slow"})
    memory = Memory(URL, CTX, KEY, client=_client(fake), timeout_s=0.05, query_timeout_s=0.05)
    with pytest.raises(MemoryUnavailable, match="fact write"):
        await memory.write("x", listing_scope("t:1"))
    started = asyncio.get_running_loop().time()
    with pytest.raises(MemoryUnavailable) as caught:
        await memory.query("parking", listing_scope("t:1"))
    assert caught.value.status is None and "query of landline/listing/t:1 failed" in str(caught.value)
    assert asyncio.get_running_loop().time() - started < 1
    await memory.close()


async def test_a_query_returns_what_a_call_wrote_on_that_listing_only(rt):
    fake = FakeMemory()
    memory = install(rt, _settings(), client=_client(fake))
    await _calling(rt)
    await _outcome(rt, 0, _parking())
    await _outcome(rt, 1, _fact("pets", "no pets", "Sorry, no pets in the building."))
    hits = await memory.query("How much is parking?", listing_scope("t:1"))
    assert fake.sent("/query")[-1] == {"query": "How much is parking?", "lens": [["landline/listing/t:1"]],
                                       "k": 10}
    assert [(h.text, h.source, h.occurred_at) for h in hits] == [(PARKING, "memory_chunk", fake.now)]
    assert await memory.query("pets", listing_scope("t:9"), k=500) == []
    assert fake.sent("/query")[-1]["k"] == 50   # the server's cap
    await memory.close()


async def test_a_fact_with_no_time_of_its_own_is_dated_by_its_outcome(rt):
    fake = FakeMemory()
    memory = install(rt, _settings(), client=_client(fake))
    await _calling(rt)
    await _outcome(rt, 0, _parking(at=None), at=T0 + timedelta(days=1))
    assert fake.sent("/facts")[0]["text"].startswith("On 2026-10-24, the listing agent said parking_cost:")
    await memory.close()


async def test_the_keys_load_from_the_environment(monkeypatch):
    monkeypatch.setenv("SPECTRON_URL", URL + "/")
    monkeypatch.setenv("SPECTRON_CONTEXT_ID", CTX)
    monkeypatch.setenv("SPECTRON_API_KEY", KEY)
    memory = from_settings(Settings.load())
    assert memory is not None and memory.base == f"{URL}/api/v1/{CTX}"
    await memory.close()


# ── O-09b: reading a listing's facts back ───────────────────────────────────

THU = datetime(2026, 10, 29, 22, 0, tzinfo=UTC)   # Thursday 29 October, 18:00 in Toronto


def _said(day: str, field: str, value: str, quote: str) -> str:
    return f'On {day}, the listing agent said {field}: {value} ("{quote}").'


def _at(day: int, hour: int) -> datetime:
    return datetime(2026, 10, day, hour, tzinfo=UTC)


async def test_listing_knowledge_keeps_our_own_sentences_from_the_last_week_newest_per_field():
    fake = FakeMemory()
    memory = Memory(URL, CTX, KEY, client=_client(fake))
    scope = listing_scope("t:1")
    for s in (scope, listing_scope("t:2")):
        await memory.register(s)
    for text, at in [
        (_said("2026-10-28", "fees", "parking $140 a month", "Parking's 140."), _at(28, 15)),
        (_said("2026-10-28", "fees", "parking $150 a month", "It's 150 a month for parking."), _at(28, 20)),
        (_said("2026-10-23", "pets", "no pets", "Sorry, no pets."), _at(23, 18)),
        (_said("2026-10-27", "pets", "pets allowed", "Dogs and cats are fine now."), _at(27, 18)),
        (_said("2026-10-22", "available", "still available", "Yes, it's still available."), _at(22, 18)),
        (_said("2026-10-20", "laundry", "laundry in the unit", "It's in the unit."), _at(20, 18)),   # 9 days
        (_said("2026-10-28", "rent", "$2,400 a month", ""), _at(28, 20)),         # no quote: never a fact
        ("The listing agent mentioned the parking is about $150.", _at(28, 20)),  # not our sentence
        (_said("2026-13-40", "ac", "air conditioning", "There's central air."), _at(28, 20)),   # no such date
    ]:
        fake.now = at   # when the server stored it, which it returns as occurredAt
        await memory.write(text, scope)
    await memory.write(_said("2026-10-28", "pets", "no pets", "No pets here."), listing_scope("t:2"))
    # What the server inferred is never a fact (hard rule 3), even in the same words.
    fake.inferred = [
        {"source": "attribute", "score": 0.99, "text": _said("2026-10-28", "laundry", "no laundry", "None.")},
        {"source": "entity", "score": 0.95, "text": "1 Test St charges $150 a month for parking."},
    ]
    known = await listing_knowledge(memory, "t:1", now=THU)
    assert fake.sent("/query")[-1] == {"query": "What did the listing agent say?",
                                       "lens": [["landline/listing/t:1"]], "k": 50, "includeDuplicates": True}
    assert [(k.field, k.value, k.quote, k.day, k.at) for k in known] == [   # newest first; same day: later
        ("fees", "parking $150 a month", "It's 150 a month for parking.", date(2026, 10, 28), _at(28, 20)),
        ("pets", "pets allowed", "Dogs and cats are fine now.", date(2026, 10, 27), _at(27, 18)),
        ("available", "still available", "Yes, it's still available.", date(2026, 10, 22), _at(22, 18)),
    ]
    assert all(isinstance(k, Known) for k in known)
    assert await listing_knowledge(memory, "t:9", now=THU) == []   # nothing heard there: only the inferred
    await memory.close()


async def test_a_fact_is_dated_by_its_toronto_day_and_the_week_counts_from_now():
    fake = FakeMemory()
    memory = Memory(URL, CTX, KEY, client=_client(fake))
    await memory.register(listing_scope("t:1"))
    await memory.write(_said("2026-10-22", "fees", "parking $150 a month", "150 for parking."),
                       listing_scope("t:1"))
    assert [k.day for k in await listing_knowledge(memory, "t:1", now=THU)] == [date(2026, 10, 22)]
    # 00:30 on Friday in UTC is still Thursday the 29th in Toronto: 7 days, kept. A day later, 8: dropped.
    assert len(await listing_knowledge(memory, "t:1", now=datetime(2026, 10, 30, 0, 30, tzinfo=UTC))) == 1
    assert await listing_knowledge(memory, "t:1", now=datetime(2026, 10, 31, 0, 30, tzinfo=UTC)) == []
    await memory.close()


@pytest.mark.parametrize("fail", [{"/query": "slow"}, {"/query": 503},
                                  {"/query": httpx.ConnectError("connection refused")}])
async def test_recall_at_dial_time_gives_up_fast_never_raises_and_logs_one_line(fail, caplog):
    caplog.set_level(logging.INFO, logger="landline.orchestrator.memory")
    assert RECALL_S <= 2
    memory = Memory(URL, CTX, KEY, client=_client(FakeMemory(fail)))
    clock = asyncio.get_running_loop().time
    started = clock()
    assert await recall(memory, "t:1", now=THU, timeout_s=0.05) == []
    assert clock() - started < 1
    mine = [r for r in caplog.records if r.name == "landline.orchestrator.memory"]
    assert [r.levelno for r in mine] == [logging.WARNING]
    assert "landline/listing/t:1" in mine[0].getMessage() and "asks cold" in mine[0].getMessage()
    assert KEY not in caplog.text
    await memory.close()


async def test_recall_without_the_keys_is_nothing_with_one_line(caplog):
    caplog.set_level(logging.INFO, logger="landline.orchestrator.memory")
    assert await recall(None, "t:1") == []
    assert [r.getMessage() for r in caplog.records] == [
        "memory: no Agent Memory keys; the call to t:1 asks cold"]


# ── O-09c: the backfill, and a renter's own memory with consent ─────────────────────────────────────────

def _outcomes() -> list[tuple[CallOutcome, datetime]]:
    unquoted = _fact("pets", "dogs ok", "")   # no quote: never written (hard rule 3)
    deposit = _fact("deposit", "$2,600", "First and last, so twenty-six hundred.")
    first = CallOutcome(call_id=CALLS[0], listing_id="t:1", end_reason="done", facts=[_parking(), unquoted])
    second = CallOutcome(call_id=CALLS[1], listing_id="t:2", end_reason="done", facts=[deposit])
    return [(first, T0), (second, T0)]


async def test_the_backfill_writes_each_grounded_fact_once_and_a_second_run_writes_nothing():
    fake = FakeMemory()
    first = await backfill(Memory(URL, CTX, KEY, client=_client(fake)), _outcomes())
    assert first == {"listings": 2, "facts": 2, "written": 2, "already": 0, "failed": 0}
    deposit = ('On 2026-10-23, the listing agent said deposit: $2,600 '
               '("First and last, so twenty-six hundred.").')
    assert [f["text"] for f in fake.facts] == [PARKING, deposit]
    assert [f["scopes"] for f in fake.facts] == [[[listing_scope("t:1")]], [[listing_scope("t:2")]]]
    again = await backfill(Memory(URL, CTX, KEY, client=_client(fake)), _outcomes())   # a fresh process
    assert again == {"listings": 2, "facts": 2, "written": 0, "already": 2, "failed": 0}
    assert len(fake.facts) == 2


async def test_the_backfill_skips_what_the_live_writer_already_saved(rt):
    fake = FakeMemory()
    install(rt, _settings(), client=_client(fake))
    await _calling(rt)
    await _outcome(rt, 0, _parking())   # the live write after OutcomeReady
    counts = await backfill(Memory(URL, CTX, KEY, client=_client(fake)), _outcomes())
    assert (counts["written"], counts["already"]) == (1, 1)   # only the deposit was missing
    assert len(fake.facts) == 2


async def test_a_failing_memory_server_is_counted_per_listing_and_never_raises():
    fake = FakeMemory(fail={"/query": 503})
    counts = await backfill(Memory(URL, CTX, KEY, client=_client(fake)), _outcomes())
    assert (counts["failed"], counts["written"]) == (2, 0)


async def test_recent_outcomes_come_from_postgres_s_event_log(db):
    now = datetime.now(UTC)
    async with db.transaction() as conn:
        await conn.execute("truncate events")
        for n, days in ((1, 10), (2, 1)):
            out = CallOutcome(call_id=f"c_memory0000{n}", listing_id=f"t:{n}", end_reason="done",
                              facts=[_parking()])
            await db.events.append(OutcomeReady(session_id=SID, at=now - timedelta(days=days),
                                                idem_key=f"backfill:{n}", outcome=out), conn=conn)
        recent = await recent_outcomes(conn, now - timedelta(days=7))
    assert [(o.listing_id, o.facts[0].quote) for o, _ in recent] == [("t:2", "It's 150 a month for parking.")]


async def test_the_backfill_command_without_the_keys_says_so_and_sends_nothing(capsys):
    assert await run_backfill([], settings=_settings(spectron_api_key=None)) == 0
    assert capsys.readouterr().out.strip() == BACKFILL_SKIP


def test_a_renter_s_words_in_sentences_with_nothing_inferred():
    prefs = Preferences(max_monthly=2600, min_beds=2, anchors=[Anchor(label="King and Bay")],
                        pets=["dog"], priorities=["transit"], occupants=2)
    reactions = {"t:1": Said(reaction="dislike", reason="too dark"), "t:2": Said(reaction="like")}
    texts = renter_texts(prefs, reactions, lambda lid: {"t:1": "1 Test St", "t:2": "2 Test St"}[lid], T0)
    assert texts == [
        "On 2026-10-23, the renter said they want up to $2,600 a month; 2 bedrooms or more; "
        "near King and Bay; with dog; most important: transit; for 2 people.",
        "On 2026-10-23, the renter's reaction to 1 Test St was dislike (too dark).",
        "On 2026-10-23, the renter's reaction to 2 Test St was like.",
    ]
    assert renter_texts(Preferences(), {}, str, T0) == []


async def test_only_a_renter_who_agreed_is_remembered_and_only_in_their_own_scope(db):
    async with db.transaction() as conn:
        await conn.execute("truncate sessions, events, users cascade")
    runtime = SessionRuntime(Catalogue([_listing(1), _listing(2)], "test"), db)
    fake = FakeMemory()
    install(runtime, _settings(), client=_client(fake))
    for sid, phone in (("s_memoryyes01", "+14165550101"), ("s_memoryno001", "+14165550102")):
        await runtime.start(SessionStarted(session_id=sid, at=T0, idem_key=f"{sid}:start", channel="phone",
                                           caller_phone=phone))
        await runtime.emit(PreferencesUpdated(session_id=sid, at=T0, idem_key=f"{sid}:prefs",
                                              delta=PreferenceDelta(max_monthly=2600, min_beds=2)))
    agreed = (await db.users.upsert("+14165550101")).user_id   # both renters are on file by number
    declined = (await db.users.upsert("+14165550102")).user_id
    async with db.transaction() as conn:   # what "remember me" will set once it's switched on
        await conn.execute("update users set consent_memory = true where user_id = %s", (agreed,))
    for sid in ("s_memoryyes01", "s_memoryno001"):
        await runtime.emit(SessionEnded(session_id=sid, at=T0, idem_key=f"{sid}:ended", reason="hangup"))
    await runtime.idle()
    renters = [f for f in fake.facts if f["scopes"][0][0].startswith("landline/renter/")]
    assert [(f["scopes"], f["text"]) for f in renters] == [
        ([[renter_scope(agreed)]],
         "On 2026-10-23, the renter said they want up to $2,600 a month; 2 bedrooms or more.")]
    assert all(renter_scope(declined) not in s for f in fake.facts for c in f["scopes"] for s in c)
    await runtime.close()


# ── Live fixes: reads take ~6 s, so the plan prefetches them for the dial ─────────────────────────────────

async def _known_parking(fake: FakeMemory) -> Memory:
    memory = Memory(URL, CTX, KEY, client=_client(fake), now=lambda: THU)
    await memory.register(listing_scope("t:1"))
    await memory.write(PARKING, listing_scope("t:1"))
    return memory


async def test_the_plan_starts_a_prefetch_and_the_dial_uses_it(rt):
    fake = FakeMemory()
    memory = install(rt, _settings(), client=_client(fake))
    memory.now = lambda: THU
    await memory.register(listing_scope("t:1"))
    await memory.write(PARKING, listing_scope("t:1"))
    await _calling(rt)   # CallsRequested for t:1 and t:2
    await rt.idle()
    await asyncio.gather(*(task for _, task in memory._prefetched.values()))
    assert sorted(memory._prefetched) == ["t:1", "t:2"] and len(fake.sent("/query")) == 2
    known = await recall(memory, "t:1", timeout_s=0.05)
    assert [(k.field, k.value) for k in known] == [("parking_cost", "$150 a month")]
    assert len(fake.sent("/query")) == 2   # the dial asked nothing new
    await memory.close()


async def test_a_slow_read_lands_before_the_dial_with_a_prefetch_and_asks_cold_without():
    fake = FakeMemory({"/query": 0.3})   # the live server's ~6 s, scaled down
    memory = await _known_parking(fake)
    memory.prefetch("t:1")   # when the plan was recorded
    await asyncio.sleep(0.4)   # the rest of the renter's call, then the dial
    assert [k.field for k in await recall(memory, "t:1", timeout_s=0.05)] == ["parking_cost"]
    cold = await _known_parking(FakeMemory({"/query": 0.3}))   # no plan prefetched it
    assert await recall(cold, "t:1", timeout_s=0.05) == []   # the dial never waits on it
    await asyncio.sleep(0.4)   # ...but the read carries on, for the next call to that listing
    assert [k.field for k in await recall(cold, "t:1", timeout_s=0.05)] == ["parking_cost"]
    await memory.close()
    await cold.close()


async def test_a_prefetch_is_kept_15_minutes_and_a_failed_one_is_tried_again():
    ticks = [0.0]
    fake = FakeMemory()
    memory = await _known_parking(fake)
    memory.clock = lambda: ticks[0]
    first = memory.prefetch("t:1")
    await first
    assert memory.prefetch("t:1") is first and len(fake.sent("/query")) == 1
    ticks[0] = 15 * 60 + 1   # past PREFETCH_TTL_S
    await memory.prefetch("t:1")
    assert len(fake.sent("/query")) == 2
    fake.fail["/query"] = 503
    failed = memory.prefetch("t:2")
    with pytest.raises(MemoryUnavailable):
        await failed
    fake.fail.clear()
    assert memory.prefetch("t:2") is not failed   # a failed read isn't kept
    await memory.close()
