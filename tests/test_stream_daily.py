"""The daily window of a live stream, run against the local stand-in: what it records, the planned
disconnect's resume check, the cloud publish, and a failed run recorded rather than lost."""
from __future__ import annotations


import duckdb
import pytest

from plumbline.ingest import stream_daily as SD
from plumbline.store.sse_server import Stream, make_events, serve

FAST = dict(base_delay=0.0, max_delay=0.0, sleep=lambda s: None, read_timeout=5)


@pytest.fixture
def live():
    s = Stream(make_events(3000), delay=0.01, drop_every=900)
    srv, url = serve(s)
    yield s, url
    srv.shutdown()


def test_a_window_is_recorded_with_its_resume_check_lag_and_cloud_publish(live, tmp_path):
    s, url = live
    cloud = str(tmp_path / "cloud.duckdb")
    r = SD.run(url=url, seconds=3, out=tmp_path / "out", client_kw=FAST, publish_to=cloud)
    assert r["ok"] and r["events"] > 500 and r["rows_loaded"] == r["rows"] and r["rows_quarantined"] == 0
    assert r["reconnects_by_cause"].get("planned") == 1 and r["resume_check"]["repeated"] == 0
    assert r["lag_p50"] is not None and r["lag_p95"] >= r["lag_p50"]
    assert r["published"]["rows_after"] == r["rows_loaded"]
    assert "user" in r["dropped_fields"] and SD.history(tmp_path / "out") == [r]
    with duckdb.connect(cloud, read_only=True) as con:
        assert con.execute("SELECT count(*) FROM current_wikimedia_recentchange").fetchone()[0] == r["rows_loaded"]


def test_a_stream_that_cannot_be_reached_is_recorded_as_a_failed_run(tmp_path):
    with pytest.raises(Exception):
        SD.run(url="http://127.0.0.1:9/v2/stream/recentchange", seconds=2, out=tmp_path / "out",
               client_kw={**FAST, "max_reconnects": 2})
    (r,) = SD.history(tmp_path / "out")
    assert r["ok"] is False and "gave up after 2 reconnects" in r["error"]
