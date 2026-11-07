"""Publishing to the cloud warehouse, tested against a local file standing in for MotherDuck (same engine)."""
from __future__ import annotations

import duckdb
import pytest

from plumbline.products.publish import PublishError, Table, publish

T = Table("current_permits", "source_row_id", "source_updated_at")


def _run(path, rows, extra_col=None):
    with duckdb.connect(str(path)) as con:
        cols = "source_row_id VARCHAR, source_updated_at TIMESTAMP, permit_status VARCHAR, cost DOUBLE"
        if extra_col:
            cols += f", {extra_col} VARCHAR"
        con.execute(f"CREATE OR REPLACE TABLE current_permits ({cols})")
        for r in rows:
            con.execute(f"INSERT INTO current_permits VALUES ({','.join('?' * len(r))})", list(r))
    return path


def _cloud(path):
    with duckdb.connect(str(path), read_only=True) as con:
        return {r[0]: r for r in con.execute("SELECT * FROM current_permits").fetchall()}


def test_the_first_publish_copies_the_newest_version_of_each_key(tmp_path):
    src = _run(tmp_path / "day1.duckdb", [("a", "2026-11-01 10:00", "Issued", 10.0), ("b", "2026-11-01 11:00", "Issued", 5.0),
                                          ("a", "2026-11-01 12:00", "Expired", 10.0)])        # a, twice in one run
    r = publish(src, [T], str(tmp_path / "cloud.duckdb"), run="day1")
    assert r["tables"][0] == {"table": "current_permits", "rows_in_run": 2, "rows_replaced": 0, "rows_new": 2,
                                   "rows_after": 2, "columns_added": []}
    assert _cloud(tmp_path / "cloud.duckdb")["a"][2] == "Expired"


def test_publishing_the_same_run_twice_changes_nothing(tmp_path):
    src = _run(tmp_path / "d.duckdb", [("a", "2026-11-01 10:00", "Issued", 1.0), ("b", "2026-11-01 10:00", "Issued", 2.0)])
    cloud = str(tmp_path / "cloud.duckdb")
    publish(src, [T], cloud)
    before = _cloud(cloud)
    r = publish(src, [T], cloud)
    assert _cloud(cloud) == before and r["tables"][0]["rows_new"] == 0 and r["tables"][0]["rows_after"] == 2


def test_the_next_day_replaces_changed_keys_and_adds_new_ones(tmp_path):
    cloud = str(tmp_path / "cloud.duckdb")
    publish(_run(tmp_path / "1.duckdb", [("a", "2026-11-01 10:00", "Issued", 1.0), ("b", "2026-11-01 10:00", "Issued", 2.0)]), [T], cloud)
    r = publish(_run(tmp_path / "2.duckdb", [("b", "2026-11-02 09:00", "Revoked", 2.0), ("c", "2026-11-02 09:00", "Issued", 3.0)]), [T], cloud)
    assert (r["tables"][0]["rows_replaced"], r["tables"][0]["rows_new"], r["tables"][0]["rows_after"]) == (1, 1, 3)
    c = _cloud(cloud)
    assert c["a"][2] == "Issued" and c["b"][2] == "Revoked" and c["c"][2] == "Issued"


def test_a_column_that_appears_is_added_to_the_cloud_table_first(tmp_path):
    cloud = str(tmp_path / "cloud.duckdb")
    publish(_run(tmp_path / "1.duckdb", [("a", "2026-11-01 10:00", "Issued", 1.0)]), [T], cloud)
    r = publish(_run(tmp_path / "2.duckdb", [("b", "2026-11-02 10:00", "Issued", 2.0, "Fl 3")], extra_col="work_on_floor"), [T], cloud)
    assert r["tables"][0]["columns_added"] == ["work_on_floor"]
    with duckdb.connect(cloud, read_only=True) as con:
        assert con.execute("SELECT source_row_id, work_on_floor FROM current_permits ORDER BY 1").fetchall() == [("a", None), ("b", "Fl 3")]
        assert con.execute("SELECT columns_added FROM _publish_log ORDER BY published_at DESC LIMIT 1").fetchone()[0] == "work_on_floor"


def test_a_publish_that_fails_halfway_leaves_the_cloud_as_it_was(tmp_path):
    cloud = str(tmp_path / "cloud.duckdb")
    publish(_run(tmp_path / "1.duckdb", [("a", "2026-11-01 10:00", "Issued", 1.0), ("b", "2026-11-01 10:00", "Issued", 2.0)]), [T], cloud)
    before = _cloud(cloud)
    with duckdb.connect(str(tmp_path / "2.duckdb")) as con:      # cost arrives as text the cloud's DOUBLE cannot hold
        con.execute("CREATE TABLE current_permits (source_row_id VARCHAR, source_updated_at TIMESTAMP, permit_status VARCHAR, cost VARCHAR)")
        con.execute("INSERT INTO current_permits VALUES ('a', '2026-11-02 10:00', 'Revoked', 'n/a')")
    with pytest.raises(duckdb.Error):
        publish(tmp_path / "2.duckdb", [T], cloud)
    assert _cloud(cloud) == before                              # the delete of 'a' was rolled back with the insert


def test_motherduck_without_a_token_publishes_nothing_and_says_why(tmp_path, monkeypatch):
    monkeypatch.delenv("MOTHERDUCK_TOKEN", raising=False)
    src = _run(tmp_path / "1.duckdb", [("a", "2026-11-01 10:00", "Issued", 1.0)])
    with pytest.raises(PublishError, match="MOTHERDUCK_TOKEN is not set"):
        publish(src, [T], "md:plumbline")


def test_table_and_column_names_cannot_carry_sql(tmp_path):
    src = _run(tmp_path / "1.duckdb", [("a", "2026-11-01 10:00", "Issued", 1.0)])
    with pytest.raises(PublishError, match="not a plain identifier"):
        publish(src, [Table("current_permits; DROP TABLE x", "source_row_id", "source_updated_at")], str(tmp_path / "c.duckdb"))
