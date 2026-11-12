"""The NYC Open Data connector against a local API that throttles, fails, corrects rows mid-sync and
adds a column without notice. Each test is one promise the connector makes."""
from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from plumbline.store.socrata_server import Dataset, make_rows, serve, ts  # noqa: E402

from plumbline.ingest.connectors.socrata import SocrataClient, SocrataError, sync_dataset  # noqa: E402

FAST = dict(base_delay=0.0, max_delay=0.0, max_retries=12, jitter_seed=0, sleep=lambda s: None)


def _client(url, token=None):
    return SocrataClient(url, token, **FAST)


def _landed(folder: Path) -> dict[str, dict]:
    """Latest landed version of each row, as the loader would keep it (newest source_updated_at)."""
    out = {}
    for p in sorted(folder.glob("*.csv")):
        for r in csv.DictReader(open(p)):
            k = r["source_row_id"]
            if k not in out or r["source_updated_at"] >= out[k]["source_updated_at"]:
                out[k] = r
    return out


def _truth(ds: Dataset) -> dict[str, str]:
    return {k: r[":updated_at"] for k, r in ds.rows.items()}


@pytest.fixture
def api():
    servers = []

    def make(rows=500, **kw):
        ds = Dataset(make_rows(rows), **kw)
        srv, url = serve(ds)
        servers.append(srv)
        return ds, url
    yield make
    for s in servers:
        s.shutdown()


def test_every_row_lands_once_in_its_latest_version_through_faults_and_mid_sync_corrections(api, tmp_path):
    ds, url = api(p429=0.15, p503=0.1, p_drop=0.05, p_half=0.05, seed=3, change_after_requests=3)
    st = sync_dataset(_client(url), "ipu4-2q9a", tmp_path, name="permits", page_size=50)
    st2 = sync_dataset(_client(url), "ipu4-2q9a", tmp_path, name="permits", page_size=50)   # picks up the corrections
    got = _landed(tmp_path)
    assert {k: v["source_updated_at"] for k, v in got.items()} == {k: t.rstrip("Z") for k, t in _truth(ds).items()}
    assert st.retries > 0 and st.throttled > 0                                             # the faults happened
    assert all(got[i]["permit_type"] == "AL" for i in ds.changed)                          # corrections arrived
    assert st2.orders < len(ds.rows)                                                       # the second sync was incremental


def test_rows_sharing_a_timestamp_across_a_page_boundary_are_not_skipped(api, tmp_path):
    """Every 37th row shares one timestamp; with page size 7 the tie spans pages. The keyset breaks
    ties by :id; a strict "updated_at greater than the last one" would drop the rest of the tie."""
    ds, url = api(rows=300)
    sync_dataset(_client(url), "ipu4-2q9a", tmp_path, name="permits", page_size=7)
    assert set(_landed(tmp_path)) == set(ds.rows)


def test_a_crash_between_pages_resumes_from_the_saved_cursor_without_loss(api, tmp_path):
    ds, url = api(rows=400)

    def crash(page):
        if page == 3:
            raise KeyboardInterrupt
    with pytest.raises(KeyboardInterrupt):
        sync_dataset(_client(url), "ipu4-2q9a", tmp_path, name="permits", page_size=40, after_page=crash)
    before = ds.requests
    st = sync_dataset(_client(url), "ipu4-2q9a", tmp_path, name="permits", page_size=40)
    assert st.resumed and set(_landed(tmp_path)) == set(ds.rows)
    assert ds.requests - before <= 400 // 40 - 3 + 1                                       # no full re-pull


def test_a_second_sync_fetches_only_what_changed(api, tmp_path):
    ds, url = api(rows=300)
    sync_dataset(_client(url), "ipu4-2q9a", tmp_path, name="permits", page_size=100)
    ds.correct(["row-000010", "row-000020"])
    st = sync_dataset(_client(url), "ipu4-2q9a", tmp_path, name="permits", page_size=100)
    assert st.orders <= 10 and {"row-000010", "row-000020"} <= {r["source_row_id"] for r in
                                                                csv.DictReader(open(sorted(tmp_path.glob("*_s0002_*.csv"))[0]))}


