"""The streaming (SSE) connector against a local stream that drops the line, repeats events, throttles and
sends bytes in awkward pieces. Each test is one promise the connector makes."""
from __future__ import annotations

import csv
import json
from pathlib import Path

import duckdb
import pytest

from plumbline.ingest.connectors.sse import (DROPPED, Parser, SSEClient, StreamError, recentchange_row, stream_sync)
from plumbline.ingest.contracts import load_contracts
from plumbline.ingest.engine import ingest
from plumbline.store.sse_server import Stream, make_events, serve

ROOT = Path(__file__).resolve().parents[1]
FAST = dict(base_delay=0.0, max_delay=0.0, sleep=lambda s: None, read_timeout=5)


@pytest.fixture
def stream():
    servers = []

    def make(n=600, **kw):
        s = Stream(make_events(n), **kw)
        srv, url = serve(s)
        servers.append(srv)
        return s, url
    yield make
    for s in servers:
        s.shutdown()


def _expected(s: Stream) -> set[str]:
    """Every event that should land: not the health checks, not the one that is not JSON."""
    return {e["meta"]["id"] for i, e in enumerate(s.events) if e["meta"]["domain"] != "canary" and i != s.bad_json_at}


def _landed(folder: Path) -> list[dict]:
    return [r for p in sorted(folder.glob("*.csv")) for r in csv.DictReader(open(p, encoding="utf-8"))]


def _sync(url, folder, n_events, seconds=60, **kw):
    """Read until n_events have arrived, or for `seconds` when the count is not known (replays, resumes)."""
    c = SSEClient(url, **FAST)
    return stream_sync(c, folder, name="wikimedia_recentchange", transform=recentchange_row, key="event_id",
                       seconds=seconds, max_events=n_events, batch_events=100, **kw), c


# ---------------------------------------------------------------- the parser follows the standard
def _feed_all(text: str, step: int) -> list:
    p, b, out = Parser(), text.encode("utf-8"), []
    for k in range(0, len(b), step):
        out += p.feed(b[k:k + step])
    return out


@pytest.mark.parametrize("step", [1, 2, 3, 5, 64, 10_000])
def test_the_parser_gives_the_same_events_however_the_bytes_are_cut(step):
    text = ("﻿:ok\n\nretry: 250\n\nid: 1\ndata: {\"a\": \"é\"}\n\n: heartbeat\r\n"
            "event: message\r\nid: 2\r\ndata: line one\r\ndata: line two\r\n\r\n"
            "id: 3\rdata:no space\r\rdata: no id change\n\nid: 4\ndata: half")
    evs = _feed_all(text, step)
    assert [(e.id, e.data) for e in evs] == [("1", '{"a": "é"}'), ("2", "line one\nline two"),
                                             ("3", "no space"), ("3", "no id change")]


def test_an_event_cut_off_by_a_dropped_connection_is_never_dispatched():
    p = Parser()
    assert p.feed(b"id: 9\ndata: {\"half\": ") == []
    p.drop_partial()
    assert p.feed(b"\n\n") == []                       # the blank line after a reconnect does not finish it


def test_the_parser_reads_the_servers_retry_hint_and_ignores_a_bad_one():
    p = Parser()
    p.feed(b"retry: 1500\n\nretry: soon\n\n")
    assert p.retry == 1500


# ---------------------------------------------------------------- the stream
def test_a_clean_read_lands_every_event_once_and_drops_health_checks_and_bad_json(stream, tmp_path):
    s, url = stream(n=600)
    r, _ = _sync(url, tmp_path, 600)
    rows = _landed(tmp_path)
    assert {x["event_id"] for x in rows} == _expected(s)
    assert len(rows) == len(_expected(s)) and r.duplicates == 0
    assert r.filtered == sum(e["meta"]["domain"] == "canary" for e in s.events) and r.bad_json == 1
    multi = next(x for x in rows if x["event_id"] == "evt-000003")   # the event split over two data lines
    assert multi["type"] == s.events[3]["type"]


def test_after_every_dropped_connection_it_resumes_from_the_last_event_id(stream, tmp_path):
    s, url = stream(n=600, drop_every=70)              # and each drop cuts an event in half
    r, c = _sync(url, tmp_path, 600)
    assert c.stats.reconnects >= 7
    assert all(x is not None for x in s.resumed_from[1:])
    assert {x["event_id"] for x in _landed(tmp_path)} == _expected(s)
    assert len(_landed(tmp_path)) == len(_expected(s))


def test_events_the_server_sends_again_after_a_reconnect_are_dropped(stream, tmp_path):
    s, url = stream(n=600, drop_every=80, replay_on_resume=5)
    r, _ = _sync(url, tmp_path, None, seconds=2)
    assert r.duplicates >= 25
    rows = _landed(tmp_path)
    assert len(rows) == len({x["event_id"] for x in rows}) == len(_expected(s))


