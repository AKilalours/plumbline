"""Change data capture from Postgres: a starting copy that lines up with the log, every insert, update and delete
after it, land before confirm, and a current view that ends exactly where the database ends.

The parser and loader tests run everywhere. The rest need a real Postgres with wal_level=logical, given by
PLUMBLINE_PG_DSN (the `postgres` job in .github/workflows/tests.yml starts one)."""
from __future__ import annotations

import os
import uuid
from pathlib import Path

import duckdb
import pytest

from plumbline.ingest.connectors.postgres_cdc import PostgresCDC, lsn_int, parse
from plumbline.ingest.contracts import load_contracts
from plumbline.ingest.engine import ingest

ROOT = Path(__file__).resolve().parents[1]
DSN = os.environ.get("PLUMBLINE_PG_DSN")
needs_pg = pytest.mark.skipif(not DSN, reason="needs a real Postgres with wal_level=logical (PLUMBLINE_PG_DSN)")


# ------------------------------------------------------------------ everywhere
def test_the_log_format_is_read_with_quotes_nulls_and_time_zones():
    t, op, row = parse("table public.customers: UPDATE: id[integer]:4 last_name[text]:'O''Neil' zip[text]:null "
                       "updated_at[timestamp with time zone]:'2026-11-12 07:43:46.32-05'")
    assert (t, op) == ("public.customers", "u")
    assert row == {"id": "4", "last_name": "O'Neil", "zip": None, "updated_at": "2026-11-12 12:43:46.320000"}
    assert parse("table public.orders: DELETE: id[integer]:102") == ("public.orders", "d", {"id": "102"})
    assert parse("BEGIN 749") is None and parse("COMMIT 749") is None


def test_log_positions_order_like_the_log():
    assert lsn_int("0/1564C90") < lsn_int("0/1565618") < lsn_int("1/0")


def _load(tmp: Path, csvs: dict[str, str]) -> duckdb.DuckDBPyConnection:
    for name, text in csvs.items():
        d = tmp / "run" / "sources" / name.split("_c0")[0]
        d.mkdir(parents=True, exist_ok=True)
        (d / name).write_text(text)
    ingest(tmp / "run", tmp / "w.duckdb", load_contracts(ROOT / "sources" / "databases"))
    return duckdb.connect(str(tmp / "w.duckdb"))


HEAD = "_op,_lsn,_lsn_text,_xid,id,customer_id,total,status,created_at\n"


def test_two_versions_of_one_key_in_one_batch_are_both_kept_and_the_later_wins(tmp_path):
    """Order 101 paid then shipped in one batch: the file rule called the second a conflicting key, quarantined
    it, and the current view said "paid"."""
    con = _load(tmp_path, {"pg_orders_c000001.csv": HEAD + "u,100,0/64,1,101,1,120.00,paid,\nu,200,0/C8,2,101,1,120.00,shipped,\n"})
    assert con.execute("SELECT status FROM current_pg_orders WHERE id = 101").fetchone()[0] == "shipped"
    assert con.execute("SELECT count(*) FROM bronze_pg_orders").fetchone()[0] == 2
    assert con.execute("SELECT count(*) FROM quarantine").fetchone()[0] == 0


def test_two_different_rows_at_the_same_log_position_still_conflict(tmp_path):
    con = _load(tmp_path, {"pg_orders_c000001.csv": HEAD + "u,100,0/64,1,101,1,120.00,paid,\nu,100,0/64,1,101,1,99.00,paid,\n"})
    assert con.execute("SELECT reason FROM quarantine").fetchall() == [("conflicting_key_in_delivery",)]


def test_a_delete_as_the_newest_change_removes_the_key_and_stays_in_history(tmp_path):
    con = _load(tmp_path, {"pg_orders_c000001.csv": HEAD + "r,99,0/63,,102,3,15.00,placed,\nd,300,0/12C,3,102,,,,\n"})
    assert con.execute("SELECT count(*) FROM current_pg_orders").fetchone()[0] == 0
    assert con.execute("SELECT _op FROM bronze_pg_orders ORDER BY _lsn").fetchall() == [("r",), ("d",)]


def test_a_delete_that_is_not_the_newest_change_does_not_remove_a_key_inserted_again(tmp_path):
    con = _load(tmp_path, {"pg_orders_c000001.csv": HEAD + "d,300,0/12C,3,102,,,,\nc,400,0/190,4,102,3,15.00,placed,\n"})
    assert con.execute("SELECT status FROM current_pg_orders WHERE id = 102").fetchone()[0] == "placed"


