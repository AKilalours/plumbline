"""Load deliveries into DuckDB so that no rerun, re-send or replay can duplicate, corrupt or resurrect data.

Four promises, each enforced in one place:

1. A file is loaded at most once. Its id is the SHA-256 of its bytes, recorded in the ledger
   in the same transaction as its rows. A crash rolls both back; a re-sent identical file is a no-op.
2. A row is stored at most once. Rows are normalized first, then hashed; a (key, row hash) pair
   already in bronze is skipped. So a partial re-send, or the same rows with different spacing or
   case, adds nothing.
3. Bad rows never load silently. Each goes to quarantine with a reason code. A delivery that lacks
   a required column is rejected whole: loading it would write NULLs over good values.
4. Deleted people stay deleted. A deletion request purges matching rows everywhere (bronze and
   quarantine) and blocks them in every later load, including replays of files sent before the request.

Storage is two tables. `bronze_<source>` holds each distinct row once. `delivery_rows` records every
row of every delivery, so we always know which files contained a row. Keeping them apart matters:
a corrected _v2 file that repeats 98% of its _v1 must still "contain" those rows.

Which row is current for a key: only rows contained in the latest loaded version of some file count
(a _v2 replaces its _v1). Among those, the newest by the source's `latest_by` columns wins, then the
row whose content arrived most recently. Re-delivering old content does not make it newer, so a
replayed old file cannot override a later correction.
"""
from __future__ import annotations

import csv
import fnmatch
import hashlib
import io
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pandas as pd

from .contracts import BadValue, SourceContract, email_hash, normalize, phone_hash

SQL_TYPES = {"date": "DATE", "timestamp": "TIMESTAMP", "int": "INTEGER", "float": "DOUBLE", "flag": "BOOLEAN"}
META_COLUMNS = ["_row_hash", "_first_delivery_id", "_first_version", "_first_seen_at", "_first_row_number"]
_VERSION = re.compile(r"_v(\d+)(?=\.csv$)")


@dataclass
class Delivery:
    source: str
    path: str                 # relative to the run directory
    delivered_at: datetime
    version: int
    group_key: str            # the logical file; versions of it share this


@dataclass
class LoadResult:
    source: str
    path: str
    delivery_id: str
    status: str               # loaded | skipped_seen_before | rejected
    rows_in: int = 0
    rows_loaded: int = 0
    rows_quarantined: int = 0
    rows_suppressed: int = 0
    rows_already_present: int = 0
    rows_purged: int = 0
    note: str = ""


@dataclass
class RunReport:
    results: list[LoadResult] = field(default_factory=list)

    def totals(self) -> dict:
        keys = ["rows_in", "rows_loaded", "rows_quarantined", "rows_suppressed", "rows_already_present", "rows_purged"]
        t = {k: sum(getattr(r, k) for r in self.results) for k in keys}
        for s in ("loaded", "skipped_seen_before", "rejected"):
            t[f"deliveries_{s}"] = sum(r.status == s for r in self.results)
        return t


# ------------------------------------------------------------------ schema
def _privacy_cols(c: SourceContract) -> list[str]:
    return [n for n, col in c.columns.items() if col.privacy]


def ensure_schema(con: duckdb.DuckDBPyConnection, contracts: dict[str, SourceContract]) -> None:
    con.execute("""CREATE TABLE IF NOT EXISTS ingest_ledger (
        delivery_id VARCHAR PRIMARY KEY, source VARCHAR, path VARCHAR, group_key VARCHAR, version INTEGER,
        delivered_at TIMESTAMP, loaded_at TIMESTAMP, status VARCHAR, rows_in INTEGER, rows_loaded INTEGER,
        rows_quarantined INTEGER, rows_suppressed INTEGER, rows_already_present INTEGER, note VARCHAR)""")
    con.execute("""CREATE TABLE IF NOT EXISTS quarantine (
        source VARCHAR, delivery_id VARCHAR, row_number INTEGER, reason VARCHAR, raw_json VARCHAR,
        sha_email VARCHAR, sha_phone VARCHAR)""")
    con.execute("""CREATE TABLE IF NOT EXISTS delivery_rows (
        source VARCHAR, delivery_id VARCHAR, row_number INTEGER, row_hash VARCHAR)""")
    con.execute("CREATE TABLE IF NOT EXISTS schema_drift (source VARCHAR, delivery_id VARCHAR, column_name VARCHAR, action VARCHAR)")
    con.execute("""CREATE TABLE IF NOT EXISTS suppression (
        request_id VARCHAR PRIMARY KEY, email_sha256 VARCHAR, phone_sha256 VARCHAR, received_date DATE, delivery_id VARCHAR)""")
    con.execute("""CREATE TABLE IF NOT EXISTS suppression_log (
        source VARCHAR, delivery_id VARCHAR, action VARCHAR, rows INTEGER, logged_at TIMESTAMP)""")
    for c in contracts.values():
        if c.role != "data":
            continue
        cols = [f'"{n}" {SQL_TYPES.get(col.type, "VARCHAR")}' for n, col in c.columns.items()]
        cols += [f'"_sha_{n}" VARCHAR' for n in _privacy_cols(c)]
        cols += ['"_row_hash" VARCHAR', '"_first_delivery_id" VARCHAR', '"_first_version" INTEGER',
                 '"_first_seen_at" TIMESTAMP', '"_first_row_number" INTEGER']
        con.execute(f'CREATE TABLE IF NOT EXISTS {c.table} ({", ".join(cols)})')
        _create_current_view(con, c)