def test_crlf_line_endings_read_the_same(stream, tmp_path):
    s, url = stream(n=300, crlf=True, chunk=3)
    _sync(url, tmp_path, 300)
    assert {x["event_id"] for x in _landed(tmp_path)} == _expected(s)


@pytest.mark.parametrize("code", [503, 429])
def test_a_busy_server_is_retried(stream, tmp_path, code):
    s, url = stream(n=200, fail_first=3, fail_code=code)
    r, c = _sync(url, tmp_path, 200)
    assert c.stats.by_cause[f"http_{code}"] == 3
    assert {x["event_id"] for x in _landed(tmp_path)} == _expected(s)


def test_it_gives_up_with_the_reason_when_the_server_never_comes_back(stream, tmp_path):
    s, url = stream(n=50, fail_first=10_000)
    c = SSEClient(url, **{**FAST, "max_reconnects": 4})
    with pytest.raises(StreamError, match="gave up after 4 reconnects"):
        stream_sync(c, tmp_path, name="w", transform=recentchange_row, key="event_id", seconds=30, max_events=50)


def test_a_page_that_is_not_an_event_stream_stops_with_the_reason(tmp_path):
    from plumbline.store.socrata_server import Dataset, make_rows, serve as serve_json
    srv, url = serve_json(Dataset(make_rows(5)))
    try:
        with pytest.raises(StreamError, match="not an event stream"):
            stream_sync(SSEClient(url + "/resource/abcd-1234.json", **FAST), tmp_path, name="w",
                        transform=recentchange_row, key="event_id", seconds=5)
    finally:
        srv.shutdown()


def test_a_crash_between_a_batch_and_its_checkpoint_rewrites_that_batch_and_the_warehouse_keeps_one_copy(stream, tmp_path):
    s, url = stream(n=600)
    landing = tmp_path / "run" / "sources" / "wikimedia_recentchange"

    def crash(b):
        if b == 3:
            raise RuntimeError("power cut after batch 3 was written, before its checkpoint")
    with pytest.raises(RuntimeError, match="power cut"):
        _sync(url, landing, 600, after_batch=crash)
    state = json.loads((landing / "_stream_state.json").read_text())
    assert state["next_batch"] == 3                   # the checkpoint still points before batch 3
    crashed = (landing / "wikimedia_recentchange_b000003.csv").read_text(encoding="utf-8")
    cdir = tmp_path / "contracts"
    cdir.mkdir()
    (cdir / "w.yaml").write_text((ROOT / "sources/streams/wikimedia_recentchange.yaml").read_text())
    ingest(tmp_path / "run", tmp_path / "w.duckdb", load_contracts(cdir))      # batch 3 is loaded before the restart
    _sync(url, landing, None, seconds=2)              # resumes from the checkpoint: batch 3 is read again
    assert (landing / "wikimedia_recentchange_b000003.csv").read_text(encoding="utf-8") == crashed   # same number, same file
    assert len(_landed(landing)) == len(_expected(s))
    ingest(tmp_path / "run", tmp_path / "w.duckdb", load_contracts(cdir))
    with duckdb.connect(str(tmp_path / "w.duckdb"), read_only=True) as con:
        n, distinct = con.execute("SELECT count(*), count(DISTINCT event_id) FROM current_wikimedia_recentchange").fetchone()
        q = con.execute("SELECT count(*) FROM quarantine").fetchone()[0]
    assert n == distinct == len(_expected(s)) and q == 0   # one copy of each in the warehouse


def test_a_new_run_starts_after_the_checkpoint_and_sends_it_as_last_event_id(stream, tmp_path):
    s, url = stream(n=400)
    _sync(url, tmp_path, 200)
    first = len(_landed(tmp_path))
    state = json.loads((tmp_path / "_stream_state.json").read_text())
    c = SSEClient(url, **FAST)                        # then a quiet stream: the run still ends on time
    stream_sync(c, tmp_path, name="wikimedia_recentchange", transform=recentchange_row, key="event_id", seconds=1.5)
    assert s.resumed_from[-1] == state["last_event_id"]
    rows = _landed(tmp_path)
    assert len(rows) == len({x["event_id"] for x in rows}) == len(_expected(s)) and first < len(rows)


def test_a_planned_disconnect_resumes_without_repeating_or_losing_events(stream, tmp_path):
    s, url = stream(n=900, delay=0.002)
    ticks = iter(range(10**9))
    c = SSEClient(url, **FAST)
    r = stream_sync(c, tmp_path, name="w", transform=recentchange_row, key="event_id", seconds=10**9, max_events=900,
                    planned_disconnect_at=300, clock=lambda: next(ticks))
    assert c.stats.by_cause["planned"] == 1 and r.resume_check["repeated"] == 0 and r.resume_check["new"] >= 150
    assert {x["event_id"] for x in _landed(tmp_path)} == _expected(s)


