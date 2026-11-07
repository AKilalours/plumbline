"""A delivery-level quality gate: hold a whole delivery that breaks its source's normal shape.

Every row of a delivery can pass its contract while the delivery as a whole is wrong: a vendor sends half
its usual rows (a truncated export), a column that is usually filled arrives empty for 40% of people (a
broken join upstream), or far more rows than usual fail validation (a format change). Row checks cannot
see any of these; this gate can.

Each delivery of a source that opts in (a `gate:` block in its contract) is profiled before anything is
written: its row count, the share of rows quarantined, and for every contract column the share of rows
where it is empty. The profile is compared with the median of the last few loaded deliveries of the same
source and the same kind of file (a daily change file with earlier change files, not with the full extract).
A delivery outside its limits is **held**: nothing from it is stored, the ledger records it as held with
each reason in plain words, and a rerun does not retry it. A person looks at it and either releases it
(it then loads on the next run, and the release is recorded with who and why) or leaves it held and asks
the sender for a new file, which arrives as a new delivery and is checked like any other.

    python -m plumbline.ingest.gate list WAREHOUSE
    python -m plumbline.ingest.gate release WAREHOUSE DELIVERY_ID --by NAME --reason "vendor confirmed the drop"

    gate:
      baseline: 5          # compare with the median of the last 5 loaded deliveries of this source
      min_history: 3       # gate only once that many exist; the first deliveries set the baseline
      rows: [0.5, 2.0]     # allowed row count, as a ratio of the usual
      empty_jump: 0.25     # a column's empty share may rise at most 25 points over its usual
      quarantine_jump: 0.15
"""
from __future__ import annotations

import json
import re
import statistics
import sys
from datetime import datetime, timezone

DEFAULTS = {"baseline": 5, "min_history": 3, "rows": [0.5, 2.0], "empty_jump": 0.25, "quarantine_jump": 0.15}
KEYS = set(DEFAULTS)


def config(raw: dict | None) -> dict | None:
    if raw is None:
        return None
    unknown = set(raw) - KEYS
    if unknown:
        raise ValueError(f"unknown gate settings {sorted(unknown)}; allowed: {sorted(KEYS)}")
    g = {**DEFAULTS, **raw}
    lo, hi = g["rows"]
    if not (0 < lo < 1 < hi) or g["baseline"] < 1 or g["min_history"] < 1:
        raise ValueError("gate: rows must be [low < 1 < high], baseline and min_history at least 1")
    return g


def ensure(con) -> None:
    con.execute("""CREATE TABLE IF NOT EXISTS delivery_profiles (source VARCHAR, family VARCHAR, delivery_id VARCHAR, delivered_at TIMESTAMP,
        rows_in INTEGER, quarantine_share DOUBLE, empty_share VARCHAR, status VARCHAR, reasons VARCHAR, profiled_at TIMESTAMP)""")
    con.execute("""CREATE TABLE IF NOT EXISTS delivery_reviews (delivery_id VARCHAR, source VARCHAR, action VARCHAR,
        by_whom VARCHAR, reason VARCHAR, reviewed_at TIMESTAMP)""")


def profile(rows_in: int, quarantined: int, empties: dict[str, int]) -> dict:
    n = max(rows_in, 1)
    return {"rows_in": rows_in, "quarantine_share": round(quarantined / n, 4),
            "empty_share": {k: round(v / n, 4) for k, v in sorted(empties.items())}}


def family(path: str) -> str:
    """The kind of file, without its date, version or sequence numbers: a daily change file is compared with
    earlier change files, never with the full extract (crm_changes_2025-12-30.csv -> crm_changes_.csv)."""
    name = path.replace("\\", "/").rsplit("/", 1)[-1]
    return re.sub(r"\d[\d-]*", "", re.sub(r"_v\d+(?=\.)", "", name))


def baseline(con, source: str, fam: str, k: int) -> list[dict]:
    rows = con.execute("""SELECT rows_in, quarantine_share, empty_share FROM delivery_profiles
        WHERE source = ? AND family = ? AND status = 'loaded' ORDER BY delivered_at DESC, profiled_at DESC LIMIT ?""",
                       [source, fam, k]).fetchall()
    return [{"rows_in": r, "quarantine_share": q, "empty_share": json.loads(e)} for r, q, e in rows]


