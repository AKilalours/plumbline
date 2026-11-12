"""Service level objectives for the real feeds, measured from their run history, with error budgets.

    python -m plumbline.ingest.slo          # each feed's objectives; exits 1 when an error budget is spent

Three promises per feed, each a ratio of good to total over a rolling window (28 days, or since the feed
started if it is younger), against a target:

- **Runs succeed** (target 95% of runs). Good: a run that finished. A cloud publish that failed counts as
  bad even though the sync finished, because the warehouse consumers read did not get the rows.
- **Data is fresh** (target 99% of hours). Measured hour by hour: an hour is bad when no run had succeeded in
  the promise before it (26 hours), or when the last successful run measured the landed data more than the
  promise behind the publisher's newest update. That second measure exists from 2026-11-12; before it, only
  the first can be counted, and nothing is inferred for the hours it cannot see.
- **What we hold matches the source** (target 99.5% of rows checked). From the daily reconcile: rows the
  source still has with the same values, of the rows checked. A changed row counts as bad (the warehouse was
  wrong until the reconcile corrected it); so does a row gone from the source.

The error budget is what the target allows to go wrong: (1 - target) of the total. `burn` compares the share
of the budget spent with the share of the window gone: above 1 the budget runs out before the window ends.
A promise with no measurements yet says so; it is never shown as met.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone

TARGETS = {"runs": 0.95, "fresh": 0.99, "matches": 0.995}
LABEL = {"runs": "Runs succeed", "fresh": "Data is fresh", "matches": "What we hold matches the source"}
UNIT = {"runs": "runs", "fresh": "hours", "matches": "rows checked"}


def _at(r: dict) -> datetime:
    return datetime.strptime(r["run_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def _ok(r: dict) -> bool:
    return bool(r.get("ok")) and not r.get("publish_error")


def _budget(good: int, total: int, target: float, elapsed: float) -> dict:
    if not total:
        return {"good": 0, "total": 0, "sli": None, "target": target, "status": "no data yet"}
    allowed = (1 - target) * total
    bad = total - good
    spent = bad / allowed if allowed else (0.0 if not bad else float("inf"))
    return {"good": good, "total": total, "sli": good / total, "target": target, "bad": bad,
            "budget_spent": round(spent, 3), "budget_left": round(max(0.0, 1 - spent), 3),
            "burn": round(spent / elapsed, 2) if elapsed else None,
            "status": "met" if good / total >= target else "breached"}


def measure(runs: list[dict], now: datetime, promise_hours: float = 26, window_days: int = 28) -> dict:
    """{runs, fresh, matches} for one feed's run history, each with its budget."""
    start = now - timedelta(days=window_days)
    rs = sorted((r for r in runs if _at(r) >= start), key=_at)
    if not rs:
        return {k: _budget(0, 0, TARGETS[k], 0) for k in TARGETS}
    began = max(start, _at(rs[0]))
    elapsed = (now - began) / timedelta(days=window_days)

    out = {"runs": _budget(sum(_ok(r) for r in rs), len(rs), TARGETS["runs"], elapsed)}

    hours = int((now - began).total_seconds() // 3600)
    oks = [r for r in rs if r.get("ok")]
    good = counted = 0
    j = -1
    for h in range(hours):
        t = began + timedelta(hours=h + 1)
        while j + 1 < len(oks) and _at(oks[j + 1]) <= t:
            j += 1
        if j < 0:
            continue                        # before the first success: the feed had not started
        counted += 1
        last = oks[j]
        alive = (t - _at(last)) <= timedelta(hours=promise_hours)
        lag = last.get("hours_behind_source") if last.get("source_newest") else None
        good += alive and (lag is None or lag <= promise_hours)
    out["fresh"] = _budget(good, counted, TARGETS["fresh"], elapsed)

    checked = [r["reconcile"] for r in rs if isinstance(r.get("reconcile"), dict) and r["reconcile"].get("checked")]
    out["matches"] = _budget(sum(c["same"] for c in checked), sum(c["checked"] for c in checked), TARGETS["matches"], elapsed)
    out["window"] = {"from": began.strftime("%Y-%m-%d %H:%M UTC"), "days": round((now - began).total_seconds() / 86400, 1)}
    return out


def main() -> int:
    from .freshness import feeds
    now = datetime.now(timezone.utc)
    spent = False
    for f in feeds():
        m = measure(f.history(), now, f.promise_hours)
        print(f"{f.name} (since {m.get('window', {}).get('from', '-')})")
        for k in TARGETS:
            s = m[k]
            if s["sli"] is None:
                print(f"  {LABEL[k]:<34} no data yet")
                continue
            spent |= s["budget_left"] <= 0
            print(f"  {LABEL[k]:<34} {s['sli']:.2%} of {s['total']:,} {UNIT[k]} (target {s['target']:.1%}); "
                  f"budget left {s['budget_left']:.0%}, burn {s['burn']}")
    return 1 if spent else 0


if __name__ == "__main__":
    sys.exit(main())
