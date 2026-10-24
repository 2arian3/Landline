"""Copy production's data into your local database (X-00): `make db-pull`.

Downloads GET /api/admin/export with ADMIN_TOKEN (in your .env; it comes through the password manager) and
loads it into the local database from `make db-up` (:54329), replacing what the copied tables held.
Production is only read. The local schema is migrated first. It refuses to load anywhere but a database on
this machine, into production itself (through a tunnel, say), or when production has a migration your
checkout doesn't (pull main first).

    make db-pull                          # listings, renters, sessions, calls and transcripts
    make db-pull ARGS=--audio             # and the call recordings
    uv run python scripts/db_pull.py --api http://localhost:8000 --database-url postgresql://...
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import httpx
import psycopg
from dotenv import load_dotenv
from psycopg.conninfo import conninfo_to_dict

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))

from landline.db.export import CLUSTER, lines, load, read_head  # noqa: E402
from landline.db.migrations import apply  # noqa: E402

API = "https://landline.134.87.8.230.sslip.io"
LOCAL_DB = "postgresql://landline:landline@localhost:54329/landline"
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def main() -> int:
    load_dotenv(ROOT / ".env")
    p = argparse.ArgumentParser(description="Copy production's data into your local database.")
    p.add_argument("--api", default=os.getenv("LANDLINE_API") or API, help=f"the API to copy from ({API})")
    p.add_argument("--database-url", default=LOCAL_DB, help=f"the local database to load into ({LOCAL_DB})")
    p.add_argument("--audio", action="store_true", help="copy the call recordings too")
    args = p.parse_args()

    token = os.getenv("ADMIN_TOKEN")
    if not token:
        print("ADMIN_TOKEN is not set: add it to .env from the password manager", file=sys.stderr)
        return 1
    host = conninfo_to_dict(args.database_url).get("host") or "localhost"
    if host not in LOCAL_HOSTS and not host.startswith("/"):
        print(f"refusing to load into {host}: only a database on this machine", file=sys.stderr)
        return 1

    url = f"{args.api.rstrip('/')}/api/admin/export"
    started = time.monotonic()
    timeout = httpx.Timeout(30, read=300)
    with httpx.stream("GET", url, params={"audio": "true"} if args.audio else None, timeout=timeout,
                      headers={"Authorization": f"Bearer {token}"}) as resp:
        if resp.status_code != 200:
            reason = {401: "wrong ADMIN_TOKEN", 404: "the API has no admin panel (ADMIN_TOKEN unset there)"}
            why = reason.get(resp.status_code, resp.reason_phrase)
            print(f"{url}: {resp.status_code} {why}", file=sys.stderr)
            return 1
        stream = lines(resp.iter_bytes())
        head = read_head(stream)
        with psycopg.connect(args.database_url, autocommit=True, connect_timeout=5) as conn:
            if head.get("cluster") and conn.execute(CLUSTER).fetchone()[0] == head["cluster"]:
                print("refusing: that database is production itself", file=sys.stderr)
                return 1
            for name in apply(conn):
                print(f"applied {name} locally")
            mine = {r[0] for r in conn.execute("select name from schema_migrations")}
            if behind := sorted(set(head["migrations"]) - mine):
                print(f"production has migrations this checkout doesn't: {', '.join(behind)}. "
                      "Pull main first.", file=sys.stderr)
                return 1
            counts = load(conn, head, stream)
        size = resp.num_bytes_downloaded
    print(f"copied production as of {head['at']} ({size / 1e6:.1f} MB in {time.monotonic() - started:.0f} s)")
    for name, n in counts.items():
        if n:
            print(f"  {name:22} {n}")
    if not head.get("audio"):
        print("  (call recordings left out: ARGS=--audio)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
