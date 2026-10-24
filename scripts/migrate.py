"""Apply db/migrations/*.sql in order to $DATABASE_URL. Forward-only.

Each file runs in its own transaction and is recorded in schema_migrations, so
running this twice is a no-op. The runner lives in server/landline/db/migrations.py;
this script puts server/ on the path itself, because the deploy runs it without
PYTHONPATH (deploy/remote.sh).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import psycopg
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))

from landline.db.migrations import apply  # noqa: E402


def main() -> int:
    load_dotenv()
    url = os.getenv("DATABASE_URL")
    if not url:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 1
    with psycopg.connect(url, autocommit=True) as conn:
        for name in apply(conn):
            print(f"applied {name}")
    print("migrations up to date")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
