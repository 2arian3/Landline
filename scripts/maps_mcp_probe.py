"""Ask Google's Maps MCP server real questions and record its answers (O-07a).

    uv run python scripts/maps_mcp_probe.py            # says what it would do, and sends nothing
    uv run python scripts/maps_mcp_probe.py --live     # needs GOOGLE_MAPS_API_KEY, in the environment or .env

With --live it asks Maps Grounding Lite, through orchestrator/maps_mcp.py, for the closest "Shoppers Drug
Mart" to a Toronto point (43.6649,-79.4190 unless --lat/--lng/--what say otherwise) and the walk there, a
drive from there to Union Station, and tomorrow's weather: 4 requests, out of 10,000 free a month. It prints
each answer with its source and how long it took, and writes each raw JSON-RPC answer to
server/tests/fixtures/maps_mcp/ as recorded_<tool>.json (recorded_compute_routes_walk.json and _drive for the
routes), which test_maps_mcp.py then replays. The key goes only in request headers, which aren't written.

Recorded answers are Google Maps content: Google's terms let us keep them 30 days at most, for checking how
answers work, so delete or re-record them within 30 days rather than commit them for good.

The key needs the Maps Grounding Lite API (mapstools.googleapis.com) enabled on a project with billing, and
allowed in the key's API restrictions. A refusal prints as a "maps mcp" log line with Google's reason.
"""
# Path is inserted before the project imports. Ruff's import-order rule doesn't cover that.
# ruff: noqa: E402
from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import logging
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))

from landline.config import Settings  # reads .env the way the API does
from landline.orchestrator import maps_mcp

FIXTURES = ROOT / "server" / "tests" / "fixtures" / "maps_mcp"
POINT = (43.6649, -79.4190)   # Bloor St W near Christie
WHAT = "Shoppers Drug Mart"
DRIVE_TO = "Union Station, Toronto"


class Recorder:
    """An httpx response hook: writes each tool's JSON-RPC answer, with what it was asked, to a fixture."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self.written: list[Path] = []

    async def __call__(self, response: httpx.Response) -> None:
        await response.aread()
        try:
            sent = json.loads(response.request.content)
        except ValueError:
            return
        params = sent.get("params") or {}
        tool, args = params.get("name") or "unknown", params.get("arguments") or {}
        name = f"{tool}_{str(args.get('travelMode', 'DRIVE')).lower()}" if tool == "compute_routes" else tool
        ctype = response.headers.get("content-type", "")
        msg = maps_mcp.parse_message(response.text, ctype, sent.get("id"))
        record = {"_recorded_at": datetime.now(UTC).isoformat(timespec="seconds"),
                  "_status": response.status_code, "_content_type": ctype, "_arguments": args,
                  **(msg if msg is not None else {"_raw": response.text[:4000]})}
        self.folder.mkdir(parents=True, exist_ok=True)
        path = self.folder / f"recorded_{name}.json"
        path.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n")
        self.written.append(path)
        try:
            took = f"{response.elapsed.total_seconds() * 1000:.0f} ms"
        except RuntimeError:   # httpx sets it once the stream is closed
            took = "time unknown"
        print(f"  {tool}: HTTP {response.status_code}, {ctype or 'no content type'}, {took} "
              f"-> {path.relative_to(ROOT)}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Ask Google's Maps MCP server and record its answers (O-07a).")
    ap.add_argument("--live", action="store_true", help="send the 4 requests (needs GOOGLE_MAPS_API_KEY)")
    ap.add_argument("--lat", type=float, default=POINT[0])
    ap.add_argument("--lng", type=float, default=POINT[1])
    ap.add_argument("--what", default=WHAT)
    ap.add_argument("--timeout", type=float, default=maps_mcp.TIMEOUT_S,
                    help=f"seconds per request (default {maps_mcp.TIMEOUT_S:g}, the API's)")
    args = ap.parse_args(argv)
    if not args.live:
        print((__doc__ or "").strip())
        print("\nNothing was sent. Add --live to ask Google.")
        return 0
    key = Settings.load().google_maps_api_key
    if not key:
        print("GOOGLE_MAPS_API_KEY isn't set (environment or .env), so nothing was sent.")
        return 1
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)   # its request lines add nothing here
    return asyncio.run(_live(key, args))


async def _live(key: str, args: argparse.Namespace) -> int:
    recorder = Recorder(FIXTURES)
    http = httpx.AsyncClient(timeout=args.timeout, event_hooks={"response": [recorder]})
    maps = maps_mcp.MapsMCP(key, client=http, timeout_s=args.timeout)
    point = (args.lat, args.lng)
    try:
        started = time.perf_counter()
        place = await maps.nearby(*point, args.what)
        _show(f"nearby({args.lat}, {args.lng}, {args.what!r})", place, started)
        started = time.perf_counter()
        drive = await maps.route(place or point, DRIVE_TO, "DRIVE")
        _show(f"route(..., {DRIVE_TO!r}, 'DRIVE')", drive, started)
        tomorrow = (datetime.now(maps_mcp.TORONTO) + timedelta(days=1)).date()
        started = time.perf_counter()
        _show(f"weather({args.lat}, {args.lng}, {tomorrow})", await maps.weather(*point, tomorrow), started)
    finally:
        await maps.close()
    print(f"\nWith every answer: its source link and attribution, and \"{maps_mcp.DISCLOSURE}\".")
    print(f"{len(recorder.written)} answers recorded in {FIXTURES.relative_to(ROOT)}/ "
          "(keep them 30 days at most).")
    if place is None or place.minutes is None:
        print("No place with walking minutes: see the log lines above.")
        return 1
    return 0


def _show(label: str, answer: maps_mcp.Answer | None, started: float) -> None:
    ms = (time.perf_counter() - started) * 1000
    if answer is None:
        print(f"{label}: no answer ({ms:.0f} ms)\n")
        return
    print(f"{label} ({ms:.0f} ms):")
    fields = dataclasses.asdict(answer)
    summary = fields.pop("summary", None)
    for k, v in fields.items():
        print(f"  {k}: {v}")
    if summary:
        print("  summary:\n    " + summary.replace("\n", "\n    "))
    print()


if __name__ == "__main__":
    sys.exit(main())
