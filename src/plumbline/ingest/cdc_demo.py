"""Record one real change-capture run for the app to replay:  python -m plumbline.ingest.cdc_demo --dsn ...

Against a real Postgres with wal_level=logical: the shop database (store/shop_db.py) is set up, the starting
copy taken, the day's transactions committed, a crash injected after landing and before confirming, the
changes read again, everything loaded under the contracts in sources/databases/. Written to
docs/results/postgres_cdc.json: the copy, every change in log order, the crash and its recovery, the slot's
lag, and the current view at the end beside what the database itself holds. The app replays this file (the
public app has no database to connect to); the `postgres` CI job runs the same steps on every push.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "docs" / "results" / "postgres_cdc.json"


def record(dsn: str, out: Path = OUT) -> dict:
    import duckdb
    import psycopg2

    from ..store import shop_db
    from .connectors.postgres_cdc import PostgresCDC
    from .contracts import load_contracts
    from .engine import ingest
    con = psycopg2.connect(dsn)
    shop_db.setup(con)
    cur = con.cursor()
    cur.execute("SHOW server_version")
    version = cur.fetchone()[0]
    slot = f"pl_demo_{uuid.uuid4().hex[:8]}"
    try:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            cdc = PostgresCDC(dsn, slot, ["public.customers", "public.orders"], tmp / "run" / "sources")
            st = cdc.start()
            shop_db.play(con)
            cur.execute("SELECT pg_wal_lsn_diff(pg_current_wal_lsn(), confirmed_flush_lsn)::bigint FROM pg_replication_slots WHERE slot_name = %s", (slot,))
            held_back = int(cur.fetchone()[0])

            def crash(_):
                raise RuntimeError("killed after landing, before confirming")
            try:
                cdc.poll(after_write=crash)
            except RuntimeError:
                pass
            again = cdc.poll()
            ingest(tmp / "run", tmp / "w.duckdb", load_contracts(ROOT / "sources" / "databases"))
            lake = duckdb.connect()
            changes = lake.execute(f"""
                SELECT DISTINCT _op, _lsn, _lsn_text, _xid, id, regexp_extract(filename, 'pg_[a-z]+') AS source,
                       email, plan, status FROM read_csv_auto('{tmp}/run/sources/pg_*/*.csv', union_by_name=true, filename=true,
                                                              all_varchar=true)
                ORDER BY CAST(_lsn AS BIGINT), source, id""").fetchall()
            wh = duckdb.connect(str(tmp / "w.duckdb"), read_only=True)
            current = {"customers": wh.execute("SELECT id, first_name, last_name, email, plan FROM current_pg_customers ORDER BY id").fetchall(),
                       "orders": wh.execute("SELECT id, customer_id, total, status FROM current_pg_orders ORDER BY id").fetchall()}
            bronze = {s: wh.execute(f"SELECT count(*) FROM bronze_pg_{s}").fetchone()[0] for s in ("customers", "orders")}
            quarantined = wh.execute("SELECT count(*) FROM quarantine").fetchone()[0]
            wh.close()
            cur.execute("SELECT id, first_name, last_name, email, plan FROM customers ORDER BY id")
            db_c = cur.fetchall()
            cur.execute("SELECT id, customer_id, total::float, status FROM orders ORDER BY id")
            db_o = cur.fetchall()
    finally:
        con.autocommit = True
        con.cursor().execute("SELECT pg_drop_replication_slot(slot_name) FROM pg_replication_slots WHERE slot_name = %s", (slot,))
        con.close()
    rec = {
        "recorded_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "postgres": version,
        "slot_point": st["consistent_point"], "copied": st["copied"], "log_held_back_bytes_before_reading": held_back,
        "changes": [{"op": o, "lsn": int(l), "lsn_text": lt, "xid": x, "id": int(i), "source": s,
                     "email": e, "plan": p, "status": stt} for o, l, lt, x, i, s, e, p, stt in changes],
        "crash": {"read_again": again.changes, "transactions": again.transactions, "by_op": again.by_op,
                  "lag_bytes_after": again.lag_bytes},
        "bronze_rows": bronze, "quarantined": quarantined,
        "current": {k: [list(map(_plain, r)) for r in v] for k, v in current.items()},
        "database": {"customers": [list(map(_plain, r)) for r in db_c], "orders": [list(map(_plain, r)) for r in db_o]},
    }
    rec["matches_database"] = rec["current"] == rec["database"]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rec, indent=1))
    return rec


def _plain(v):
    if v is None or isinstance(v, (int, str)):
        return v
    try:
        return float(v)
    except (TypeError, ValueError):
        return str(v)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dsn", required=True)
    ap.add_argument("--out", type=Path, default=OUT)
    a = ap.parse_args(argv)
    r = record(a.dsn, a.out)
    print(json.dumps({k: r[k] for k in ("postgres", "copied", "crash", "bronze_rows", "quarantined", "matches_database")}, indent=1))
    return 0 if r["matches_database"] and not r["quarantined"] else 1


if __name__ == "__main__":
    sys.exit(main())
