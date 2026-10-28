"""Backfill SurrealDB Agent Memory with the last week's listing-call facts (O-09c, #240).

Postgres is the system of record. This reads every listing call's outcome committed in the last --days days
(the OutcomeReady events) and writes its grounded facts to each listing's scope, in the live writer's own
sentences (orchestrator/memory.py). It's safe to run twice: each scope is read first, and a sentence already
there is skipped. Without SPECTRON_URL, SPECTRON_CONTEXT_ID and SPECTRON_API_KEY it prints a skip line and
sends nothing.

    uv run python scripts/memory_backfill.py --dry-run    # count what it would write, send nothing
    uv run python scripts/memory_backfill.py              # the last 7 days
    uv run python scripts/memory_backfill.py --days 3
"""
# Path is inserted before the project imports. Ruff's import-order rule doesn't cover that.
# ruff: noqa: E402
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))

import psycopg

from landline.config import Settings  # reads .env the way the API does
from landline.orchestrator import memory as m

SKIP = "memory backfill: SPECTRON_URL, SPECTRON_CONTEXT_ID and SPECTRON_API_KEY aren't all set; skipping"


async def run(argv: list[str] | None = None, settings: Settings | None = None) -> int:
    parser = argparse.ArgumentParser(prog="memory_backfill", description=__doc__.split("\n\n")[0])
    parser.add_argument("--days", type=int, default=m.BACKFILL_DAYS, help="how far back (default 7)")
    parser.add_argument("--dry-run", action="store_true", help="count what it would write; send nothing")
    args = parser.parse_args(argv)
    settings = settings or Settings.load()
    memory = m.from_settings(settings)
    if memory is None:
        print(SKIP)
        return 0
    if not settings.database_url:
        print("Set DATABASE_URL: the facts come from Postgres.", file=sys.stderr)
        await memory.close()
        return 2
    since = datetime.now(UTC) - timedelta(days=args.days)
    try:
        async with await psycopg.AsyncConnection.connect(settings.database_url) as conn:
            outcomes = await m.recent_outcomes(conn, since)
        if args.dry_run:
            facts = sum(1 for o, _ in outcomes for f in o.facts if m.rememberable(f))
            listings = len({o.listing_id for o, _ in outcomes})
            print(f"memory backfill (dry run): {len(outcomes)} call outcomes since "
                  f"{since:%Y-%m-%d %H:%M} UTC, {facts} facts with quotes on {listings} listings; "
                  "nothing sent")
            return 0
        c = await m.backfill(memory, outcomes)
    finally:
        await memory.close()
    print(f"memory backfill: {c['listings']} listings, {c['facts']} facts: {c['written']} written, "
          f"{c['already']} already there, {c['failed']} listings failed")
    return 1 if c["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