def test_columns_that_name_or_contact_a_person_are_never_written(api, tmp_path):
    ds, url = api(rows=200)
    sync_dataset(_client(url), "ipu4-2q9a", tmp_path, name="permits", page_size=50)
    text = "".join(p.read_text() for p in tmp_path.glob("*.csv"))
    header = next(csv.reader(open(sorted(tmp_path.glob("*.csv"))[0])))
    assert not any(h.startswith(("owner", "permittee")) for h in header)
    assert "ACME BUILDERS" not in text and not any(r["owner_s_first_name"] in text.split(",") for r in ds.rows.values())
    state = json.loads((tmp_path / "_sync_state.json").read_text())
    assert {"owner_s_first_name", "owner_s_phone__", "permittee_s_business_name"} <= set(state["dropped_personal"])


def test_a_new_column_is_reported_as_drift_and_does_not_change_the_delivery_shape(api, tmp_path):
    ds, url = api(rows=200, change_after_requests=2)
    sync_dataset(_client(url), "ipu4-2q9a", tmp_path, name="permits", page_size=20)
    sync_dataset(_client(url), "ipu4-2q9a", tmp_path, name="permits", page_size=20)
    headers = {tuple(next(csv.reader(open(p)))) for p in tmp_path.glob("*.csv")}
    state = json.loads((tmp_path / "_sync_state.json").read_text())
    assert len(headers) == 1 and "filing_status" in state["drift"] and "filing_status" not in state["columns"]


def test_a_field_the_json_leaves_out_is_written_empty_not_shifted(api, tmp_path):
    ds, url = api(rows=200)
    sync_dataset(_client(url), "ipu4-2q9a", tmp_path, name="permits", page_size=50)
    got = _landed(tmp_path)
    missing = [k for k, r in ds.rows.items() if "permit_type" not in r]
    assert missing and all(got[k]["permit_type"] == "" for k in missing)
    assert all(got[k]["zip_code"] == ds.rows[k]["zip_code"] for k in ds.rows)


def test_the_app_token_is_never_written_anywhere(api, tmp_path):
    ds, url = api(rows=100)
    c = _client(url, token="SECRET-APP-TOKEN-123")
    sync_dataset(c, "ipu4-2q9a", tmp_path, name="permits", page_size=50)
    assert "SECRET-APP-TOKEN-123" not in "".join(p.read_text() for p in tmp_path.iterdir()) + repr(c)


def test_an_api_that_ignores_the_cursor_stops_the_sync_instead_of_looping(api, tmp_path, monkeypatch):
    """A broken filter would return the same page forever; the sync must stop and leave the watermark alone."""
    import plumbline.ingest.connectors.socrata as S
    ds, url = api(rows=100)
    monkeypatch.setattr(S, "_query", lambda dataset, cursor, since, limit, after=None: f"/resource/{dataset}.json?$limit={limit}")
    with pytest.raises(SocrataError, match="no progress"):
        sync_dataset(_client(url), "ipu4-2q9a", tmp_path, name="permits", page_size=10)
    assert json.loads((tmp_path / "_sync_state.json").read_text())["watermark"] is None


def test_a_correction_in_the_same_second_as_the_watermark_is_not_lost(api, tmp_path):
    """The city can correct a row with the very timestamp the last sync ended on. Starting the next
    sync strictly after the watermark would skip it forever; the 5-minute overlap re-reads it."""
    ds, url = api(rows=120)
    sync_dataset(_client(url), "ipu4-2q9a", tmp_path, name="permits", page_size=50)
    wm = json.loads((tmp_path / "_sync_state.json").read_text())["watermark"]
    late = "row-000005"
    ds.rows[late][":updated_at"] = wm + ".000" if "." not in wm else wm
    ds.rows[late]["permit_type"] = "DM"
    sync_dataset(_client(url), "ipu4-2q9a", tmp_path, name="permits", page_size=50)
    assert _landed(tmp_path)[late]["permit_type"] == "DM"


def test_a_building_address_is_kept_while_the_people_attached_to_it_are_dropped(api, tmp_path):
    """A first version matched any column with "name" in it and dropped street_name with the owners."""
    ds, url = api(rows=60)
    sync_dataset(_client(url), "ipu4-2q9a", tmp_path, name="permits", page_size=50)
    header = next(csv.reader(open(sorted(tmp_path.glob("*.csv"))[0])))
    assert {"street_name", "house__", "zip_code"} <= set(header)
    assert not {"owner_s_first_name", "owner_s_phone__", "permittee_s_business_name"} & set(header)


