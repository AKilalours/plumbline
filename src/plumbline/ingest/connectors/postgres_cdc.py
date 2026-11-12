"""A Postgres database as a source, by change data capture: its own change log, not repeated table copies.

    cdc = PostgresCDC(dsn, slot="plumbline", tables=["public.customers", "public.orders"], landing=root / "sources")
    cdc.start()          # first time: a consistent copy of each table at the slot's starting point
    cdc.poll()           # every run after: the inserts, updates and deletes since the last confirmed change

How it stays correct, in the order a real CDC tool (Debezium, Fivetran's log-based sync) does it:

1. **A starting copy that lines up with the log.** A logical replication slot is the database's bookmark in its
   write-ahead log: it keeps every change after a point until a reader confirms it. The slot is created with
   EXPORT_SNAPSHOT, and the tables are copied inside that exact snapshot, so the copy holds everything up to
   the slot's point and the log holds everything after it: nothing missed at the handover, nothing twice.
2. **Land, then confirm.** Changes are read with pg_logical_slot_peek_changes, which does not consume them; the
   batch is written atomically; only then is the slot advanced past it. A crash between the two reads the same
   changes again on restart, and they land again with the same log positions, so the warehouse keeps one copy.
3. **Every change carries its log position** (`_lsn`, a number that only grows), and the contract's
   `latest_by: [_lsn]` makes the newest change win; `deleted_when` drops a key whose newest change is a delete.
   Row versions are ordered by the database's log, not by a clock that can skew.
4. **Not consuming is not free:** a slot nobody reads keeps the database's log on disk. `lag_bytes` reports how
   much the slot is holding back, so a stalled reader is visible before the disk fills.

The log is decoded with test_decoding, Postgres's built-in plugin (no extension to install); `parse` reads its
text format.
"""
from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .store_api import _atomic_write

_TABLE = re.compile(r"^table ([^:]+): (INSERT|UPDATE|DELETE):(?: (.*))?$")
_FIELD = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\[([^\]]+)\]:('(?:[^']|'')*'|\S+)")
OPS = {"INSERT": "c", "UPDATE": "u", "DELETE": "d"}


def lsn_int(lsn: str) -> int:
    """'16/B374D848' -> one integer that orders like the log."""
    hi, lo = lsn.split("/")
    return (int(hi, 16) << 32) + int(lo, 16)


def _utc(v) -> str | None:
    """A timestamp from either path as UTC text: the copy gives datetimes ("-05:00"), the log gives text ("-05")."""
    from datetime import datetime, timezone
    if v is None or v == "":
        return None
    if isinstance(v, str):
        t = v.strip()
        if re.search(r"[+-]\d\d$", t):
            t += ":00"
        v = datetime.fromisoformat(t)
    if v.tzinfo is not None:
        v = v.astimezone(timezone.utc).replace(tzinfo=None)
    return v.isoformat(sep=" ")


def parse(data: str) -> tuple[str, str, dict] | None:
    """One test_decoding line -> (table, op, {column: value}); None for BEGIN/COMMIT.

    table public.customers: UPDATE: id[integer]:7 email[text]:'ann@x.test' note[text]:null
    """
    m = _TABLE.match(data)
    if not m:
        return None
    table, op, rest = m.group(1), m.group(2), m.group(3) or ""
    row = {}
    for name, type_, raw in _FIELD.findall(rest):
        if raw == "null":
            row[name] = None
        elif raw.startswith("'"):
            row[name] = raw[1:-1].replace("''", "'")
        else:
            row[name] = raw
        if type_.startswith("timestamp") and row[name] is not None:
            row[name] = _utc(row[name])
    return table.replace('"', ""), OPS[op], row


@dataclass
class PollResult:
    changes: int = 0
    transactions: int = 0
    batches: int = 0
    by_op: dict = field(default_factory=dict)
    by_table: dict = field(default_factory=dict)
    first_lsn: str | None = None
    last_lsn: str | None = None
    lag_bytes: int | None = None


