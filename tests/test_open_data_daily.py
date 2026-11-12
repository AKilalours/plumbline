"""The daily sync of a live NYC dataset: the cursor carries over between runs, every run is recorded
(failures too), and a publisher reloading its whole table is reported rather than silently re-downloaded."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from plumbline.ingest import open_data_daily as OD
from plumbline.ingest.connectors.socrata import bulk_rewrite
from plumbline.ingest.connectors.store_api import StoreAPIError
from plumbline.store.socrata_server import Dataset, make_rows, serve

FAST = dict(base_delay=0.0, max_delay=0.0, max_retries=12, jitter_seed=0, sleep=lambda s: None)
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def city(tmp_path):
    ds = Dataset(make_rows(1200), p429=0.2, p503=0.2, seed=3)
    srv, url = serve(ds)
    contracts = tmp_path / "contracts"
    contracts.mkdir()
    cols = {"source_row_id": {"type": "string", "required": True}, "source_updated_at": {"type": "timestamp", "required": True}}
    cols |= {c: {"type": "string", "optional": True} for c in ("job__", "bin__", "house__", "street_name", "permit_type", "issuance_date")}
    cols["zip_code"] = {"type": "zip", "optional": True}
    (contracts / "permits.yaml").write_text(yaml.safe_dump({"name": "permits", "kind": "api", "files": "sources/permits/*.csv",
                                                             "key": ["source_row_id"], "latest_by": ["source_updated_at"], "columns": cols}))
    kw = dict(host=url, dataset="x", name="permits", out=tmp_path / "out", contracts=contracts, since="2026-09-01",
              client_kw=FAST, page_size=100)
    yield ds, kw
    srv.shutdown()


def test_a_run_is_recorded_with_what_it_pulled_loaded_and_held(city):
    ds, kw = city
    r = OD.run(**kw)
    assert r["ok"] and r["complete"] and r["rows"] == 1200 and r["rows_loaded"] == 1200 and r["rows_quarantined"] == 0
    assert r["retries"] > 0 and set(r["retries_by_cause"]) <= {"429", "503"}
    assert "owner_s_first_name" in r["dropped_personal"] and r["bulk_rewrite"] is None
    assert OD.history(kw["out"]) == [r]


def test_the_cursor_is_kept_between_runs_so_the_next_run_pulls_only_what_changed(city):
    from datetime import datetime, timedelta
    ds, kw = city
    first = OD.run(**kw)
    ds.correct(sorted(ds.rows)[:7])
    r = OD.run(**kw)
    edge = datetime.fromisoformat(first["watermark"]) - timedelta(minutes=5)     # the overlap re-reads 5 minutes...
    in_window = sum(datetime.fromisoformat(x[":updated_at"]) >= edge for x in ds.rows.values()) - 7
    assert r["ok"] and r["rows"] == 7 and r["reread"] == in_window              # ...but only the 7 corrections are new
    assert len(OD.history(kw["out"])) == 2


def test_a_failed_run_is_recorded_with_the_apis_reason_and_still_fails(city, monkeypatch):
    ds, kw = city
    from plumbline.ingest.connectors import socrata as S
    monkeypatch.setattr(S, "_query", lambda *a, **k: "/resource/x.json?%24select=%3Aid%2C%2A")   # the star not first
    with pytest.raises(StoreAPIError):
        OD.run(**kw)
    last = OD.history(kw["out"])[-1]
    assert last["ok"] is False and "Star selections" in last["error"]


def test_a_publisher_reloading_its_table_is_reported_as_a_bulk_rewrite(city):
    ds, kw = city
    OD.run(**kw)
    for r in ds.rows.values():                 # what NYC did to ipu4-2q9a: one new time on nearly every row
        r[":updated_at"] = "2026-11-06T00:10:39.255"
    r = OD.run(**kw)
    assert r["bulk_rewrite"] == {"timestamp": "2026-11-06T00:10:39.255", "rows": 1200, "of": 1200, "share": 1.0}
    assert bulk_rewrite({"a": 30, "b": 1}, page_size=1000) is None        # small syncs are not called rewrites


def test_the_real_contract_loads_with_the_loader_and_keeps_no_person():
    from plumbline.ingest.connectors.socrata import PERSONAL
    from plumbline.ingest.contracts import load_contract
    c = load_contract(ROOT / "sources" / "open_data" / f"{OD.NAME}.yaml")
    assert c.name == OD.NAME and not [n for n in c.columns if PERSONAL.search(n)]
    assert {n for n, x in c.columns.items() if not x.optional} == {"source_row_id", "source_updated_at", "job_filing_number", "work_permit"}


def test_the_history_skips_a_broken_line_and_falls_back_when_the_branch_is_unreachable(tmp_path):
    (tmp_path / "runs.jsonl").write_text('{"run_at": "2026-11-07T11:23:00Z", "ok": true, "rows": 5}\nnot json\n{"x": 1}\n')
    runs = OD.history(tmp_path, url="http://127.0.0.1:9/none", timeout=0.5)
    assert runs == [{"run_at": "2026-11-07T11:23:00Z", "ok": True, "rows": 5}]
    assert OD.summary(runs)["rows"] == 5 and OD.summary([])["runs"] == 0


def test_each_daily_run_upserts_into_the_cloud_copy_so_it_ends_up_a_full_replica(city, tmp_path):
    import duckdb
    ds, kw = city
    cloud = str(tmp_path / "cloud.duckdb")
    first = OD.run(**kw, publish_to=cloud)
    assert first["published"]["rows_new"] == 1200 and first["published"]["rows_after"] == 1200
    ds.correct(sorted(ds.rows)[:7])                  # the city corrects seven rows overnight
    second = OD.run(**kw, publish_to=cloud)
    assert second["published"]["rows_replaced"] >= 7 and second["published"]["rows_after"] == 1200
    with duckdb.connect(cloud, read_only=True) as con:
        cloud_rows = dict(con.execute("SELECT source_row_id, source_updated_at FROM current_permits").fetchall())
    assert {k: v.strftime("%Y-%m-%dT%H:%M:%S.000") for k, v in cloud_rows.items()} == {k: r[":updated_at"] for k, r in ds.rows.items()}


def test_without_a_cloud_token_nothing_is_published_or_claimed(city, monkeypatch):
    monkeypatch.delenv("MOTHERDUCK_TOKEN", raising=False)
    ds, kw = city
    assert OD.run(**kw)["published"] is None


def test_an_unchanged_source_lands_nothing_new_on_the_next_run(city):
    """NYC stamps a whole daily batch with one time. The 5-minute overlap re-read that whole batch on every run
    and the history counted it as new rows: 2,565 "new" rows at 05:21, 06:03 and 07:02 on 2026-11-07."""
    ds, kw = city
    first = OD.run(**kw)
    again = OD.run(**kw)
    assert first["rows"] == 1200 and again["rows"] == 0 and again["reread"] > 0
    assert OD.summary(OD.history(kw["out"]))["rows"] == 1200


def test_a_failed_cloud_publish_keeps_the_cursor_and_still_turns_the_job_red(city, monkeypatch):
    """On 2026-11-07 MotherDuck refused DuckDB 2.0 ("not yet supported"); the exception failed the whole run
    before the cursor was saved, so every following run pulled the same rows again."""
    from plumbline.products import publish as P
    ds, kw = city

    def refuse(*a, **k):
        raise RuntimeError("Your DuckDB version (v2.0.0) is not yet supported by MotherDuck")
    monkeypatch.setattr(P, "publish", refuse)
    first = OD.run(**kw, publish_to="md:plumbline")
    assert first["ok"] and first["rows"] == 1200 and "not yet supported" in first["publish_error"]
    again = OD.run(**kw, publish_to="md:plumbline")
    assert again["rows"] == 0                                   # the cursor was saved despite the failed publish
    monkeypatch.setattr(OD, "run", lambda **k: first)
    assert OD.main([]) == 1                                     # and the job still shows red
    from plumbline.ingest.alerts import problems
    from plumbline.ingest.freshness import Feed
    from datetime import datetime, timezone
    p = problems([Feed("NYC Open Data permits", 26, lambda: [first])], datetime.now(timezone.utc))
    assert any("cloud publish failed" in t for t in p)


def test_lag_is_measured_against_the_publishers_newest_update_not_the_clock(city):
    """The old measure, now minus the newest row landed, read a quiet weekend at the city as lag and could not tell
    it from a stuck sync. Caught up means 0 hours behind, however old the newest row is."""
    ds, kw = city
    r = OD.run(**kw)
    newest = max(x[":updated_at"] for x in ds.rows.values())
    assert r["source_newest"] == newest.rstrip("Z") and r["hours_behind_source"] == 0.0


def test_after_a_reload_bigger_than_a_runs_budget_the_next_run_lands_the_changes_after_it(city):
    """What happened on 2026-11-10 to 12: one time on every row, more rows than a run reads. Each run used to stop
    at its budget inside the block and start over at its top the next time, so later changes never landed."""
    from datetime import timedelta
    from plumbline.store.socrata_server import ts
    ds, kw = city
    OD.run(**kw)
    t = ds.now_max() + timedelta(days=1)
    for x in ds.rows.values():
        x[":updated_at"] = ts(t)
    later = sorted(ds.rows)[:9]
    for i, k in enumerate(later):
        ds.rows[k][":updated_at"] = ts(t + timedelta(hours=26, seconds=i))
    r = OD.run(**kw, max_pages=3, reload_rows=1000)
    assert r["ok"] and r["complete"] and r["reload_skipped"]["rows"] == 1191 and r["rows"] == 9
    assert r["hours_behind_source"] == 0.0


def _held(ds):
    """What the warehouse holds after a run: each row as landed (flattened, without the city's own system fields)."""
    from plumbline.ingest.connectors.socrata import _flat
    return [_flat(dict(r)) for r in ds.rows.values()]


def test_reconcile_finds_rows_the_source_changed_without_moving_their_time_and_rows_it_removed(city):
    """An incremental sync trusts update times; a change under the same time (or inside a skipped reload) and a
    removed row get past it. The reconcile asks the source for the rows held and compares them."""
    ds, kw = city
    OD.run(**kw, reconcile_budget=0)
    held = _held(ds)
    ids = sorted(ds.rows)
    for k in ids[:3]:
        ds.rows[k]["street_name"] = "CORRECTED STREET"           # same :updated_at
    for k in ids[3:5]:
        del ds.rows[k]
    r = OD.run(**kw, held_rows=lambda: held, reconcile_budget=5000, reconcile_by="job__")
    rc = r["reconcile"]
    assert (rc["checked"], rc["changed"], rc["gone"], rc["same"]) == (1200, 3, 2, 1195)
    assert rc["fields"] == {"street_name": 3} and set(rc["gone_ids"]) == set(ids[3:5])
    assert r["rows"] == 0 and r["reconcile_rows"] == 3 and r["rows_loaded"] == 3      # the corrections were loaded


def test_reconcile_takes_the_next_slice_each_run_and_wraps(city):
    ds, kw = city
    OD.run(**kw, reconcile_budget=0)
    held = _held(ds)
    seen = []
    for _ in range(3):
        r = OD.run(**kw, held_rows=lambda: held, reconcile_budget=500, reconcile_by="job__")
        seen.append((r["reconcile"]["offset"], r["reconcile"]["checked"], r["reconcile"]["next_offset"]))
    assert seen == [(0, 500, 500), (500, 500, 1000), (1000, 500, 300)]


def test_without_rows_held_nothing_is_claimed(city):
    ds, kw = city
    r = OD.run(**kw, publish_to="off", reconcile_by="job__")
    assert r["reconcile"] is None