def _create_current_view(con, c: SourceContract) -> None:
    key = ", ".join(f'"{k}"' for k in c.key)
    order = [f'"{k}" DESC NULLS LAST' for k in c.latest_by] + ["_first_seen_at DESC", "_first_version DESC",
                                                                "_first_row_number DESC"]
    sup = " AND ".join(
        f'(b."_sha_{n}" IS NULL OR b."_sha_{n}" NOT IN (SELECT {("email" if c.columns[n].privacy == "email" else "phone")}_sha256 '
        f'FROM suppression WHERE {("email" if c.columns[n].privacy == "email" else "phone")}_sha256 IS NOT NULL))'
        for n in _privacy_cols(c)) or "TRUE"
    con.execute(f"""CREATE OR REPLACE VIEW current_{c.name} AS
        WITH loaded AS (SELECT delivery_id, group_key, version FROM ingest_ledger
                        WHERE source = '{c.name}' AND status = 'loaded'),
        newest AS (SELECT l.delivery_id FROM loaded l
                   JOIN (SELECT group_key, max(version) AS v FROM loaded GROUP BY group_key) m
                   ON l.group_key = m.group_key AND l.version = m.v),
        live_hashes AS (SELECT DISTINCT row_hash FROM delivery_rows
                        WHERE source = '{c.name}' AND delivery_id IN (SELECT delivery_id FROM newest)),
        live AS (SELECT b.* FROM {c.table} b WHERE b._row_hash IN (SELECT row_hash FROM live_hashes) AND {sup}),
        ranked AS (SELECT *, row_number() OVER (PARTITION BY {key} ORDER BY {", ".join(order)}) AS _rn FROM live)
        SELECT * EXCLUDE (_rn) FROM ranked WHERE _rn = 1""")


# ------------------------------------------------------------------ discovery
def discover(run_dir: Path, contracts: dict[str, SourceContract], until: datetime | None = None) -> list[Delivery]:
    """Every file matching a contract's glob. Arrival time comes from deliveries.json when the
    sender provides one, otherwise from the file's modification time, as for any landing folder."""
    manifest = {}
    mf = run_dir / "deliveries.json"
    if mf.exists():
        manifest = {d["path"]: d for d in json.loads(mf.read_text())}
    found: list[Delivery] = []
    for c in contracts.values():
        for p in sorted(run_dir.glob(c.files)):
            rel = p.relative_to(run_dir).as_posix()
            if not fnmatch.fnmatch(rel, c.files):
                continue
            m = _VERSION.search(rel)
            version = int(m.group(1)) if m else 1
            group = _VERSION.sub("", rel)
            if rel in manifest:
                at = datetime.fromisoformat(manifest[rel]["delivered_at"])
            else:
                at = datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc).replace(tzinfo=None)
            if until is None or at <= until:
                found.append(Delivery(c.name, rel, at, version, group))
    return sorted(found, key=lambda d: (d.delivered_at, d.path))


# ------------------------------------------------------------------ loading
def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _row_hash(values: dict) -> str:
    return _sha(json.dumps(values, sort_keys=True, default=str).encode())


def _raw_hashes(c: SourceContract, raw: dict) -> tuple[str | None, str | None]:
    """Best-effort identifier hashes for a raw row, so quarantined rows can be purged on deletion."""
    e = p = None
    for n, col in c.columns.items():
        v = (raw.get(n) or "").strip()
        if not v:
            continue
        if col.privacy == "email":
            e = email_hash(v)
        elif col.privacy == "phone":
            d = re.sub(r"\D", "", v)
            d = d[1:] if len(d) == 11 and d.startswith("1") else d
            p = phone_hash(d) if len(d) == 10 else None
    return e, p


