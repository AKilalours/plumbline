"""The app's kit and scraper replays are records of real runs: check the records say what the code does."""
from plumbline.app import kit_demo as K


def test_the_playground_records_real_outcomes():
    d = K.playground(n_events=450)
    assert d["clean"]["held"] == 450 and d["crash"]["held"] == 450
    assert d["careless"]["held"] + d["lost"] == 450 and d["lost"] > 0
    again = d["clean"]["steps"][-1]
    assert again["label"] == "again" and again["loaded"] == 0 and sum(p["rows"] for p in again["pages"]) == 450
    assert [c["ok"] for c in d["verify"]["good"]] == [True, True, True, True]          # the whole window is read
    assert [c["ok"] for c in d["verify"]["careless"]][2] is False


def test_the_scraper_record_keeps_robots_first_blocks_login_and_waits_as_asked():
    r = K.scrape(delay=2.5, slow_down=False)
    assert r["requests"][0]["path"] == "/robots.txt" and "/login" not in [q["path"] for q in r["requests"]]
    assert r["blocked"] == [{"t": 0.0, "path": "/login"}]
    assert [w["secs"] for w in r["waits"]] == [2.5, 2.5, 2.5]
    told = K.scrape(delay=2.0, slow_down=True)
    assert [q["status"] for q in told["requests"]][:3] == [200, 429, 200] and told["waits"][0]["secs"] == 6.0
    assert all(w["secs"] == 4.0 for w in told["waits"][1:])                         # twice as slow after the 429


def test_an_unreadable_robots_txt_means_one_request_and_nothing_else():
    r = K.scrape(robots="cannot be read (503)")
    assert [q["path"] for q in r["requests"]] == ["/robots.txt"] and "nothing is allowed" in r["outcome"]


def test_the_playground_cleans_up_its_runs(tmp_path, monkeypatch):
    import tempfile
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    K.playground(n_events=300)
    assert list(tmp_path.iterdir()) == []


def test_the_reload_replay_runs_the_real_connector_and_records_what_it_did(tmp_path, monkeypatch):
    import tempfile
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    r = K.reload_replay(block=600, budget=2, changes=10)
    skip, read = r["lanes"]["skip"], r["lanes"]["read"]
    assert len(skip) == 1 and skip[0]["skipped"] == 590 and skip[0]["real"] == 10      # counted, passed, changes landed
    assert len(read) == 3 and [x["real"] for x in read] == [0, 0, 10]                  # 600 rows at 200 a run: the third ends with the changes
    assert all(x["capped"] for x in read[:-1])
    assert list(tmp_path.iterdir()) == []
