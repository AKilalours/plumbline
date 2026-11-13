"""The public "add a source" kit, tested on the worked example (examples/sources/usgs_quakes.py) against a
local stand-in for the USGS API. Each test names the promise the kit makes to whoever writes a source."""
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pytest
import yaml

from plumbline import kit
from plumbline.store.usgs_server import Feed, serve

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "sources" / "usgs_quakes.py"


@pytest.fixture
def feed():
    f = Feed.generated()
    srv, url = serve(f)
    f.url = url
    yield f
    srv.shutdown()


def quakes(feed):
    cls = kit._load(str(EXAMPLE))
    cls.url = feed.url

    def make():
        s = cls()
        s._sleep = lambda _s: None        # retries are tested; waiting for them is not
        return s
    return make


@pytest.fixture
def contract(tmp_path, feed):
    return kit.draft(quakes(feed)(), out=tmp_path / "usgs_quakes.yaml", workdir=tmp_path / "draft")


def current(wd: Path, name="usgs_quakes"):
    with duckdb.connect(str(wd / "kit.duckdb"), read_only=True) as con:
        return dict(con.execute(f"SELECT event_id, magnitude FROM current_{name}").fetchall())


def test_the_worked_example_lands_every_event_once(tmp_path, feed, contract):
    r = kit.run(quakes(feed)(), workdir=tmp_path / "w", contract=contract)
    assert (r.rows_landed, r.rows_loaded, r.rows_quarantined) == (1200, 1200, 0)
    assert len(current(tmp_path / "w")) == 1200


def test_the_next_run_asks_only_for_what_changed_and_picks_up_a_revision(tmp_path, feed, contract):
    make = quakes(feed)
    kit.run(make(), workdir=tmp_path / "w", contract=contract)
    feed.revise(["zz0000003"], datetime(2026, 12, 1, tzinfo=timezone.utc), 4.4)
    r = kit.run(make(), workdir=tmp_path / "w", contract=contract)
    assert (r.rows_landed, r.rows_loaded) == (1, 1)
    assert current(tmp_path / "w")["zz0000003"] == 4.4


def test_a_crash_between_pages_resumes_and_loses_nothing(tmp_path, feed, contract):
    make = quakes(feed)

    def crash(n):
        if n == 1:
            raise KeyboardInterrupt
    with pytest.raises(KeyboardInterrupt):
        kit.run(make(), workdir=tmp_path / "w", contract=contract, after_page=crash)
    kit.run(make(), workdir=tmp_path / "w", contract=contract)
    assert len(current(tmp_path / "w")) == 1200


def test_verify_passes_the_worked_example(tmp_path, feed, contract):
    checks = kit.verify(quakes(feed), contract=contract, workdir=tmp_path / "v")
    assert [ok for _, ok, _ in checks] == [True] * 4, checks


def test_verify_catches_a_source_that_saves_its_cursor_before_the_window_is_read(tmp_path, feed, contract):
    """The mistake every new connector makes: the feed is ordered by event time, the cursor is the update
    time, so saving the newest update seen on page 1 skips rows on page 2 that were updated earlier."""
    base = quakes(feed)

    class Careless(base().__class__):
        def pages(self, cursor, size=500):
            since, offset = cursor or "2026-11-01T00:00:00", 1
            while True:
                body = self.get_json(self.url, format="geojson", updatedafter=since, orderby="time-asc", limit=size, offset=offset)
                rows = [{"event_id": f["id"], "updated_at": kit_iso(f["properties"]["updated"]), "magnitude": f["properties"]["mag"],
                         "place": f["properties"]["place"], "happened_at": kit_iso(f["properties"]["time"]),
                         "status": f["properties"]["status"]} for f in body["features"]]
                yield kit.Page(rows, cursor=max(r["updated_at"] for r in rows) if rows else None)
                if len(rows) < size:
                    return
                offset += size

    def make():
        s = Careless()
        s._sleep = lambda _s: None
        return s
    checks = dict((n, ok) for n, ok, _ in kit.verify(make, contract=contract, workdir=tmp_path / "v"))
    assert checks["a crash after page 1 resumes and loses nothing"] is False


def kit_iso(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]


def test_429_waits_as_told_and_5xx_backs_off_then_succeeds(tmp_path, feed, contract):
    feed.fail, feed.retry_after = [429, 503, 502], "7"
    s = quakes(feed)()
    waits = []
    s._sleep = waits.append
    r = kit.run(s, workdir=tmp_path / "w", contract=contract)
    assert r.rows_loaded == 1200 and r.retries == 3
    assert waits[0] == 7.0 and all(w > 0 for w in waits[1:])


def test_a_refusal_a_retry_cannot_fix_fails_at_once_with_the_reason(feed):
    s = quakes(feed)()
    feed.fail = [400]
    with pytest.raises(kit.SourceError, match="HTTP 400"):
        list(s.pages(None))
    assert feed.requests == 1


