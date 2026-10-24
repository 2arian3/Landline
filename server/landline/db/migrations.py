"""Apply db/migrations/*.sql in order. Forward-only.

Each file runs in its own transaction and is recorded in schema_migrations, so
running this twice is a no-op. `scripts/migrate.py` is the command-line entry the
deploy and CI use; tests call `apply()` with a throwaway schema.
"""
from __future__ import annotations

from pathlib import Path

import psycopg
from psycopg import sql

MIGRATIONS = Path(__file__).resolve().parents[3] / "db" / "migrations"


def apply(conn: psycopg.Connection, schema: str | None = None, folder: Path = MIGRATIONS) -> list[str]:
    """Apply every migration not yet recorded; return the names applied. `conn` must be in autocommit.

    With `schema`, the tables go into that schema (search path `<schema>, public`, so the PostGIS and
    pgvector types in public still resolve) and schema_migrations is kept there too.
    """
    if schema:
        conn.execute(sql.SQL("set search_path to {}, public").format(sql.Identifier(schema)))
    conn.execute(
        "create table if not exists schema_migrations "
        "(name text primary key, applied_at timestamptz not null default now())"
    )
    done = {r[0] for r in conn.execute("select name from schema_migrations")}
    applied = []
    for path in sorted(folder.glob("*.sql")):
        if path.name in done:
            continue
        with conn.transaction():
            conn.execute(path.read_text())
            conn.execute("insert into schema_migrations (name) values (%s)", (path.name,))
        applied.append(path.name)
    return applied