def test_a_new_public_dataset_onboards_end_to_end_and_every_step_is_timed(api, tmp_path):
    """Sync, draft, validate, load: the number the ingestion role owns, measured. The draft goes to
    drafts/ for a person; the first delivery loads under it with every row kept once."""
    from plumbline.ingest.onboard_source import onboard
    ds, url = api(rows=500, p429=0.1, p503=0.05, seed=2)
    r = onboard("ipu4-2q9a", "permits", host=url, since=None, workdir=tmp_path / "w", drafts=tmp_path / "drafts",
                page_size=100, max_pages=None, client_kw=FAST)
    assert set(r["seconds"]) == {"sync", "draft", "validate", "load"} and r["total_seconds"] > 0
    assert r["rows_landed"] == 500 and r["rows_loaded"] == 500 and r["rows_quarantined"] == 0
    assert "street_name" not in r["dropped_personal"] and "owner_s_first_name" in r["dropped_personal"]
    draft = (tmp_path / "drafts" / "permits.yaml").read_text()
    assert "key:\n- source_row_id" in draft and "latest_by:\n- source_updated_at" in draft


def test_the_query_puts_the_star_first_as_the_real_api_requires(api, tmp_path):
    """NYC's API refused "$select=:id,:updated_at,*" with HTTP 400 on the first real run; the local server
    accepted it. The server now refuses it too, so this fails if the order comes back."""
    ds, url = api(rows=300)
    first = min(r[":updated_at"] for r in ds.rows.values())[:10]           # a bare date, which NYC accepts
    st = sync_dataset(_client(url), "rbx6-tga4", tmp_path / "land", name="permits", page_size=100, since=first)
    assert st.orders == 300


def test_a_refused_query_reports_the_apis_reason_not_just_the_status(api, tmp_path):
    from urllib.parse import urlencode
    from plumbline.ingest.connectors.store_api import StoreAPIError, SyncStats
    _, url = api(rows=10)
    with pytest.raises(StoreAPIError, match="HTTP 400 .*Star selections must come at the start"):
        _client(url).get("/resource/x.json?" + urlencode({"$select": ":id,:updated_at,*"}), SyncStats())


def test_a_since_that_is_not_a_date_is_refused_before_any_request(api, tmp_path):
    _, url = api(rows=10)
    with pytest.raises(SocrataError, match="not a date"):
        sync_dataset(_client(url), "x", tmp_path / "l", name="x", since="last tuesday")


def test_a_row_written_late_inside_the_overlap_still_lands_but_rows_already_landed_there_do_not(api, tmp_path):
    from datetime import datetime, timedelta
    ds, url = api(400)
    sync_dataset(_client(url), "abcd-1234", tmp_path, name="permits", since="2026-09-01", page_size=100)
    wm = json.loads((tmp_path / "_sync_state.json").read_text())["watermark"]
    late = (datetime.fromisoformat(wm) - timedelta(minutes=2)).strftime("%Y-%m-%dT%H:%M:%S.000")
    ds.rows["row-late01"] = {":id": "row-late01", ":updated_at": late, "job__": "1", "street_name": "KENT AVENUE"}
    before = set(tmp_path.glob("*.csv"))
    st = sync_dataset(_client(url), "abcd-1234", tmp_path, name="permits", page_size=100)
    landed = [r["source_row_id"] for p in set(tmp_path.glob("*.csv")) - before for r in csv.DictReader(open(p))]
    assert landed == ["row-late01"] and st.orders == 1 and st.reread >= 1


def _reloaded(ds: Dataset, n_newer: int = 30):
    """What NYC did to rbx6-tga4 on 2026-11-10: every row re-stamped with one update time, then a few real
    changes after it."""
    from datetime import datetime, timedelta
    from plumbline.store.socrata_server import ts
    t = ds.now_max() + timedelta(days=1)
    for r in ds.rows.values():
        r[":updated_at"] = ts(t)
    for i, k in enumerate(sorted(ds.rows)[:n_newer]):
        ds.rows[k][":updated_at"] = ts(t + timedelta(hours=1, seconds=i))
    return t


def test_a_run_stopped_by_its_page_budget_continues_where_it_stopped_not_from_the_top(api, tmp_path):
    """Under one update time the old code threw the cursor away at the page budget: every run re-read the same first
    pages and never reached the rest. 2,500 rows, 2 pages a run: three runs must land all of them, once each."""
    ds, url = api(rows=2500)
    _reloaded(ds, n_newer=0)
    got = [sync_dataset(_client(url), "x", tmp_path, name="p", since="2026-01-01", page_size=500, max_pages=2, reload_rows=None)
           for _ in range(3)]
    assert [g.capped for g in got] == [True, True, False]
    assert [g.orders for g in got] == [1000, 1000, 500]
    assert set(_landed(tmp_path)) == set(ds.rows)


