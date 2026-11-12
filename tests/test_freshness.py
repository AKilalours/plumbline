"""Feed health is counted from the run history, failures included, never assumed."""
from __future__ import annotations

from datetime import datetime, timezone

from plumbline.ingest.freshness import health

NOW = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)


def run(day: int, ok: bool = True, hour: int = 11) -> dict:
    r = {"run_at": f"2026-11-{day:02d}T{hour:02d}:23:00Z", "ok": ok}
    if not ok:
        r["error"] = "StoreAPIError: gave up after 8 retries (last: 503)"
    return r


def test_a_week_of_daily_successes_is_on_time_with_a_seven_run_streak():
    h = health([run(d) for d in range(8, 15)], 26, NOW)
    assert h["status"] == "on time" and h["streak"] == 7 and h["good_days_of_7"] == 7 and h["failed_7"] == 0


def test_a_failure_breaks_the_streak_and_is_reported_with_its_reason():
    h = health([run(d) for d in range(8, 12)] + [run(12, ok=False)] + [run(13), run(14)], 26, NOW)
    assert h["streak"] == 2 and h["failed_7"] == 1 and h["good_days_of_7"] == 6 and "503" in h["last_error"]


def test_a_feed_whose_last_success_is_past_its_promise_is_late_even_if_it_kept_running():
    h = health([run(10), run(11, ok=False), run(12, ok=False), run(13, ok=False)], 26, NOW)
    assert h["status"] == "late" and h["hours_since_success"] == 96.6 and h["streak"] == 0


def test_runs_out_of_order_are_sorted_by_time_first():
    h = health([run(14), run(13, ok=False), run(12)], 26, NOW)
    assert h["streak"] == 1


def test_no_history_says_so_rather_than_guessing():
    assert health([], 26, NOW)["status"] == "never ran"
    assert health([run(14, ok=False)], 26, NOW)["status"] == "no success yet"


def test_runs_that_succeed_while_the_data_falls_behind_the_source_are_behind_not_on_time():
    """2026-11-10 to 12: every NYC run passed and the feed read "on time" while the landed permits fell a day behind."""
    runs = [run(d) for d in range(8, 14)] + [{**run(14), "source_newest": "2026-11-14T10:00:00.000", "hours_behind_source": 38.3}]
    h = health(runs, 26, NOW)
    assert h["status"] == "behind" and h["hours_behind"] == 38.3


def test_a_run_from_before_lag_was_measured_against_the_source_is_not_read_as_lag():
    h = health([run(d) for d in range(8, 14)] + [{**run(14), "hours_behind_source": 40.1}], 26, NOW)
    assert h["status"] == "on time" and h["hours_behind"] is None
