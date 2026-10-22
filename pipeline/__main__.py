"""python -m pipeline <command>

  run      fetch + normalize one source, write JSONL, print coverage; --load upserts to Postgres
  load     upsert an existing JSONL file into Postgres
  report   print coverage for a JSONL file
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from pipeline import stages
from pipeline.http import PoliteClient


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(prog="python -m pipeline")
    sub = parser.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run", help="fetch and normalize a source")
    run.add_argument("--source", default="livrent", choices=["livrent"])
    run.add_argument("--max-pages", type=int, default=40)
    run.add_argument("--min-delay", type=float, default=2.0, help="seconds between requests to one host")
    run.add_argument("--no-cache", action="store_true")
    run.add_argument("--load", action="store_true", help="upsert into $DATABASE_URL")
    run.add_argument("--min-listings", type=int, default=0,
                     help="refuse to load if the run found fewer listings, so a broken scrape "
                          "never overwrites good data")
    run.add_argument("--export-fixture", type=Path, help="write a geocoded sample for the web app")
    run.add_argument("--fixture-limit", type=int, default=250)

    ld = sub.add_parser("load", help="upsert a JSONL file into Postgres")
    ld.add_argument("path", type=Path)

    rp = sub.add_parser("report", help="coverage for a JSONL file")
    rp.add_argument("path", type=Path)

    args = parser.parse_args(argv)

    if args.cmd == "report":
        print(stages.report(stages.read_jsonl(args.path)))
        return 0

    if args.cmd == "load":
        listings = stages.read_jsonl(args.path)
        started = stages.now()
        result = stages.RunResult("file", 0, listings, complete=False, stopped_because="loaded from file")
        print(stages.load(result, started))
        return 0

    contact = os.getenv("SCRAPER_CONTACT_EMAIL")
    if not contact:
        print("Set SCRAPER_CONTACT_EMAIL; it goes into the User-Agent so sites can reach us.",
              file=sys.stderr)
        return 2
    client = PoliteClient(contact=contact, min_delay=args.min_delay)
    started = stages.now()
    print(f"{args.source}: fetching up to {args.max_pages} pages as {client.user_agent!r}")
    try:
        result = stages.fetch_livrent(client, args.max_pages, started) if not args.no_cache else \
            _no_cache(client, args.max_pages, started)
    finally:
        client.close()
    out = stages.write_jsonl(result, started)
    print(f"{result.stopped_because}; wrote {out.relative_to(stages.ROOT)}")
    print(stages.report(result.listings))
    located = sum(1 for x in result.listings if x.lat is not None)
    if len(result.listings) < args.min_listings or (result.listings and located / len(result.listings) < 0.9):
        print(f"check failed: {len(result.listings)} listings (need {args.min_listings}), "
              f"{located} with coordinates; not loading", file=sys.stderr)
        return 3
    stages.promote_latest(result)
    if args.export_fixture:
        stages.export_fixture(result.listings, args.export_fixture, args.fixture_limit)
        print(f"fixture: {args.export_fixture}")
    if args.load:
        print("load:", stages.load(result, started))
    return 0 if result.listings else 1


def _no_cache(client: PoliteClient, max_pages: int, started):
    client.cache_ttl_s = 0
    return stages.fetch_livrent(client, max_pages, started)


if __name__ == "__main__":
    raise SystemExit(main())