def test_a_publisher_reload_is_counted_skipped_and_the_real_changes_after_it_land_in_the_same_run(api, tmp_path):
    ds, url = api(rows=2500)
    first = sync_dataset(_client(url), "x", tmp_path, name="p", since="2026-01-01", page_size=500)
    assert first.orders == 2500
    t = _reloaded(ds, n_newer=30)
    r = sync_dataset(_client(url), "x", tmp_path, name="p", page_size=500, max_pages=3, reload_rows=1000)
    assert r.reload_skipped == {"timestamp": t.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3], "rows": 2470} and not r.capped
    newer = {k for k, v in ds.rows.items() if v[":updated_at"] > ts(t)}
    latest = _landed(tmp_path)
    assert len(newer) == 30 and all(latest[k]["source_updated_at"] == ds.rows[k][":updated_at"].rstrip("Z") for k in newer)
    assert r.orders == 30                                            # the reload itself was not re-landed


def test_a_daily_batch_under_one_time_below_the_reload_limit_is_still_read(api, tmp_path):
    """NYC stamps each day's batch with one time; only a block bigger than reload_rows is treated as a reload."""
    ds, url = api(rows=1500)
    _reloaded(ds, n_newer=0)
    r = sync_dataset(_client(url), "x", tmp_path, name="p", since="2026-01-01", page_size=500, reload_rows=100_000)
    assert r.reload_skipped is None and r.orders == 1500


def test_a_column_the_contract_approves_is_written_when_it_first_appears_after_the_first_page(api, tmp_path):
    """The API leaves out a field empty on a whole page. apt_condo_no_s was missing from the first page NYC sent
    on 2026-11-07, so it was called drift and every apartment number was dropped, though the contract had it."""
    ds, url = api(rows=600)
    for i, k in enumerate(sorted(ds.rows, key=lambda k: (ds.rows[k][":updated_at"], k))):
        ds.rows[k]["apt_condo_no_s"] = "" if i < 300 else f"APT {i}"
    got = sync_dataset(_client(url), "x", tmp_path / "a", name="p", since="2026-01-01", page_size=100, accepted={"apt_condo_no_s"})
    rows = _landed(tmp_path / "a")
    assert sum(bool(r.get("apt_condo_no_s")) for r in rows.values()) == 300
    st = json.loads((tmp_path / "a" / "_sync_state.json").read_text())
    assert "apt_condo_no_s" not in st["drift"]
    sync_dataset(_client(url), "x", tmp_path / "b", name="p", since="2026-01-01", page_size=100)   # not approved
    st = json.loads((tmp_path / "b" / "_sync_state.json").read_text())
    assert st["drift"] == ["apt_condo_no_s"] and not any(r.get("apt_condo_no_s") for r in _landed(tmp_path / "b").values())


def test_the_test_server_refuses_row_id_lookups_like_nyc_does(api):
    from plumbline.store.socrata_server import BadQuery
    ds, _ = api(rows=5)
    k = sorted(ds.rows)[0]
    for where in (f":id = '{k}'", f":id in ('{k}')"):
        with pytest.raises(BadQuery):
            ds.query({"$select": "*, :id, :updated_at", "$where": where})


def test_an_approved_column_already_called_drift_is_promoted_even_when_no_page_carries_it(api, tmp_path):
    """The first run after the fix re-read pages where every apartment number was empty, so NYC left the field
    out and apt_condo_no_s stayed on the drift list although the contract approves it."""
    ds, url = api(rows=300)
    sync_dataset(_client(url), "x", tmp_path, name="p", since="2026-01-01", page_size=100)
    p = tmp_path / "_sync_state.json"
    st = json.loads(p.read_text())
    st["drift"] = ["apt_condo_no_s"]                       # as an older connector left it
    p.write_text(json.dumps(st))
    sync_dataset(_client(url), "x", tmp_path, name="p", page_size=100, accepted={"apt_condo_no_s"})
    st = json.loads(p.read_text())
    assert st["drift"] == [] and "apt_condo_no_s" in st["columns"]