def test_who_made_a_change_and_comments_never_reach_the_landing_files(stream, tmp_path):
    s, url = stream(n=200)
    _sync(url, tmp_path, 200)
    text = "".join(p.read_text(encoding="utf-8") for p in tmp_path.glob("*.csv"))
    header = next(csv.reader(open(sorted(tmp_path.glob("*.csv"))[0], encoding="utf-8")))
    assert not {"user", "comment", "parsedcomment", "log_params"} & set(header)
    assert "Editor" not in text and "free text" not in text
    assert "user" in DROPPED and "comment" in DROPPED


def test_fields_only_some_event_types_carry_land_empty_and_pass_the_contract(stream, tmp_path):
    s, url = stream(n=500)
    landing = tmp_path / "run" / "sources" / "wikimedia_recentchange"
    _sync(url, landing, 500)
    rows = _landed(landing)
    cat = next(x for x in rows if x["type"] == "categorize")
    log = next(x for x in rows if x["type"] == "log")
    assert cat["revision_new"] == "" and cat["log_type"] == "" and log["log_type"] == "upload"
    cdir = tmp_path / "c"
    cdir.mkdir()
    (cdir / "w.yaml").write_text((ROOT / "sources/streams/wikimedia_recentchange.yaml").read_text())
    ingest(tmp_path / "run", tmp_path / "w.duckdb", load_contracts(cdir))
    with duckdb.connect(str(tmp_path / "w.duckdb"), read_only=True) as con:
        assert con.execute("SELECT count(*) FROM quarantine").fetchone()[0] == 0
        assert con.execute("SELECT count(*) FROM current_wikimedia_recentchange").fetchone()[0] == len(_expected(s))
        assert con.execute("SELECT max(title) FROM current_wikimedia_recentchange WHERE title LIKE '%é'").fetchone()[0]


def test_a_field_that_appears_later_is_reported_as_drift_not_written(stream, tmp_path):
    s, url = stream(n=300)
    _sync(url, tmp_path, 150)

    def wider(d):
        r = recentchange_row(d)
        return None if r is None else {**r, "new_field": "x"}
    c = SSEClient(url, **FAST)
    stream_sync(c, tmp_path, name="wikimedia_recentchange", transform=wider, key="event_id", seconds=1.5)
    state = json.loads((tmp_path / "_stream_state.json").read_text())
    assert state["drift"] == ["new_field"]
    assert all("new_field" not in r for r in _landed(tmp_path))


def test_an_id_past_32_bits_loads_instead_of_failing_the_delivery(tmp_path):
    """Live rc ids passed 3.5 billion. The warehouse stored contract ints as 32-bit INTEGER, so this row's
    cast failed inside the insert and rolled back the whole delivery."""
    landing = tmp_path / "run" / "sources" / "wikimedia_recentchange"
    landing.mkdir(parents=True)
    row = recentchange_row(make_events(1, canary_every=0)[0])
    row["rc_id"] = 3_523_811_080
    big = {**row, "event_id": "too-big", "rc_id": 2**70}
    (landing / "w_b000001.csv").write_text("\n".join([",".join(row), ",".join(str(v) for v in row.values()),
                                                      ",".join(str(v) for v in big.values())]) + "\n", encoding="utf-8")
    cdir = tmp_path / "c"
    cdir.mkdir()
    (cdir / "w.yaml").write_text((ROOT / "sources/streams/wikimedia_recentchange.yaml").read_text())
    ingest(tmp_path / "run", tmp_path / "w.duckdb", load_contracts(cdir))
    with duckdb.connect(str(tmp_path / "w.duckdb"), read_only=True) as con:
        assert con.execute("SELECT rc_id FROM current_wikimedia_recentchange").fetchall() == [(3_523_811_080,)]
        assert con.execute("SELECT reason FROM quarantine").fetchall() == [("out_of_range:rc_id",)]


def test_lag_is_measured_from_each_events_own_time(stream, tmp_path):
    from datetime import timedelta, timezone
    from plumbline.store.sse_server import T0
    s, url = stream(n=200)
    c = SSEClient(url, **FAST)
    r = stream_sync(c, tmp_path, name="w", transform=recentchange_row, key="event_id", seconds=60, max_events=200,
                    wall=lambda: (T0 + timedelta(seconds=30)).replace(tzinfo=timezone.utc))
    assert 21 < min(r.lag) and max(r.lag) <= 30      # events are 40 ms apart from T0