def _suppressed(con) -> tuple[set[str], set[str]]:
    rows = con.execute("SELECT email_sha256, phone_sha256 FROM suppression").fetchall()
    return {e for e, _ in rows if e}, {p for _, p in rows if p}


def load_delivery(con, c: SourceContract, run_dir: Path, d: Delivery,
                  contracts: dict[str, SourceContract]) -> LoadResult:
    """`contracts` is every source, needed because a deletion request purges all of them."""
    body = (run_dir / d.path).read_bytes()
    did = _sha(body)
    res = LoadResult(c.name, d.path, did, "loaded")
    if con.execute("SELECT 1 FROM ingest_ledger WHERE delivery_id = ?", [did]).fetchone():
        res.status = "skipped_seen_before"
        return res

    quarantine: list[tuple] = []
    drift: list[tuple] = []
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        return _reject(con, c, d, res, "not_utf8")
    reader = csv.reader(io.StringIO(text))
    try:
        header = [h.strip().lower() for h in next(reader)]
    except StopIteration:
        return _reject(con, c, d, res, "empty_file")
    mapped = []
    for h in header:
        if h in c.aliases:
            drift.append((c.name, did, h, f"renamed_to:{c.aliases[h]}"))
            h = c.aliases[h]
        mapped.append(h)
    if len(set(mapped)) != len(mapped):
        return _reject(con, c, d, res, "duplicate_columns")
    for h in mapped:
        if h not in c.columns:
            drift.append((c.name, did, h, "unknown_held_for_review"))
    missing = [n for n, col in c.columns.items() if n not in mapped and not col.optional]
    if missing:
        return _reject(con, c, d, res, "missing_columns:" + ",".join(missing), drift)

    sup_e, sup_p = _suppressed(con)

    def deleted(raw: dict) -> bool:
        # Checked before anything is stored, quarantine included: a deleted person's
        # broken row must not be kept just because it failed validation.
        e_h, p_h = _raw_hashes(c, raw)
        return (e_h is not None and e_h in sup_e) or (p_h is not None and p_h in sup_p)

    seen: dict[tuple, str] = {}
    out_rows: list[dict] = []
    for i, row in enumerate(reader, start=1):
        if not row:
            continue
        res.rows_in += 1
        raw = dict(zip(mapped, row))
        if deleted(raw):
            res.rows_suppressed += 1
            continue
        if len(row) != len(mapped):
            quarantine.append((c.name, did, i, "bad_field_count", json.dumps(row), None, None))
            continue
        vals, errors = {}, []
        for n, col in c.columns.items():
            try:
                vals[n] = normalize(col, raw.get(n, ""))
            except BadValue as e:
                errors.append(str(e))
        if errors:
            e_h, p_h = _raw_hashes(c, raw)
            quarantine.append((c.name, did, i, ";".join(errors), json.dumps(raw), e_h, p_h))
            continue
        # Deleted people were already stopped by deleted(raw) above; these hashes are stored so a
        # later deletion request can find and purge this row.
        shas = {n: (None if vals[n] is None else
                    email_hash(vals[n]) if c.columns[n].privacy == "email" else phone_hash(vals[n]))
                for n in _privacy_cols(c)}
        key = tuple(vals[k] for k in c.key)
        rh = _row_hash(vals)
        if key in seen:
            reason = "duplicate_in_delivery" if seen[key] == rh else "conflicting_key_in_delivery"
            quarantine.append((c.name, did, i, reason, json.dumps(raw), *_raw_hashes(c, raw)))
            continue
        seen[key] = rh
        rec = dict(vals)
        rec.update({f"_sha_{n}": h for n, h in shas.items()})
        rec.update({"_row_hash": rh, "_first_delivery_id": did, "_first_version": d.version,
                    "_first_seen_at": d.delivered_at, "_first_row_number": i})
        out_rows.append(rec)

    con.execute("BEGIN TRANSACTION")
    try:
        if c.role == "suppression":
            res.rows_loaded, res.rows_purged = _apply_suppression(con, did, out_rows, contracts)
        elif out_rows:
            members = pd.DataFrame({"source": c.name, "delivery_id": did,
                                    "row_number": [r["_first_row_number"] for r in out_rows],
                                    "row_hash": [r["_row_hash"] for r in out_rows]}).astype(object)
            con.register("members", members)
            con.execute("INSERT INTO delivery_rows SELECT source, delivery_id, row_number, row_hash FROM members")
            con.unregister("members")
            res.rows_already_present, res.rows_loaded = _insert_new(con, c, out_rows)
        res.rows_quarantined = len(quarantine)
        if quarantine:
            con.executemany("INSERT INTO quarantine VALUES (?, ?, ?, ?, ?, ?, ?)", quarantine)
        if drift:
            con.executemany("INSERT INTO schema_drift VALUES (?, ?, ?, ?)", drift)
        if res.rows_suppressed:
            con.execute("INSERT INTO suppression_log VALUES (?, ?, 'blocked_at_load', ?, now())",
                        [c.name, did, res.rows_suppressed])
        _ledger(con, d, res)
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return res


