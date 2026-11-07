"""Publish landed tables to a cloud warehouse: MotherDuck (DuckDB in the cloud, so the SQL here runs unchanged).

    python -m plumbline.products.publish WAREHOUSE --table current_dob_now_permits:source_row_id:source_updated_at

Each daily run lands only what changed since the last one, so the cloud copy is built by upsert, not replace:
- the newest version of each key in this run replaces the cloud's version of that key (delete, then insert,
  in one transaction: a failure leaves the cloud as it was);
- running the same publish twice changes nothing (rows are matched by key, never appended blindly);
- a column that appears in the source is added to the cloud table first; a column the cloud has and this run
  does not is left alone and filled with NULL for the new rows;
- after the write, the cloud is checked: every key from this run is there exactly once, and holds this run's
  version. A mismatch raises, so the job goes red.
- a log table (_publish_log) in the cloud database records every publish.

The target is `md:<database>` for MotherDuck (needs MOTHERDUCK_TOKEN in the environment; never written
anywhere) or a file path, which is what the tests use.
"""
from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import duckdb

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class PublishError(RuntimeError):
    pass


@dataclass(frozen=True)
class Table:
    name: str
    key: str
    latest_by: str


def _q(x: str) -> str:
    if not _IDENT.match(x):
        raise PublishError(f"not a plain identifier: {x!r}")
    return f'"{x}"'


def connect(target: str):
    """A connection that can write the target, and the catalog name to write into.

    MotherDuck reads its token only when a connection opens (it cannot be given to ATTACH), so for md: the
    connection is opened on MotherDuck and the local warehouse is attached to it, read-only."""
    if target.startswith("md:"):
        db = target[3:] or "plumbline"
        _q(db)
        token = os.environ.get("MOTHERDUCK_TOKEN") or os.environ.get("motherduck_token")
        if not token:
            raise PublishError("MOTHERDUCK_TOKEN is not set: nothing was published")
        os.environ["motherduck_token"] = token          # the name the extension reads; never printed or saved
        con = duckdb.connect("md:")
        con.execute(f"CREATE DATABASE IF NOT EXISTS {_q(db)}")
        return con, db
    con = duckdb.connect()
    con.execute(f"ATTACH '{Path(target).as_posix()}' AS cloud")
    return con, "cloud"


def _columns(con, catalog: str, table: str) -> dict[str, str]:
    rows = con.execute("SELECT column_name, data_type FROM information_schema.columns WHERE table_catalog = ? "
                       "AND table_schema = 'main' AND table_name = ? ORDER BY ordinal_position", [catalog, table]).fetchall()
    return dict(rows)


def upsert(con, catalog: str, t: Table, run: str) -> dict:
    tq, kq, lq = _q(t.name), _q(t.key), _q(t.latest_by)
    con.execute(f"""CREATE OR REPLACE TEMP TABLE _batch AS SELECT * FROM src.main.{tq}
        QUALIFY row_number() OVER (PARTITION BY {kq} ORDER BY {lq} DESC NULLS LAST) = 1""")
    n = con.execute("SELECT count(*) FROM _batch").fetchone()[0]
    src = _columns(con, "temp", "_batch") or dict(con.execute("SELECT column_name, data_type FROM (DESCRIBE _batch)").fetchall())
    dst = f"{catalog}.main.{tq}"
    have = _columns(con, catalog, t.name)
    added = []
    con.execute("BEGIN")
    try:
        if not have:
            con.execute(f"CREATE TABLE {dst} AS SELECT * FROM _batch LIMIT 0")
        else:
            for c, typ in src.items():
                if c not in have:
                    con.execute(f"ALTER TABLE {dst} ADD COLUMN {_q(c)} {typ}")
                    added.append(c)
        replaced = con.execute(f"SELECT count(*) FROM {dst} WHERE {kq} IN (SELECT {kq} FROM _batch)").fetchone()[0]
        con.execute(f"DELETE FROM {dst} WHERE {kq} IN (SELECT {kq} FROM _batch)")
        con.execute(f"INSERT INTO {dst} BY NAME SELECT * FROM _batch")
        con.execute(f"""CREATE TABLE IF NOT EXISTS {catalog}.main._publish_log (published_at TIMESTAMP, run VARCHAR,
            table_name VARCHAR, rows_in_run BIGINT, rows_replaced BIGINT, rows_new BIGINT, rows_after BIGINT, columns_added VARCHAR)""")
        after = con.execute(f"SELECT count(*) FROM {dst}").fetchone()[0]
        con.execute(f"INSERT INTO {catalog}.main._publish_log VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    [datetime.now(timezone.utc).replace(tzinfo=None), run, t.name, n, replaced, n - replaced, after, ",".join(added)])
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    # the check: every key of this run is in the cloud once, holding this run's version
    bad = con.execute(f"""SELECT count(*) FROM _batch b LEFT JOIN (
            SELECT {kq}, count(*) AS n, max({lq}) AS v FROM {dst} WHERE {kq} IN (SELECT {kq} FROM _batch) GROUP BY {kq}) c
            USING ({kq}) WHERE c.n IS DISTINCT FROM 1 OR c.v IS DISTINCT FROM b.{lq}""").fetchone()[0]
    if bad:
        raise PublishError(f"{t.name}: {bad} keys from this run are missing, doubled or stale in the cloud copy")
    return {"table": t.name, "rows_in_run": n, "rows_replaced": replaced, "rows_new": n - replaced, "rows_after": after,
            "columns_added": added}


def publish(warehouse: str | Path, tables: list[Table], target: str, run: str = "") -> dict:
    """Copy the newest version of each key in `tables` from the local warehouse into the target."""
    con, catalog = connect(target)
    try:
        con.execute(f"ATTACH '{Path(warehouse).as_posix()}' AS src (READ_ONLY)")
        out = [upsert(con, catalog, t, run) for t in tables]
        return {"target": target if target.startswith("md:") else "file", "tables": out}
    finally:
        con.close()


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("warehouse")
    ap.add_argument("--target", default="md:plumbline")
    ap.add_argument("--table", action="append", required=True, help="name:key:latest_by")
    a = ap.parse_args(argv)
    try:
        r = publish(a.warehouse, [Table(*x.split(":")) for x in a.table], a.target)
    except PublishError as e:
        print(f"not published: {e}", file=sys.stderr)
        return 1
    print(json.dumps(r, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
