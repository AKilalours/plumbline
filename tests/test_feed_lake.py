"""The real NYC feed's Iceberg copy: the daily run appends one snapshot per run, a rerun commits nothing,
and any run's view of the feed can be read back (time travel), through the same daily job code."""
from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

pytest.importorskip("pyiceberg")

from plumbline.ingest import open_data_daily as OD  # noqa: E402
from plumbline.lake import iceberg as L  # noqa: E402
from plumbline.store.socrata_server import ts  # noqa: E402

from test_open_data_daily import city  # noqa: E402,F401  (the stand-in NYC API and its contract)


def lake(tmp_path: Path) -> L.Lake:
    return L.Lake(str(tmp_path / "lake" / "warehouse"), tmp_path / "lake" / "catalog.db")


def test_each_run_appends_one_snapshot_tagged_with_its_run_and_a_quiet_run_commits_nothing(city, tmp_path):
    ds, kw = city
    a = OD.run(**kw, lake_dir=str(tmp_path / "lake"))
    assert a["lake"]["appended"] == 1200 and a["lake"]["rows"] == 1200 and a["lake"]["snapshots"] == 1
    b = OD.run(**kw, lake_dir=str(tmp_path / "lake"))
    assert (b.get("lake") or {}).get("appended", 0) == 0
    (h,) = L.history(lake(tmp_path), "permits")
    assert h["run_at"] == a["run_at"] and h["added"] == 1200


def test_time_travel_shows_the_feed_as_an_earlier_run_left_it(city, tmp_path, monkeypatch):
    ds, kw = city
    a = OD.run(**kw, lake_dir=str(tmp_path / "lake"))
    rid = sorted(ds.rows)[0]
    ds.rows[rid]["street_name"] = "CHANGED AVENUE"
    ds.rows[rid][":updated_at"] = ts(ds.now_max() + timedelta(hours=1))
    import time
    time.sleep(1.1)                                        # run_at has one-second resolution
    b = OD.run(**kw, lake_dir=str(tmp_path / "lake"))
    assert b["lake"]["appended"] == 1 and b["lake"]["snapshots"] == 2
    then = {r["source_row_id"]: r["street_name"] for r in L.as_of(lake(tmp_path), "permits", a["run_at"]).to_pylist()}
    now = {r["source_row_id"]: r["street_name"] for r in L.as_of(lake(tmp_path), "permits", b["run_at"]).to_pylist()}
    assert len(then) == len(now) == 1200
    assert now[rid] == "CHANGED AVENUE" and then[rid] != "CHANGED AVENUE"
    with pytest.raises(LookupError):
        L.as_of(lake(tmp_path), "permits", "2020-01-01T00:00:00Z")


def test_a_lake_that_fails_is_recorded_and_the_sync_still_keeps_its_cursor(city, tmp_path):
    ds, kw = city
    bad = tmp_path / "file-not-dir"
    bad.write_text("x")
    r = OD.run(**kw, lake_dir=str(bad))
    assert r["ok"] and r["lake"] is None and r["lake_error"]
    assert OD.run(**kw)["rows"] < 1200                     # the next run started from the saved cursor


def test_without_a_lake_folder_nothing_is_written_or_claimed(city, monkeypatch):
    ds, kw = city
    monkeypatch.delenv("PLUMBLINE_FEED_LAKE", raising=False)
    assert OD.run(**kw)["lake"] is None


def _warehouse(tmp_path, csv_text: str, extra: dict | None = None) -> Path:
    import yaml
    from plumbline.ingest.contracts import load_contracts
    from plumbline.ingest.engine import ingest
    run = tmp_path / "run" / "sources" / "permits"
    run.mkdir(parents=True)
    (run / "p1.csv").write_text(csv_text)
    c = tmp_path / "c"
    c.mkdir()
    cols = {"source_row_id": {"type": "string", "required": True}, "source_updated_at": {"type": "timestamp", "required": True},
            "street_name": {"type": "string"}, **(extra or {})}
    (c / "permits.yaml").write_text(yaml.safe_dump({"name": "permits", "kind": "api", "files": "sources/permits/*.csv",
        "key": ["source_row_id"], "latest_by": ["source_updated_at"], "columns": cols}))
    wh = tmp_path / "w.duckdb"
    ingest(tmp_path / "run", wh, load_contracts(c))
    return wh


def test_the_same_rows_published_again_add_no_snapshot(tmp_path):
    """Every NYC run re-reads the rows at its cursor (the overlap); the lake must not grow from them."""
    wh = _warehouse(tmp_path, "source_row_id,source_updated_at,street_name\nrow-1,2026-11-10T00:00:00,KENT AVENUE\n"
                              "row-2,2026-11-10T00:00:00,N 7 STREET\n")
    lk = lake(tmp_path)
    a = L.feed_snapshot(wh, lk, "permits", "2026-11-10T01:00:00Z")
    b = L.feed_snapshot(wh, lk, "permits", "2026-11-10T04:00:00Z")
    assert (a["appended"], b["appended"], b["snapshots"], b["rows"]) == (2, 0, 1, 2)


def test_a_column_the_contract_adds_later_widens_the_table_and_old_snapshots_still_read(tmp_path):
    """The apartment-number case: a column approved after the table exists."""
    lk = lake(tmp_path)
    a = _warehouse(tmp_path / "a", "source_row_id,source_updated_at,street_name\nrow-1,2026-11-10T00:00:00,KENT AVENUE\n")
    first = L.feed_snapshot(a, lk, "permits", "2026-11-10T01:00:00Z")
    b = _warehouse(tmp_path / "b", "source_row_id,source_updated_at,street_name,apt_condo_no_s\n"
                                   "row-1,2026-11-12T00:00:00,KENT AVENUE,4B\n", {"apt_condo_no_s": {"type": "string", "optional": True}})
    L.feed_snapshot(b, lk, "permits", "2026-11-12T13:35:00Z")
    now = L.as_of(lk, "permits", "2026-11-12T13:35:00Z").to_pylist()
    then = L.as_of(lk, "permits", "2026-11-10T01:00:00Z").to_pylist()
    assert [r["apt_condo_no_s"] for r in now] == ["4B"] and len(then) == 1
    assert first["snapshots"] == 1


def test_a_new_table_is_seeded_once_from_the_fuller_copy_then_only_grows_by_new_rows(tmp_path):
    wh = _warehouse(tmp_path, "source_row_id,source_updated_at,street_name\nrow-2,2026-11-12T00:00:00,N 7 STREET\n")
    import duckdb
    with duckdb.connect(str(wh), read_only=True) as con:
        held = [dict(zip([d[0] for d in con.execute("SELECT * FROM current_permits").description], r))
                for r in con.execute("SELECT * FROM current_permits").fetchall()]
    older = dict(held[0], source_row_id="row-1", _row_hash="h-old", street_name="KENT AVENUE")
    calls = []
    seed = lambda: calls.append(1) or [older]
    lk = lake(tmp_path)
    a = L.feed_snapshot(wh, lk, "permits", "2026-11-12T01:00:00Z", seed=seed)
    b = L.feed_snapshot(wh, lk, "permits", "2026-11-12T04:00:00Z", seed=seed)
    assert (a["seeded"], a["appended"], a["rows"], a["snapshots"]) == (1, 1, 2, 2)
    assert (b["seeded"], b["appended"], len(calls)) == (0, 0, 1)
    assert {r["source_row_id"] for r in L.as_of(lk, "permits", "2026-11-12T04:00:00Z").to_pylist()} == {"row-1", "row-2"}
