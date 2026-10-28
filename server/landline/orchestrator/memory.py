"""Shared listing memory on SurrealDB Agent Memory (O-09a): what each listing call heard, for the next one.

Postgres stays the system of record. This mirrors each call's facts into Agent Memory, so the next renter's
call to the same place can start from them (O-09b) instead of asking cold. The REST API lives under
{SPECTRON_URL}/api/v1/{SPECTRON_CONTEXT_ID}/ with `Authorization: Bearer {SPECTRON_API_KEY}`
(surrealdb.com/docs/agent-memory/reference/rest-api):

- `POST /scopes {"path"}` registers a scope path. Paths are registered before their first write, parents
  first (landline, landline/listing, landline/listing/<id>), once per process.
- `POST /facts` writes one fact. Its body is snake_case: `text`, `scopes` (a DNF selector, `[[path]]`),
  `observed_at`, and `infer: "none"`, which stores the sentence as written (a `memory_chunk`), so the quote
  and the date come back word for word. The default, `full`, has a model extract attributes from the
  text, which is an inferred summary, not a fact with its quote (hard rule 3).
- `POST /query` reads. Its body is camelCase: `query`, `lens` (the read's scope, `[[path]]`), and `k` (at
  most 50). The server ignores a field it doesn't know without an error, so a read that sent `scope`
  instead of `lens` would search everything the key can read.

On OutcomeReady, `install`'s subscriber runs as a background task after the commit, so it never holds up
a call or a turn (hard rule 7). It writes each fact of the call's outcome to landline/listing/<listing_id>
as one sentence, dated in Toronto:

    On 2026-10-28, the listing agent said parking_cost: $150 a month ("It's 150 a month for parking.").

The Auditor has already grounded every fact in an outcome (its quote is in a landlord turn) and dropped
the rest, so a fact is written only while it still carries its quote and value. Each request times out
after 3 s, and a failure is one log line that never reaches the runtime. Without the three keys,
`from_settings` and `install` return None and nothing is sent.

O-09b's `listing_knowledge(listing_id)` sits on top of `query`: keep the `memory_chunk` hits, read the
date, field, value and quote back out of `fact_text`'s sentence, and keep the last 7 days.

The live run: `uv run python -m landline.orchestrator.memory` writes a test fact to
landline/listing/test:o09a and queries it back, and `... memory livrent:100219` only queries that listing.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx

from landline.config import Settings
from landline.contracts import CallOutcome, Event, Fact, OutcomeReady, SessionView

if TYPE_CHECKING:
    from landline.orchestrator.runtime import SessionRuntime

log = logging.getLogger(__name__)

TORONTO = ZoneInfo("America/Toronto")
TIMEOUT_S = 3.0
LISTINGS = "landline/listing"
K, K_MAX = 10, 50              # the server's default and cap for `k` on /query
TEST_LISTING = "test:o09a"     # the live run's own scope; no real listing has this id


class MemoryUnavailable(Exception):
    """Agent Memory refused a request (`status` is its HTTP status) or couldn't be reached in time (`status`
    is None). The message has the status and the server's reason, never the key."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Hit:
    """One ranked result from /query."""
    text: str
    source: str                          # memory_chunk for what Landline wrote; else entity, attribute, …
    score: float = 0.0
    occurred_at: datetime | None = None  # the record's known time (observed_at on Landline's writes)
    id: str = ""


class Memory:
    """The Agent Memory REST client: register a scope, write a fact, query."""

    def __init__(self, url: str, context_id: str, api_key: str, client: httpx.AsyncClient | None = None,
                 timeout_s: float = TIMEOUT_S) -> None:
        self.base = f"{url.rstrip('/')}/api/v1/{quote(context_id, safe='')}"
        self.client = client or httpx.AsyncClient(timeout=timeout_s)
        self.timeout_s = timeout_s
        self._auth = {"Authorization": f"Bearer {api_key}"}
        self._registered: set[str] = set()

    async def register(self, path: str) -> None:
        """Registers `path` and every parent above it, each once per process. A path that is already
        registered (409) counts as registered."""
        for p in _lineage(path):
            if p not in self._registered:
                await self._post("/scopes", f"scope {p} register", {"path": p}, tolerate=(409,))
                self._registered.add(p)

    async def write(self, text: str, scope: str, *, observed_at: datetime | None = None) -> None:
        """Stores `text` as written in `scope`. The same sentence in the same scope is one memory: its
        Idempotency-Key comes from both, so a retry within the server's 24 hours is a no-op."""
        body: dict = {"text": text, "infer": "none", "scopes": [[scope]]}
        if observed_at is not None:
            body["observed_at"] = _utc(observed_at).isoformat(timespec="seconds")
        key = hashlib.sha256(f"{scope}\n{text}".encode()).hexdigest()[:32]
        # 409 is the same key still in flight, or already stored: the fact is there either way.
        await self._post("/facts", f"fact write to {scope}", body,
                         headers={"Idempotency-Key": f"landline-{key}"}, tolerate=(409,))

    async def query(self, query: str, scope: str, *, k: int = K) -> list[Hit]:
        """The hits for `query` inside `scope` (the read's `lens`), best first."""
        body = {"query": query, "lens": [[scope]], "k": max(1, min(k, K_MAX))}
        answer = await self._post("/query", f"query of {scope}", body)
        hits = answer.get("hits") if isinstance(answer, dict) else None
        return [h for h in (_hit(raw) for raw in hits or []) if h is not None]

    async def _post(self, path: str, what: str, body: dict, *, headers: dict[str, str] | None = None,
                    tolerate: tuple[int, ...] = ()) -> dict:
        try:
            async with asyncio.timeout(self.timeout_s):   # the whole request, connect to last byte
                r = await self.client.post(f"{self.base}{path}", json=body,
                                           headers={**self._auth, **(headers or {})})
        except (httpx.HTTPError, TimeoutError) as e:   # a timeout's message is empty, so its repr says what
            raise MemoryUnavailable(f"{what} failed ({e!r})") from None
        if r.status_code in tolerate:
            return {}
        if r.status_code >= 300:
            raise MemoryUnavailable(f"{what} refused ({r.status_code}): {_reason(r)}", r.status_code)
        try:
            return r.json() if r.content else {}
        except ValueError:
            raise MemoryUnavailable(f"{what} answered with no JSON", r.status_code) from None

    async def close(self) -> None:
        await self.client.aclose()


