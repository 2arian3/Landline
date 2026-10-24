"""A copy of the whole database, so teammates can have production's data locally (X-00).

`GET /api/admin/export` streams it with `export()` (api/admin.py), and `make db-pull` (scripts/db_pull.py)
loads it into a local database with `load()`. Production is only read: one read-only, repeatable-read
transaction, so every table comes from the same moment.

The stream is gzipped text. A header line of JSON (the format, when, the source cluster's id, the migrations
applied, and every table with its columns, parents before children), then each table as a
`COPY <table> (<columns>) FROM stdin;` block in Postgres's text format, ended by a `\\.` line, the way
pg_dump writes them. Call recordings (call_audio) are the bulk of the bytes, so they're left out unless asked.
"""
from __future__ import annotations

import json
import zlib
from collections.abc import AsyncIterator, Iterable, Iterator
from datetime import UTC, datetime
from graphlib import TopologicalSorter

import psycopg
from psycopg import AsyncConnection, sql

FORMAT = "landline-export-v1"
AUDIO = "call_audio"
END = b"\\.\n"

# The schema's own tables: not schema_migrations (each database keeps its own), not PostGIS's spatial_ref_sys.
TABLES = """
select c.oid, c.relname from pg_class c
where c.relnamespace = (select oid from pg_namespace where nspname = current_schema())
  and c.relkind = 'r' and c.relname <> 'schema_migrations'
  and not exists (select 1 from pg_depend d
                  where d.classid = 'pg_class'::regclass and d.objid = c.oid and d.deptype = 'e')
"""
COLUMNS = """
select attrelid, attname from pg_attribute
where attrelid = any(%s) and attnum > 0 and not attisdropped and attgenerated = ''
order by attrelid, attnum
"""
PARENTS = "select conrelid, confrelid from pg_constraint where contype = 'f' and conrelid = any(%s)"
CLUSTER = """
select case when has_function_privilege('pg_control_system()', 'execute')
  then (select system_identifier::text from pg_control_system()) end
"""
SEQUENCES = """
select table_name, column_name, pg_get_serial_sequence(quote_ident(table_name), column_name)
from information_schema.columns
where table_schema = current_schema() and table_name = any(%s)
  and (is_identity = 'YES' or column_default like 'nextval(%%')
"""


async def export(conn: AsyncConnection, *, audio: bool = False) -> AsyncIterator[bytes]:
    """The database as gzipped chunks. `conn` must not already be in a transaction."""
    gz = zlib.compressobj(6, zlib.DEFLATED, 31)
    async with conn.transaction():
        await conn.execute("set transaction isolation level repeatable read, read only")
        tables = await _tables(conn, audio)
        migrations = [r[0] for r in await (await conn.execute(
            "select name from schema_migrations order by name")).fetchall()]
        cluster = (await (await conn.execute(CLUSTER)).fetchone())[0]
        head = {"format": FORMAT, "at": datetime.now(UTC).isoformat(), "cluster": cluster,
                "migrations": migrations, "audio": audio,
                "tables": [{"name": name, "columns": cols} for name, cols in tables]}
        yield gz.compress(json.dumps(head).encode() + b"\n")
        for name, cols in tables:
            target = _target(name, cols)
            yield gz.compress(sql.SQL("COPY {} FROM stdin;\n").format(target).as_string(conn).encode())
            async with conn.cursor() as cur, cur.copy(sql.SQL("COPY {} TO STDOUT").format(target)) as copy:
                async for data in copy:
                    if out := gz.compress(bytes(data)):
                        yield out
            yield gz.compress(END)
    yield gz.flush()


async def _tables(conn: AsyncConnection, audio: bool) -> list[tuple[str, list[str]]]:
    """Each table with its columns (not generated ones: they're computed again on load), parents first."""
    names = {oid: name for oid, name in await (await conn.execute(TABLES)).fetchall()
             if audio or name != AUDIO}
    oids = list(names)
    cols: dict[int, list[str]] = {oid: [] for oid in oids}
    for oid, col in await (await conn.execute(COLUMNS, (oids,))).fetchall():
        cols[oid].append(col)
    graph: dict[int, set[int]] = {oid: set() for oid in sorted(oids, key=names.get)}
    for child, parent in await (await conn.execute(PARENTS, (oids,))).fetchall():
        if child != parent and parent in names:
            graph[child].add(parent)
    return [(names[oid], cols[oid]) for oid in TopologicalSorter(graph).static_order()]


def _target(name: str, cols: list[str]) -> sql.Composed:
    return sql.SQL("{} ({})").format(sql.Identifier(name), sql.SQL(", ").join(map(sql.Identifier, cols)))


# Loading it


def lines(chunks: Iterable[bytes]) -> Iterator[bytes]:
    """Gzipped chunks as the lines they hold, each with its newline."""
    gz = zlib.decompressobj(31)
    rest = b""
    for chunk in chunks:
        rest += gz.decompress(chunk)
        *whole, rest = rest.split(b"\n")
        for line in whole:
            yield line + b"\n"
    rest += gz.flush()
    if rest:
        yield rest


def read_head(stream: Iterator[bytes]) -> dict:
    """The header line, checked."""
    try:
        head = json.loads(next(stream, b""))
    except ValueError:
        raise ValueError("not a Landline export") from None
    if not isinstance(head, dict) or head.get("format") != FORMAT:
        raise ValueError(f"not a {FORMAT} export")
    return head


def load(conn: psycopg.Connection, head: dict, stream: Iterator[bytes]) -> dict[str, int]:
    """Replace the export's tables in `conn`'s database with the export, in one transaction; returns the rows
    loaded per table. `stream` is what follows the header (see `lines`). Tables that point at a replaced one
    (and weren't exported, like call_audio without `audio`) are emptied too."""
    tables = {t["name"]: t["columns"] for t in head["tables"]}
    counts: dict[str, int] = {}
    with conn.transaction():
        if tables:
            names = sql.SQL(", ").join(map(sql.Identifier, tables))
            conn.execute(sql.SQL("truncate {} cascade").format(names))
        for line in stream:
            if not line.startswith(b"COPY "):
                raise ValueError(f"expected a COPY line, got {line[:60]!r}")
            name = next((n for n in tables if line.startswith(f'COPY "{n}" '.encode())), None)
            if name is None:
                raise ValueError(f"a table the header doesn't list: {line[:60]!r}")
            n = 0
            with conn.cursor() as cur, cur.copy(sql.SQL("COPY {} FROM STDIN").format(
                    _target(name, tables[name]))) as copy:
                for row in stream:
                    if row == END:
                        break
                    copy.write(row)
                    n += 1
                else:
                    raise ValueError(f"the export ends inside {name}")
            counts[name] = n
        # Copied ids leave the sequences behind; move each past the largest id, so new rows don't collide.
        for table, column, seq in conn.execute(SEQUENCES, (list(tables),)).fetchall():
            if seq:
                conn.execute(sql.SQL("select setval(%s::regclass, coalesce((select max({}) from {}), 0) + 1, "
                                     "false)").format(sql.Identifier(column), sql.Identifier(table)), (seq,))
    return counts