class PostgresCDC:
    def __init__(self, dsn: str, slot: str, tables: list[str], landing: str | Path, connect=None):
        self.dsn, self.slot, self.tables, self.landing = dsn, slot, tables, Path(landing)
        self._connect = connect
        self.state_path = self.landing / f"_cdc_{slot}.json"

    # -- plumbing
    def _con(self, **kw):
        import psycopg2
        return (self._connect or psycopg2.connect)(self.dsn, **kw)

    def _state(self) -> dict:
        return json.loads(self.state_path.read_text()) if self.state_path.exists() else {"slot": self.slot, "next_batch": {}}

    def _save(self, st: dict) -> None:
        self.landing.mkdir(parents=True, exist_ok=True)
        _atomic_write(self.state_path, json.dumps(st, indent=1))

    @staticmethod
    def name(table: str) -> str:
        """public.customers -> pg_customers: the source name its contract and landing folder use."""
        return "pg_" + table.split(".")[-1]

    def _land(self, st: dict, table: str, rows: list[dict], columns: list[str]) -> None:
        n = self.name(table)
        st["next_batch"][n] = st["next_batch"].get(n, 0) + 1
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=["_op", "_lsn", "_lsn_text", "_xid"] + columns, extrasaction="ignore",
                           lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
        folder = self.landing / n
        folder.mkdir(parents=True, exist_ok=True)
        _atomic_write(folder / f"{n}_c{st['next_batch'][n]:06d}.csv", buf.getvalue())

    def _columns(self, cur, table: str) -> list[str]:
        schema, t = table.split(".")
        cur.execute("""SELECT column_name FROM information_schema.columns WHERE table_schema = %s AND table_name = %s
                       ORDER BY ordinal_position""", (schema, t))
        return [r[0] for r in cur.fetchall()]

    # -- the two operations
    def start(self, after_slot: Callable[[], None] | None = None) -> dict:
        """Create the slot and copy each table inside the slot's exported snapshot. Safe to call again: an existing
        slot means the copy was taken, and nothing is done."""
        from psycopg2.extras import LogicalReplicationConnection
        st = self._state()
        if st.get("consistent_point"):
            return st
        repl = self._con(connection_factory=LogicalReplicationConnection)
        try:
            rc = repl.cursor()
            rc.execute(f"CREATE_REPLICATION_SLOT {self.slot} LOGICAL test_decoding EXPORT_SNAPSHOT")
            slot_name, point, snapshot, _plugin = rc.fetchone()
            if after_slot:
                after_slot()                    # tests commit a change here: it must be in the log, not the copy
            con = self._con()
            try:
                con.set_session(isolation_level="REPEATABLE READ", readonly=True)
                cur = con.cursor()
                cur.execute("SET TRANSACTION SNAPSHOT %s", (snapshot,))   # see the tables exactly as of `point`
                copied = {}
                for t in self.tables:
                    cols = self._columns(cur, t)
                    cur.execute(f"SELECT {', '.join(cols)} FROM {t}")
                    # one before the slot's point: the first change after it can sit at the point itself, and a
                    # copy row tied with a change to the same key would leave "newest" to chance
                    rows = [{"_op": "r", "_lsn": lsn_int(point) - 1, "_lsn_text": point, "_xid": "",
                             **{c: (_utc(v) if hasattr(v, "tzinfo") else v) for c, v in zip(cols, r)}}
                            for r in cur.fetchall()]
                    self._land(st, t, rows, cols)
                    copied[t] = len(rows)
                    st.setdefault("columns", {})[t] = cols
                con.rollback()
            finally:
                con.close()
        finally:
            repl.close()                        # the exported snapshot lives only while this connection is open
        st.update(consistent_point=point, copied=copied)
        self._save(st)
        return st

    def poll(self, max_changes: int = 10_000, after_write: Callable[[int], None] | None = None) -> PollResult:
        """Read up to max_changes since the last confirmed position (whole transactions), land them, then advance."""
        st = self._state()
        if not st.get("consistent_point"):
            raise RuntimeError("start() first: the starting copy has not been taken")
        res = PollResult()
        con = self._con()
        con.autocommit = True
        try:
            cur = con.cursor()
            cur.execute("SELECT lsn::text, xid::text, data FROM pg_logical_slot_peek_changes(%s, NULL, %s)",
                        (self.slot, max_changes))
            rows, last_commit, xid_seen = {}, None, set()
            for lsn, xid, data in cur.fetchall():
                if data.startswith("COMMIT"):
                    last_commit = lsn
                    continue
                p = parse(data)
                if p is None:
                    continue
                table, op, values = p
                rows.setdefault(table, []).append({"_op": op, "_lsn": lsn_int(lsn), "_lsn_text": lsn, "_xid": xid, **values})
                xid_seen.add(xid)
                res.changes += 1
                res.by_op[op] = res.by_op.get(op, 0) + 1
                res.by_table[table] = res.by_table.get(table, 0) + 1
                res.first_lsn = res.first_lsn or lsn
                res.last_lsn = lsn
            res.transactions = len(xid_seen)
            for table, rs in rows.items():
                cols = st.get("columns", {}).get(table) or self._columns(cur, table)
                extra = [c for r in rs for c in r if not c.startswith("_") and c not in cols]
                cols = cols + list(dict.fromkeys(extra))         # a new column is landed, the contract decides on it
                self._land(st, table, rs, cols)
                res.batches += 1
            self._save(st)
            if after_write:
                after_write(res.batches)                       # tests crash here: landed, not yet confirmed
            if last_commit:
                cur.execute("SELECT pg_replication_slot_advance(%s, %s::pg_lsn)", (self.slot, last_commit))
            cur.execute("""SELECT pg_wal_lsn_diff(pg_current_wal_lsn(), confirmed_flush_lsn)::bigint
                           FROM pg_replication_slots WHERE slot_name = %s""", (self.slot,))
            r = cur.fetchone()
            res.lag_bytes = int(r[0]) if r and r[0] is not None else None
        finally:
            con.close()
        return res

    def drop(self) -> None:
        """Remove the slot (it would otherwise keep the database's log forever)."""
        con = self._con()
        con.autocommit = True
        try:
            con.cursor().execute("SELECT pg_drop_replication_slot(%s) FROM pg_replication_slots WHERE slot_name = %s",
                                 (self.slot, self.slot))
        finally:
            con.close()