def check(p: dict, history: list[dict], g: dict) -> list[str]:
    """Plain-language reasons this delivery is outside its source's normal shape; empty if it is fine."""
    if len(history) < g["min_history"]:
        return []
    out = []
    usual = statistics.median(h["rows_in"] for h in history)
    lo, hi = g["rows"]
    if usual and not lo <= p["rows_in"] / usual <= hi:
        out.append(f"{p['rows_in']:,} rows against a usual {usual:,.0f} ({p['rows_in'] / usual:.2f}x, allowed {lo:g}x to {hi:g}x)")
    uq = statistics.median(h["quarantine_share"] for h in history)
    if p["quarantine_share"] - uq > g["quarantine_jump"]:
        out.append(f"{p['quarantine_share']:.0%} of rows failed validation against a usual {uq:.0%}")
    for col, share in p["empty_share"].items():
        prev = [h["empty_share"].get(col) for h in history if col in h["empty_share"]]
        if len(prev) < g["min_history"]:
            continue
        ue = statistics.median(prev)
        if share - ue > g["empty_jump"]:
            out.append(f"{col} empty in {share:.0%} of rows against a usual {ue:.0%}")
    return out


def released(con, delivery_id: str) -> bool:
    return bool(con.execute("SELECT 1 FROM delivery_reviews WHERE delivery_id = ? AND action = 'released'", [delivery_id]).fetchone())


def record(con, source: str, fam: str, delivery_id: str, delivered_at, p: dict, status: str, reasons: list[str]) -> None:
    con.execute("DELETE FROM delivery_profiles WHERE delivery_id = ?", [delivery_id])
    con.execute("INSERT INTO delivery_profiles VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, now())",
                [source, fam, delivery_id, delivered_at, p["rows_in"], p["quarantine_share"], json.dumps(p["empty_share"]),
                 status, json.dumps(reasons)])


def release(warehouse: str, delivery_id: str, by: str, reason: str) -> dict:
    """Let a held delivery load on the next run. A release must say who and why, and is recorded; a
    delivery that is not held cannot be released."""
    import duckdb
    if not by.strip() or not reason.strip():
        raise ValueError("a release needs who (--by) and why (--reason)")
    with duckdb.connect(str(warehouse)) as con:
        ensure(con)
        row = con.execute("SELECT source FROM ingest_ledger WHERE delivery_id = ? AND status = 'held'", [delivery_id]).fetchone()
        if not row:
            raise ValueError(f"{delivery_id[:12]}: not a held delivery")
        con.execute("BEGIN TRANSACTION")
        con.execute("INSERT INTO delivery_reviews VALUES (?, ?, 'released', ?, ?, ?)",
                    [delivery_id, row[0], by.strip(), reason.strip(), datetime.now(timezone.utc).replace(tzinfo=None)])
        con.execute("DELETE FROM ingest_ledger WHERE delivery_id = ?", [delivery_id])      # so the next run picks it up
        con.execute("COMMIT")
    return {"delivery_id": delivery_id, "source": row[0], "released_by": by.strip()}


def held(con) -> list[dict]:
    """Every held delivery with its reasons (works on a read-only connection)."""
    have = {r[0] for r in con.execute("SELECT table_name FROM information_schema.tables").fetchall()}
    if "delivery_profiles" not in have:
        return []
    rows = con.execute("""SELECT l.delivery_id, l.source, l.path, l.delivered_at, l.rows_in, p.reasons
        FROM ingest_ledger l LEFT JOIN delivery_profiles p USING (delivery_id) WHERE l.status = 'held'
        ORDER BY l.delivered_at""").fetchall()
    return [{"delivery_id": d, "source": s, "path": p, "delivered_at": str(t), "rows_in": n, "reasons": json.loads(r or "[]")}
            for d, s, p, t, n, r in rows]


def main(argv: list[str] | None = None) -> int:
    import argparse
    import duckdb
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    a1 = sub.add_parser("list")
    a1.add_argument("warehouse")
    a2 = sub.add_parser("release")
    a2.add_argument("warehouse")
    a2.add_argument("delivery_id")
    a2.add_argument("--by", required=True)
    a2.add_argument("--reason", required=True)
    a = ap.parse_args(argv)
    if a.cmd == "list":
        with duckdb.connect(a.warehouse, read_only=True) as con:
            for h in held(con):
                print(f"{h['delivery_id'][:12]}  {h['source']}  {h['path']}  {h['rows_in']:,} rows")
                for r in h["reasons"]:
                    print(f"    - {r}")
        return 0
    try:
        print(json.dumps(release(a.warehouse, a.delivery_id, a.by, a.reason)))
    except ValueError as e:
        print(f"not released: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
