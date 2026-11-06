"""The NYC Open Data connector against a local API that throttles, fails, corrects rows mid-sync and
adds a column without notice. Each test is one promise the connector makes."""
from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from plumbline.store.socrata_server import Dataset, make_rows, serve  # noqa: E402

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
    monkeypatch.setattr(S, "_query", lambda dataset, cursor, since, limit: f"/resource/{dataset}.json?$limit={limit}")
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
