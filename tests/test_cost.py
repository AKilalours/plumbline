"""Cost per run: billed minutes are the job's time rounded up, priced at the runner's list rate, and a public
repository is charged nothing; earlier runs are shown only as a lower bound from their steps."""
import json

from plumbline.ingest import cost as C


def test_a_job_is_billed_whole_minutes_at_the_list_rate_and_a_public_repo_is_charged_nothing():
    j = C.job_record(1000.0, 1000.0 + 61, feeds={"nyc": {"seconds": 40, "rows": 2438, "ok": True},
                                                 "wiki": {"seconds": 12, "events": 4648, "ok": True}})
    assert (j["billed_minutes"], j["list_usd"], j["charged_usd"]) == (2, 0.012, 0.0)
    assert j["rows"] == 7086 and j["list_usd_per_1k_rows"] == round(0.012 / 7086 * 1000, 4)
    assert C.job_record(0, 59, public=False)["charged_usd"] == 0.006


def test_the_summary_projects_a_month_from_measured_jobs_only():
    jobs = [C.job_record(0, 170), C.job_record(0, 230)]               # 3 and 4 billed minutes
    s = C.summary(jobs, {})
    assert s["measured"]["billed_minutes_avg"] == 3.5 and s["measured"]["list_usd_per_30_days"] == round(3.5 * 0.006 * 8 * 30, 2)
    assert "steps_only" not in s


def test_runs_from_before_job_records_are_a_lower_bound_from_their_steps():
    s = C.summary([], {"nyc": [{"seconds": 100}, {"seconds": 20}], "wiki": [{"seconds": 121}]})
    assert "measured" not in s
    assert s["steps_only"]["seconds_per_run"] == 181.0 and s["steps_only"]["list_usd_per_run_at_least"] == 0.024     # 181 s rounds up to 4 minutes


def test_record_appends_one_line_with_only_this_jobs_feed_lines(tmp_path, monkeypatch):
    res = tmp_path / "docs" / "results"
    (res / "open_data").mkdir(parents=True)
    (res / "streams").mkdir(parents=True)
    (res / "open_data" / "runs.jsonl").write_text(json.dumps({"run_at": "2026-11-12T16:09:40Z", "seconds": 97.9, "rows": 0, "ok": True}) + "\n")
    (res / "streams" / "runs.jsonl").write_text(json.dumps({"run_at": "2026-11-12T11:00:00Z", "seconds": 120, "events": 9, "ok": True}) + "\n")
    monkeypatch.setattr(C, "ROOT", tmp_path)
    started = 1794499780.0                                            # 2026-11-12T16:09:40Z
    out = tmp_path / "jobs.jsonl"
    assert C.main(["record", "--started", str(started), "--event", "schedule", "--out", str(out)]) == 0
    (j,) = [json.loads(l) for l in out.read_text().splitlines()]
    assert list(j["feeds"]) == ["dob_now_permits"] and j["trigger"] == "schedule" and j["billed_minutes"] >= 1