# ------------------------------------------------------------------ real Postgres
@pytest.fixture
def db():
    psycopg2 = pytest.importorskip("psycopg2")
    from plumbline.store import shop_db
    con = psycopg2.connect(DSN)
    shop_db.setup(con)
    slot = f"pl_{uuid.uuid4().hex[:10]}"
    yield con, slot, shop_db
    con.autocommit = True
    con.cursor().execute("SELECT pg_drop_replication_slot(slot_name) FROM pg_replication_slots WHERE slot_name = %s", (slot,))
    con.close()


def _cdc(tmp, slot):
    return PostgresCDC(DSN, slot, ["public.customers", "public.orders"], tmp / "run" / "sources")


def _current(tmp):
    ingest(tmp / "run", tmp / "w.duckdb", load_contracts(ROOT / "sources" / "databases"))
    con = duckdb.connect(str(tmp / "w.duckdb"), read_only=True)
    cust = dict(con.execute("SELECT id, email FROM current_pg_customers").fetchall())
    plans = dict(con.execute("SELECT id, plan FROM current_pg_customers").fetchall())
    orders = dict(con.execute("SELECT id, status FROM current_pg_orders").fetchall())
    q = con.execute("SELECT count(*) FROM quarantine").fetchone()[0]
    con.close()
    return cust, plans, orders, q


@needs_pg
def test_a_day_of_changes_ends_exactly_where_the_database_ends(db, tmp_path):
    con, slot, shop = db
    cdc = _cdc(tmp_path, slot)
    assert cdc.start()["copied"] == {"public.customers": 4, "public.orders": 3}
    shop.play(con)
    r = cdc.poll()
    assert r.by_op == {"c": 3, "u": 5, "d": 3} and r.transactions == 8 and r.lag_bytes == 0
    assert cdc.poll().changes == 0                                    # confirmed: nothing is read twice
    cust, plans, orders, q = _current(tmp_path)
    assert cust == shop.END_CUSTOMERS and plans == shop.END_PLANS and orders == shop.END_ORDERS and q == 0


@needs_pg
def test_a_crash_after_landing_before_confirming_reads_the_same_changes_again_and_keeps_one_copy(db, tmp_path):
    con, slot, shop = db
    cdc = _cdc(tmp_path, slot)
    cdc.start()
    shop.play(con)

    def crash(batches):
        raise RuntimeError("killed after landing, before the slot was advanced")
    with pytest.raises(RuntimeError, match="killed"):
        cdc.poll(after_write=crash)
    again = cdc.poll()
    assert again.changes == 11                                        # Postgres kept them: nothing lost
    files = sorted((tmp_path / "run" / "sources" / "pg_orders").glob("*.csv"))
    assert len(files) == 3                                            # the copy, the batch, the same batch again
    cust, plans, orders, q = _current(tmp_path)
    assert cust == shop.END_CUSTOMERS and orders == shop.END_ORDERS and q == 0


@needs_pg
def test_the_starting_copy_is_as_of_the_slot_and_a_change_during_it_arrives_once_by_the_log(db, tmp_path):
    con, slot, shop = db
    cdc = _cdc(tmp_path, slot)

    def change_now():
        shop.play(con, [["UPDATE customers SET email = 'ann@during.test' WHERE id = 1"]])
    cdc.start(after_slot=change_now)
    first = (tmp_path / "run" / "sources" / "pg_customers" / "pg_customers_c000001.csv").read_text()
    assert "ann.lee@mail.test" in first and "ann@during.test" not in first     # the copy is as of the slot
    r = cdc.poll()
    assert r.by_op == {"u": 1}                                                  # the change came by the log
    cust, *_ = _current(tmp_path)
    assert cust[1] == "ann@during.test"


@needs_pg
def test_the_copy_is_older_than_every_change_even_one_at_the_slots_own_position(db, tmp_path):
    """The first change after the slot can sit at the slot's position itself; the copy tagged with that position
    tied with it, and which version was newest was left to the loader's tie-breaks."""
    con, slot, shop = db
    cdc = _cdc(tmp_path, slot)
    cdc.start()
    shop.play(con, [["UPDATE customers SET plan = 'pro' WHERE id = 1"]])
    cdc.poll()
    lake = duckdb.connect()
    rows = lake.execute(f"SELECT _op, _lsn FROM read_csv_auto('{tmp_path}/run/sources/pg_customers/*.csv') WHERE id = 1").fetchall()
    copy = [l for o, l in rows if o == "r"][0]
    assert all(copy < l for o, l in rows if o != "r")
    _, plans, _, _ = _current(tmp_path)
    assert plans[1] == "pro"