def test_it_gives_up_loudly_after_its_retries(feed):
    s = quakes(feed)()
    s.max_retries, feed.fail = 2, [503] * 5
    with pytest.raises(kit.SourceError, match="gave up after 3 tries"):
        list(s.pages(None))


def test_a_column_the_contract_does_not_list_is_held_back_not_loaded(tmp_path, feed, contract):
    make = quakes(feed)
    cls = make().__class__

    class WithDepth(cls):
        def pages(self, cursor, size=500):
            for p in super().pages(cursor, size):
                yield kit.Page([r | {"depth_km": 10} for r in p.rows], p.cursor)

    s = WithDepth()
    r = kit.run(s, workdir=tmp_path / "w", contract=contract)
    assert r.held_back == ["depth_km"] and r.rows_loaded == 1200
    first = next((tmp_path / "w" / "run" / "sources" / "usgs_quakes").glob("*.csv"))
    assert "depth_km" not in first.read_text().splitlines()[0]


def test_the_draft_loads_uses_the_sources_key_and_leaves_personal_fields_out(tmp_path, feed):
    make = quakes(feed)

    class WithOwner(make().__class__):
        def pages(self, cursor, size=500):
            for p in super().pages(cursor, size):
                yield kit.Page([r | {"owner_name": "Pat Example"} for r in p.rows], p.cursor)

    out = kit.draft(WithOwner(), out=tmp_path / "d.yaml", workdir=tmp_path / "dw")
    spec = yaml.safe_load(out.read_text())
    assert spec["key"] == ["event_id"] and spec["latest_by"] == ["updated_at"]
    assert "owner_name" not in spec["columns"] and "owner_name" in out.read_text().splitlines()[1]
    from plumbline.ingest.contracts import load_contract
    assert load_contract(out).name == "usgs_quakes"


def test_a_source_needs_a_valid_name_and_never_shows_its_headers():
    class Bad(kit.Source):
        name = "Bad Name"
    with pytest.raises(ValueError, match="snake_case"):
        Bad()

    class Tok(kit.Source):
        name, headers = "tok", {"Authorization": "Bearer secret-value"}
    assert "secret" not in repr(Tok())


def test_run_without_a_reviewed_contract_says_what_to_do(tmp_path, feed):
    with pytest.raises(FileNotFoundError, match="kit draft"):
        kit.run(quakes(feed)(), workdir=tmp_path, contract=tmp_path / "missing.yaml")


def test_the_committed_example_contract_passes_verify(tmp_path, feed):
    contract = Path(__file__).resolve().parents[1] / "sources" / "kit" / "usgs_quakes.yaml"
    checks = kit.verify(quakes(feed), contract=contract, workdir=tmp_path / "v")
    assert all(ok for _, ok, _ in checks), checks


def test_the_guides_commands_work_as_typed_through_python_dash_m(tmp_path, feed):
    """Found by the second engineer: under `python -m plumbline.kit`, kit.py runs as __main__, a second copy
    of the module, so the source file's Source (from plumbline.kit) never matched and every command said
    "found 0". The unit tests called the functions directly and never ran the CLI the guide tells people to."""
    import os
    import subprocess
    import sys
    src = tmp_path / "quakes.py"
    src.write_text(EXAMPLE.read_text().replace('URL = "https://earthquake.usgs.gov/fdsnws/event/1/query"', f'URL = "{feed.url}"'))
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"), "NO_PROXY": "127.0.0.1,localhost"}
    contract = Path(__file__).resolve().parents[1] / "sources" / "kit" / "usgs_quakes.yaml"
    run = lambda *a: subprocess.run([sys.executable, "-m", "plumbline.kit", *a], capture_output=True, text=True,
                                    env=env, cwd=tmp_path, timeout=300)
    r = run("run", str(src), "--contract", str(contract), "--workdir", str(tmp_path / "w"))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "1200 rows landed" in r.stdout
    v = run("verify", str(src), "--contract", str(contract), "--workdir", str(tmp_path / "v"))
    assert v.returncode == 0 and v.stdout.count("PASS") == 4, v.stdout + v.stderr


def test_a_window_longer_than_the_page_limit_skips_the_cursor_check_instead_of_failing(tmp_path, feed, contract):
    """Found by the second engineer: a source that rightly holds its cursor until a long window ends failed
    "a finished run leaves a cursor" only because verify stopped it at 3 pages."""
    checks = kit.verify(quakes(feed), contract=contract, workdir=tmp_path / "v", max_pages=2)
    status = {n: ok for n, ok, _ in checks}
    assert status["a finished run leaves a cursor"] is None
    assert all(ok is not False for ok in status.values())


def test_a_run_whose_window_ends_exactly_at_the_page_limit_is_finished(tmp_path, feed, contract):
    r = kit.run(quakes(feed)(), workdir=tmp_path / "w", contract=contract, max_pages=3)      # 1200 rows = 3 pages of 500
    assert r.finished and r.cursor is not None
    assert not kit.run(quakes(feed)(), workdir=tmp_path / "x", contract=contract, max_pages=2).finished