def _insert_new(con, c: SourceContract, rows: list[dict]) -> tuple[int, int]:
    cols = list(c.columns) + [f"_sha_{n}" for n in _privacy_cols(c)] + META_COLUMNS
    df = pd.DataFrame(rows, columns=cols).astype(object)
    df = df.where(pd.notna(df), None)
    con.register("incoming", df)
    before = con.execute(f"SELECT count(*) FROM {c.table}").fetchone()[0]
    quoted = ", ".join(f'"{x}"' for x in cols)
    con.execute(f"""INSERT INTO {c.table} ({quoted})
        SELECT {", ".join(f'i."{x}"' for x in cols)} FROM incoming i
        WHERE NOT EXISTS (SELECT 1 FROM {c.table} b WHERE b._row_hash = i._row_hash)""")
    con.unregister("incoming")
    added = con.execute(f"SELECT count(*) FROM {c.table}").fetchone()[0] - before
    return len(rows) - added, added


def _apply_suppression(con, did: str, rows: list[dict], contracts: dict[str, SourceContract]) -> tuple[int, int]:
    new = 0
    for r in rows:
        if not con.execute("SELECT 1 FROM suppression WHERE request_id = ?", [r["request_id"]]).fetchone():
            con.execute("INSERT INTO suppression VALUES (?, ?, ?, ?, ?)",
                        [r["request_id"], r.get("email_sha256"), r.get("phone_sha256"), r["received_date"], did])
            new += 1
    purged = 0
    for dc in contracts.values():
        if dc.role != "data":
            continue
        t, src = dc.table, dc.name
        conds = []
        for col in _privacy_cols(dc):
            kind = dc.columns[col].privacy
            conds.append(f'"_sha_{col}" IN (SELECT {kind}_sha256 FROM suppression WHERE {kind}_sha256 IS NOT NULL)')
        if not conds:
            continue
        n = con.execute(f"SELECT count(*) FROM {t} WHERE {' OR '.join(conds)}").fetchone()[0]
        if n:
            con.execute(f"DELETE FROM {t} WHERE {' OR '.join(conds)}")
            con.execute("INSERT INTO suppression_log VALUES (?, ?, 'purged', ?, now())", [src, did, n])
            purged += n
    q = con.execute("""SELECT count(*) FROM quarantine WHERE
        sha_email IN (SELECT email_sha256 FROM suppression) OR sha_phone IN (SELECT phone_sha256 FROM suppression)""").fetchone()[0]
    if q:
        con.execute("""DELETE FROM quarantine WHERE
            sha_email IN (SELECT email_sha256 FROM suppression) OR sha_phone IN (SELECT phone_sha256 FROM suppression)""")
        con.execute("INSERT INTO suppression_log VALUES ('quarantine', ?, 'purged', ?, now())", [did, q])
        purged += q
    return new, purged


def _reject(con, c, d, res, note, drift=()):
    res.status, res.note = "rejected", note
    con.execute("BEGIN TRANSACTION")
    if drift:
        con.executemany("INSERT INTO schema_drift VALUES (?, ?, ?, ?)", list(drift))
    _ledger(con, d, res)
    con.execute("COMMIT")
    return res


def _ledger(con, d: Delivery, r: LoadResult) -> None:
    con.execute("INSERT INTO ingest_ledger VALUES (?, ?, ?, ?, ?, ?, now(), ?, ?, ?, ?, ?, ?, ?)",
                [r.delivery_id, r.source, d.path, d.group_key, d.version, d.delivered_at, r.status, r.rows_in,
                 r.rows_loaded, r.rows_quarantined, r.rows_suppressed, r.rows_already_present, r.note])


# ------------------------------------------------------------------ run
def ingest(run_dir: str | Path, warehouse: str | Path, contracts: dict[str, SourceContract],
           until: datetime | None = None) -> RunReport:
    run_dir = Path(run_dir)
    con = duckdb.connect(str(warehouse))
    try:
        ensure_schema(con, contracts)
        report = RunReport()
        for d in discover(run_dir, contracts, until):
            report.results.append(load_delivery(con, contracts[d.source], run_dir, d, contracts))
        return report
    finally:
        con.close()


def report_json(report: RunReport) -> str:
    return json.dumps({"totals": report.totals(), "deliveries": [asdict(r) for r in report.results]}, indent=2, default=str)