@needs_pg
def test_an_unread_slot_reports_how_much_log_it_holds_back(db, tmp_path):
    con, slot, shop = db
    cdc = _cdc(tmp_path, slot)
    cdc.start()
    shop.play(con)
    cur = con.cursor()
    cur.execute("SELECT pg_wal_lsn_diff(pg_current_wal_lsn(), confirmed_flush_lsn) FROM pg_replication_slots WHERE slot_name = %s", (slot,))
    assert cur.fetchone()[0] > 0
    assert cdc.poll().lag_bytes == 0


@needs_pg
def test_a_column_added_mid_day_is_landed_and_held_back_by_the_contract(db, tmp_path):
    con, slot, shop = db
    cdc = _cdc(tmp_path, slot)
    cdc.start()
    shop.play(con)
    cdc.poll()
    text = "".join(p.read_text() for p in (tmp_path / "run" / "sources" / "pg_customers").glob("*.csv"))
    assert "referral_code" in text and "ANN-1" in text
    _current(tmp_path)
    con2 = duckdb.connect(str(tmp_path / "w.duckdb"), read_only=True)
    cols = [r[0] for r in con2.execute("DESCRIBE current_pg_customers").fetchall()]
    assert "referral_code" not in cols                                # held back until a person adds it


@pytest.mark.parametrize("raw,want", [("2026-11-12 07:43:46.32-05", "2026-11-12 12:43:46.320000"),
                                      ("2026-11-12 07:43:46.3-05", "2026-11-12 12:43:46.300000"),
                                      ("2026-11-12 07:43:46-05", "2026-11-12 12:43:46"),
                                      ("2026-11-12 07:43:46.123456+05:30", "2026-11-12 02:13:46.123456")])
def test_a_timestamp_with_trimmed_fraction_digits_parses_on_every_supported_python(raw, want):
    """Postgres writes 46.32, not 46.320000, and Python 3.10's parser takes only 3 or 6 digits: the CI job on 3.10
    failed with "Invalid isoformat string", and a 3.10 deployment would have crashed on such a change."""
    from plumbline.ingest.connectors.postgres_cdc import _utc
    assert _utc(raw) == want


@needs_pg
def test_the_benchmark_reads_every_change_it_committed(tmp_path):
    pytest.importorskip("psycopg2")
    from plumbline.ingest.cdc_bench import run
    r = run(DSN, sizes=(900,), batch=200, seed_rows=200, out=tmp_path / "b.json")
    (b,) = r["bursts"]
    # whole transactions only: the burst commits 500-row transactions, so a 200-change limit still reads 500 at a time
    assert b["changes"] == b["read"] == 900 and 2 <= b["polls"] <= 5 and b["lag_after_mb"][-1] == 0


def test_a_large_value_the_log_left_out_is_taken_from_the_old_row_when_it_is_logged():
    t, op, row = parse("table public.tt: UPDATE: old-key: id[integer]:1 note[text]:'a long note' plan[text]:'pro' "
                       "new-tuple: id[integer]:1 note[text]:unchanged-toast-datum plan[text]:'team'")
    assert row == {"id": "1", "note": "a long note", "plan": "team"}
    from plumbline.ingest.connectors.postgres_cdc import UNCHANGED
    t, op, row = parse("table public.tt: UPDATE: id[integer]:1 note[text]:unchanged-toast-datum plan[text]:'pro'")
    assert row["note"] is UNCHANGED and row["plan"] == "pro"


@needs_pg
@pytest.mark.parametrize("identity", ["DEFAULT", "FULL"])
def test_a_long_note_survives_an_update_that_does_not_touch_it(db, tmp_path, identity):
    """Postgres stores values over about 2 kB out of line (TOAST) and does not log one an update leaves alone:
    test_decoding writes "unchanged-toast-datum", and the connector stored that text as the customer's note."""
    con, slot, shop = db
    cur = con.cursor()
    cur.execute("ALTER TABLE customers ADD COLUMN note text")
    cur.execute(f"ALTER TABLE customers REPLICA IDENTITY {identity}")
    long = "".join(f"{i:05d}-a long free-text note " for i in range(600))      # about 14 kB
    cdc = _cdc(tmp_path, slot)
    cdc.start()
    shop.play(con, [[f"UPDATE customers SET note = '{long}' WHERE id = 1"], ["UPDATE customers SET plan = 'pro' WHERE id = 1"]])
    r = cdc.poll()
    assert r.toast_filled == (1 if identity == "DEFAULT" else 0)
    lake = duckdb.connect()
    notes = lake.execute(f"SELECT note FROM read_csv_auto('{tmp_path}/run/sources/pg_customers/*_c000002.csv', all_varchar=true) "
                         "WHERE id = '1'").fetchall()
    assert [n for (n,) in notes] == [long, long]                       # both versions carry the real note
