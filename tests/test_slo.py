"""Service level objectives, counted from run lines: what is good, what spends the budget, and what is not
claimed when it was not measured."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from plumbline.ingest.slo import measure

NOW = datetime(2026, 11, 15, 0, 0, tzinfo=timezone.utc)


def run(hours_ago: float, ok: bool = True, **kw) -> dict:
    t = NOW - timedelta(hours=hours_ago)
    return {"run_at": t.strftime("%Y-%m-%dT%H:%M:%SZ"), "ok": ok, **kw}


def test_a_failed_run_and_a_failed_publish_both_spend_the_runs_budget():
    m = measure([run(30), run(20, ok=False), run(10, publish_error="x"), run(1)], NOW)["runs"]
    assert (m["good"], m["total"], m["bad"]) == (2, 4, 2) and m["status"] == "breached"


def test_hours_without_a_success_within_the_promise_are_stale():
    # a success 60 h ago, then 2 h ago; each hour is judged at its end: the 26 ending 59..34 h ago are within
    # 26 h of the first success, the 31 ending 33..3 h ago are not, the 3 ending 2..0 h ago follow the second
    m = measure([run(60), run(2)], NOW, promise_hours=26)["fresh"]
    assert (m["good"], m["total"]) == (26 + 3, 60) and m["status"] == "breached"


def test_a_run_that_measured_its_data_behind_the_source_makes_the_hours_after_it_stale():
    m = measure([run(10, source_newest="x", hours_behind_source=30.0), run(4, source_newest="x", hours_behind_source=0.0)],
                NOW, promise_hours=26)["fresh"]
    assert (m["good"], m["total"]) == (5, 10)      # the 5 hours ending 9..5 h ago follow the run 30 h behind


def test_a_lag_from_before_it_was_measured_against_the_source_is_not_counted():
    m = measure([run(10, hours_behind_source=40.1), run(4)], NOW)["fresh"]
    assert m["good"] == m["total"] == 10


def test_matches_come_from_the_reconcile_and_are_not_claimed_before_it_ran():
    assert measure([run(5), run(1)], NOW)["matches"] == {"good": 0, "total": 0, "sli": None, "target": 0.995, "status": "no data yet"}
    m = measure([run(5, reconcile={"checked": 2000, "same": 1990, "changed": 8, "gone": 2}),
                 run(1, reconcile={"checked": 2000, "same": 2000, "changed": 0, "gone": 0})], NOW)["matches"]
    assert (m["good"], m["total"], m["bad"]) == (3990, 4000, 10) and m["budget_spent"] == 0.5


def test_burn_compares_budget_spent_with_window_gone():
    # 7 of 28 days gone, and a quarter of the run budget spent: burning at exactly the sustainable rate
    rs = [run(h) for h in range(0, 168, 2)] + [run(167.5, ok=False)]
    m = measure(rs, NOW)["runs"]
    assert m["total"] == 85 and abs(m["budget_spent"] - 1 / (0.05 * 85)) < 1e-3
    assert m["burn"] == round(m["budget_spent"] / (167.5 / 24 / 28), 2)


def test_the_hour_list_the_app_draws_is_what_the_slo_counts():
    """fresh_hours was pulled out of measure so the app can draw every hour; both must count the same hours."""
    from datetime import datetime, timezone
    from plumbline.ingest.slo import fresh_hours, measure
    runs = [{"run_at": "2026-11-07T03:00:00Z", "ok": True}, {"run_at": "2026-11-07T09:00:00Z", "ok": False},
            {"run_at": "2026-11-09T12:00:00Z", "ok": True, "source_newest": "x", "hours_behind_source": 30}]
    now = datetime(2026, 11, 10, tzinfo=timezone.utc)
    hs, m = fresh_hours(runs, now), measure(runs, now)["fresh"]
    assert len(hs) == m["total"] and sum(h["good"] for h in hs) == m["good"]
    assert any(not h["good"] for h in hs) and hs[-1]["lag"] == 30
