"""Hands-on recoveries per feed, counted from the ledger and the run lines rather than from memory."""
from datetime import datetime, timezone
from pathlib import Path

from plumbline.ingest import recoveries as R

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 11, 12, 18, tzinfo=timezone.utc)


def run(at, ok=True, **kw):
    return {"run_at": at, "ok": ok, **kw}


def test_every_postmortem_is_in_the_recoveries_ledger():
    listed = {e["postmortem"] for e in R.ledger()}
    for pm in sorted((ROOT / "docs" / "postmortems").glob("*.md")):
        if pm.name.lower() != "readme.md":
            assert f"docs/postmortems/{pm.name}" in listed, f"{pm.name} has no entry in docs/reliability/recoveries.yaml"


def test_every_ledger_entry_names_a_real_feed_and_ends_after_it_starts():
    feeds = {"dob_now_permits", "wikimedia_recentchange"}
    for e in R.ledger():
        assert e["feed"] in feeds and R._t(e["recovered"]) > R._t(e["started"]), e
        assert (ROOT / e["postmortem"]).exists(), e


def test_a_failure_the_next_run_fixed_with_nobody_acting_counts_as_on_its_own():
    runs = [run("2026-11-10T00:00:00Z"), run("2026-11-10T03:00:00Z", ok=False), run("2026-11-10T06:00:00Z")]
    m = R.measure("f", runs, [], now=NOW)
    assert (m["failed_runs"], m["on_its_own"], m["hands_on"]) == (1, 1, 0)


def test_a_failure_a_person_fixed_is_hands_on_with_how_long_the_feed_was_hurt():
    runs = [run("2026-11-10T03:00:00Z", ok=False), run("2026-11-10T06:00:00Z")]
    e = [{"feed": "f", "started": "2026-11-10T03:00:00Z", "recovered": "2026-11-10T06:00:00Z", "postmortem": "pm.md"}]
    m = R.measure("f", runs, e, now=NOW)
    assert (m["on_its_own"], m["hands_on"], m["hours_hurt_median"], m["runs_per_hands_on"]) == (0, 1, 3.0, 2.0)


def test_a_run_started_by_hand_with_no_ledger_entry_counts_as_hands_on_and_is_named():
    runs = [run("2026-11-10T00:00:00Z"), run("2026-11-10T01:00:00Z", trigger="workflow_dispatch")]
    m = R.measure("f", runs, [], now=NOW)
    assert m["hands_on"] == 1 and m["by_hand_unexplained"] == ["2026-11-10T01:00:00Z"]


def test_a_rerun_by_hand_during_a_written_up_recovery_is_not_counted_twice():
    runs = [run("2026-11-10T03:00:00Z", ok=False), run("2026-11-10T04:00:00Z", trigger="workflow_dispatch")]
    e = [{"feed": "f", "started": "2026-11-10T03:00:00Z", "recovered": "2026-11-10T04:00:00Z"}]
    assert R.measure("f", runs, e, now=NOW)["hands_on"] == 1


def test_retries_inside_runs_are_counted_as_absorbed_not_as_recoveries():
    m = R.measure("f", [run("2026-11-10T00:00:00Z", retries=4), run("2026-11-10T03:00:00Z", retries=1)], [], now=NOW)
    assert (m["retries_absorbed"], m["hands_on"], m["runs_per_hands_on"]) == (5, 0, None)


def test_old_runs_and_recoveries_leave_the_window():
    e = [{"feed": "f", "started": "2026-09-01T00:00:00Z", "recovered": "2026-09-01T02:00:00Z"}]
    m = R.measure("f", [run("2026-09-01T03:00:00Z", ok=False), run("2026-11-10T00:00:00Z")], e, now=NOW)
    assert (m["runs"], m["hands_on"]) == (1, 0)
