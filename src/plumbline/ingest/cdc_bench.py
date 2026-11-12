"""How fast change capture keeps up, and what it costs the database while it does:

    python -m plumbline.ingest.cdc_bench --dsn "host=... dbname=..."      # writes docs/results/postgres_cdc_bench.json

For each burst size, against a real Postgres with wal_level=logical: commit that many changes (a third inserts,
a third updates, a third deletes, in transactions of 500), measure how much write-ahead log the unread slot now
holds back, then read it all with the connector in batches and time it: changes a second end to end (read,
decode, land, confirm), the slot's lag after each batch, and the time to catch up. The connector's starting
copy is timed too. Numbers are for the machine named in the file; they are a floor to compare against, not a
promise about another machine.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "docs" / "results" / "postgres_cdc_bench.json"


def _burst(con, n: int, start_id: int, tx: int = 500) -> int:
    """Commit about n changes (a third each of inserts, updates, deletes); returns how many exactly."""
    cur = con.cursor()
    con.autocommit = False
    third = n // 3
    ids = list(range(start_id, start_id + third))
    for i in range(0, third, tx):
        cur.executemany("INSERT INTO bench (id, email, plan, n) VALUES (%s, %s, 'free', 0)",
                        [(k, f"user{k}@bench.test") for k in ids[i:i + tx]])
        con.commit()
    for i in range(0, third, tx):
        cur.execute("UPDATE bench SET plan = 'pro', n = n + 1 WHERE id >= %s AND id < %s", (ids[i], ids[i] + tx))
        con.commit()
    for i in range(0, third, tx):
        cur.execute("DELETE FROM bench WHERE id >= %s AND id < %s", (ids[i], ids[i] + tx))
        con.commit()
    con.autocommit = True
    return 3 * third


def run(dsn: str, sizes=(10_000, 100_000), batch: int = 10_000, seed_rows: int = 50_000, out: Path = OUT) -> dict:
    import psycopg2
    from .connectors.postgres_cdc import PostgresCDC
    con = psycopg2.connect(dsn)
    con.autocommit = True
    cur = con.cursor()
    cur.execute("SHOW server_version")
    version = cur.fetchone()[0]
    cur.execute("DROP TABLE IF EXISTS bench; CREATE TABLE bench (id bigint PRIMARY KEY, email text, plan text, n int, "
                "updated_at timestamptz DEFAULT now())")
    cur.execute("INSERT INTO bench (id, email, plan, n) SELECT g, 'seed' || g || '@bench.test', 'free', 0 FROM generate_series(1, %s) g",
                (seed_rows,))
    results, slot = [], f"pl_bench_{uuid.uuid4().hex[:8]}"
    try:
        with tempfile.TemporaryDirectory() as tmp:
            cdc = PostgresCDC(dsn, slot, ["public.bench"], Path(tmp) / "sources")
            t = time.perf_counter()
            cdc.start()
            copy_s = time.perf_counter() - t
            next_id = seed_rows + 1
            for n in sizes:
                t = time.perf_counter()
                n = _burst(con, n, next_id)
                write_s = time.perf_counter() - t
                next_id += n
                cur.execute("SELECT pg_wal_lsn_diff(pg_current_wal_lsn(), confirmed_flush_lsn)::bigint FROM pg_replication_slots "
                            "WHERE slot_name = %s", (slot,))
                held = int(cur.fetchone()[0])
                t, read, lags, polls = time.perf_counter(), 0, [], 0
                while True:
                    r = cdc.poll(max_changes=batch)
                    polls += 1
                    read += r.changes
                    lags.append(r.lag_bytes)
                    if r.changes == 0:
                        break
                catch_s = time.perf_counter() - t
                if read != n:
                    raise AssertionError(f"committed {n:,} changes, read {read:,}")
                results.append({"changes": n, "read": read, "write_seconds": round(write_s, 2), "catch_up_seconds": round(catch_s, 2),
                                "changes_per_second": round(read / max(catch_s, 1e-9)), "log_held_back_mb": round(held / 2**20, 1),
                                "polls": polls, "lag_after_mb": [round((x or 0) / 2**20, 2) for x in lags]})
    finally:
        cur.execute("SELECT pg_drop_replication_slot(slot_name) FROM pg_replication_slots WHERE slot_name = %s", (slot,))
        cur.execute("DROP TABLE IF EXISTS bench")
        con.close()
    rec = {"measured_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "postgres": version,
           "machine": f"{platform.machine()}, {os.cpu_count()} cores, {platform.system()}", "batch": batch,
           "starting_copy": {"rows": seed_rows, "seconds": round(copy_s, 2), "rows_per_second": round(seed_rows / max(copy_s, 1e-9))},
           "bursts": results}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rec, indent=1))
    return rec


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dsn", required=True)
    ap.add_argument("--out", type=Path, default=OUT)
    a = ap.parse_args(argv)
    print(json.dumps(run(a.dsn, out=a.out), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