def from_settings(settings: Settings, client: httpx.AsyncClient | None = None) -> Memory | None:
    """The client, or None unless all three keys are set."""
    keys = (settings.spectron_url, settings.spectron_context_id, settings.spectron_api_key)
    if not all(keys):
        return None
    return Memory(*keys, client=client)


def install(runtime: SessionRuntime, settings: Settings,
            client: httpx.AsyncClient | None = None) -> Memory | None:
    """Writes each listing call's facts to Agent Memory after its OutcomeReady commits. Returns the client to
    close at shutdown, or None, with nothing subscribed, without the keys."""
    memory = from_settings(settings, client=client)
    if memory is None:
        return None

    async def on_outcome(event: Event, view: SessionView) -> None:
        if isinstance(event, OutcomeReady):
            await remember_outcome(memory, event.outcome, event.at)

    runtime.on("OutcomeReady", on_outcome)
    return memory


async def remember_outcome(memory: Memory, outcome: CallOutcome, at: datetime) -> int:
    """Writes the outcome's facts to its listing's scope and returns how many were saved. `at` dates a fact
    that has no time of its own. Never raises for Agent Memory: a failure is one warning, and the facts
    after it wait for the backfill (O-09c), since Postgres has them all."""
    facts = [f for f in outcome.facts if rememberable(f)]
    if not facts:
        return 0
    scope = listing_scope(outcome.listing_id)
    saved = 0
    try:
        await memory.register(scope)
        for fact in facts:
            when = fact.at or at
            await memory.write(fact_text(fact, when), scope, observed_at=when)
            saved += 1
    except MemoryUnavailable as e:
        log.warning("memory: %d of %d facts from call %s not saved to %s: %s", len(facts) - saved, len(facts),
                    outcome.call_id, scope, e)
        return saved
    log.info("memory: %d facts from call %s saved to %s", saved, outcome.call_id, scope)
    return saved


def rememberable(fact: Fact) -> bool:
    """A fact with its quote and value (hard rule 3). Anything else is never written."""
    return bool(fact.quote.strip() and fact.value.strip())


def fact_text(fact: Fact, at: datetime) -> str:
    """The sentence a fact is remembered as, dated in Toronto, with the landlord's exact words."""
    day = _utc(at).astimezone(TORONTO).date().isoformat()
    return f'On {day}, the listing agent said {fact.field}: {fact.value.strip()} ("{fact.quote.strip()}").'


def listing_scope(listing_id: str) -> str:
    return f"{LISTINGS}/{listing_id}"


def _lineage(path: str) -> Iterator[str]:
    """"a/b/c" → "a", "a/b", "a/b/c"."""
    parts = [p for p in path.strip("/").split("/") if p]
    for i in range(1, len(parts) + 1):
        yield "/".join(parts[:i])


def _utc(at: datetime) -> datetime:
    return (at if at.tzinfo else at.replace(tzinfo=UTC)).astimezone(UTC)


def _hit(raw: object) -> Hit | None:
    if not isinstance(raw, dict) or not isinstance(raw.get("text"), str):
        return None
    when = raw.get("occurredAt")
    try:
        occurred = datetime.fromisoformat(when) if isinstance(when, str) else None
    except ValueError:
        occurred = None
    score = raw.get("score")
    return Hit(text=raw["text"], source=str(raw.get("source") or ""),
               score=float(score) if isinstance(score, int | float) else 0.0, occurred_at=occurred,
               id=str(raw.get("id") or ""))


def _reason(r: httpx.Response) -> str:
    """The server's `{"message": …}`, or the start of the body. Neither carries the key."""
    try:
        body = r.json()
    except ValueError:
        return r.text[:200]
    if isinstance(body, dict) and body.get("message"):
        return str(body["message"])[:200]
    return r.text[:200]


async def _check(listing_id: str | None) -> None:
    """The live run. With no listing id, a test fact goes through the same path as a call's (to
    landline/listing/test:o09a, which no real listing has) and is queried back; with one, only that
    listing's memory is read."""
    memory = from_settings(Settings.load())
    if memory is None:
        raise SystemExit("set SPECTRON_URL, SPECTRON_CONTEXT_ID and SPECTRON_API_KEY in .env")
    try:
        if listing_id is None:
            fact = Fact(field="parking_cost", value="$150 a month", quote="It's 150 a month for parking.",
                        speaker="Agent heard")
            outcome = CallOutcome(call_id="c_o09a_check", listing_id=TEST_LISTING, end_reason="done",
                                  facts=[fact])
            print(f"saved {await remember_outcome(memory, outcome, datetime.now(UTC))} fact")
        scope = listing_scope(listing_id or TEST_LISTING)
        hits = await memory.query("What did the listing agent say?", scope)
        print(f"{len(hits)} hits in {scope}")
        for hit in hits:
            print(f"  {hit.source:<12} {hit.occurred_at or '-'}  {hit.text}")
    finally:
        await memory.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    asyncio.run(_check(sys.argv[1] if len(sys.argv) > 1 else None))
