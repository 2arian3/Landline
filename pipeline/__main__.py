"""python -m pipeline <command>

  run      fetch + normalize one source, write JSONL, print coverage; --load upserts to Postgres
  load     upsert an existing JSONL file into Postgres
  report   print coverage for a JSONL file
  light    the light estimate (D-07): floor and light words, photo brightness; --load updates Postgres
  geo      neighbourhoods, their neighbours and the nearest subway station (D-05); --load updates Postgres
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

    lt = sub.add_parser("light", help="estimate each listing's natural light")
    lt.add_argument("--photos", type=int, default=2, help="photos measured per listing (0: rules only)")
    lt.add_argument("--limit", type=int, default=0, help="only the first N listings (0: all)")
    lt.add_argument("--load", action="store_true", help="update light_score and floor in $DATABASE_URL")

    gp = sub.add_parser("geo", help="neighbourhood and nearest station for every listing")
    gp.add_argument("--load", action="store_true", help="load and tag in $DATABASE_URL (otherwise a dry run)")

    args = parser.parse_args(argv)

    if args.cmd == "light":
        return _light(args)
    if args.cmd == "geo":
        return _geo(args)

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


def _light(args) -> int:
    """Rules over the latest snapshot's descriptions, then each listing's photos, one pixel apiece."""
    from pipeline import light
    from pipeline.sources import livrent

    listings = stages.read_jsonl(stages.DATA / "stages" / "normalize" / "livrent-latest.jsonl")
    if args.limit:
        listings = listings[: args.limit]
    days = sorted(p for p in (stages.DATA / "raw" / "livrent").iterdir() if p.is_dir())
    texts: dict[str, str] = {}
    for page in sorted(days[-1].glob("*.html")) if days else []:
        texts.update(livrent.descriptions(page.read_text()))
    clues = {x.listing_id: light.read_clues(x.listing_id, texts.get(x.source_id), x.unit_type)
             for x in listings}
    described = sum(1 for x in listings if x.source_id in texts)
    with_words = sum(1 for c in clues.values() if c.cues or c.floor is not None)
    print(f"light: {len(listings)} listings, {described} descriptions, "
          f"{with_words} with a floor or light words")
    if args.photos:
        contact = os.getenv("SCRAPER_CONTACT_EMAIL")
        if not contact:
            print("Set SCRAPER_CONTACT_EMAIL for the photo requests' User-Agent.", file=sys.stderr)
            return 2
        client = PoliteClient(contact=contact)
        try:
            photos = {x.listing_id: x.photos for x in listings}
            light.measure_photos(clues, photos, client.get_bytes, args.photos)
        finally:
            client.close()
    out = light.rows(clues)
    path = light.write(out, stages.DATA / "stages" / "light" / "livrent-latest.jsonl")
    measured = sum(1 for r in out if r["light_basis"]["photos"])
    print(f"wrote {path.relative_to(stages.ROOT)}: {len(out)} scores, {measured} with photos")
    if args.load:
        url = os.getenv("DATABASE_URL")
        if not url:
            print("Set DATABASE_URL to load.", file=sys.stderr)
            return 2
        print(f"updated {light.load(out, url)} listings")
    return 0


def _geo(args) -> int:
    """The City's neighbourhoods (fetched politely, cached), the TTC stations, then every listing tagged."""
    from pipeline import geo

    contact = os.getenv("SCRAPER_CONTACT_EMAIL")
    if not contact:
        print("Set SCRAPER_CONTACT_EMAIL for the City request's User-Agent.", file=sys.stderr)
        return 2
    client = PoliteClient(contact=contact, cache_ttl_s=7 * 24 * 3600)   # boundaries change yearly
    try:
        hoods = geo.parse_neighbourhoods(client.get(geo.HOODS_URL))
    finally:
        client.close()
    station_rows = geo.stations()
    print(f"geo: {len(hoods)} neighbourhoods, {len(station_rows)} stations")
    if not args.load:
        return 0
    url = os.getenv("DATABASE_URL")
    if not url:
        print("Set DATABASE_URL to load.", file=sys.stderr)
        return 2
    import psycopg

    with psycopg.connect(url) as conn:
        print(geo.report(geo.load(conn, hoods, station_rows)))
    return 0


def _no_cache(client: PoliteClient, max_pages: int, started):
    client.cache_ttl_s = 0
    return stages.fetch_livrent(client, max_pages, started)


if __name__ == "__main__":
    raise SystemExit(main())
