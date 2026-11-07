"""Feed health for the real, scheduled feeds: is each one on time, and how reliable has it been?

    python -m plumbline.ingest.freshness            # prints each feed's health; exits 1 if any is late

A feed is on time when its last successful run is within its promise (26 hours for a daily job: a day plus
slack for a slow or delayed runner). Reliability is read from the run history, failures included: the streak
of successful runs ending at the latest one, and how many of the last 7 days had at least one success.
Nothing here is estimated: every number is counted from the run lines the jobs wrote.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


@dataclass(frozen=True)
class Feed:
    name: str
    promise_hours: float
    history: object          # () -> list of run records


def _at(r: dict) -> datetime:
    return datetime.strptime(r["run_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def health(runs: list[dict], promise_hours: float, now: datetime) -> dict:
    runs = sorted(runs, key=_at)
    ok = [r for r in runs if r.get("ok")]
    streak = 0
    for r in reversed(runs):
        if not r.get("ok"):
            break
        streak += 1
    days = [(now - timedelta(days=i)).date() for i in range(7)]
    good_days = {_at(r).date() for r in ok}
    last_ok = _at(ok[-1]) if ok else None
    since = None if last_ok is None else round((now - last_ok).total_seconds() / 3600, 1)
    status = "never ran" if not runs else "no success yet" if last_ok is None else "on time" if since <= promise_hours else "late"
    week = [r for r in runs if _at(r) >= now - timedelta(days=7)]
    return {"status": status, "promise_hours": promise_hours, "hours_since_success": since,
            "last_success": last_ok.strftime("%Y-%m-%dT%H:%M:%SZ") if last_ok else None,
            "streak": streak, "good_days_of_7": sum(d in good_days for d in days), "runs_7": len(week),
            "failed_7": sum(not r.get("ok") for r in week), "first_run": runs[0]["run_at"] if runs else None,
            "last_error": next((r.get("error") for r in reversed(runs) if not r.get("ok")), None)}


def feeds() -> list[Feed]:
    from . import open_data_daily as OD
    from . import stream_daily as SD
    return [Feed("NYC Open Data permits", 26, lambda: OD.history(url=OD.HISTORY_URL)),
            Feed("Wikimedia live stream", 26, lambda: SD.history(url=SD.HISTORY_URL))]


def main() -> int:
    now = datetime.now(timezone.utc)
    late = False
    for f in feeds():
        h = health(f.history(), f.promise_hours, now)
        late |= h["status"] == "late"
        print(f"{f.name}: {h['status']}; last success {h['last_success']} ({h['hours_since_success']} h ago, promise "
              f"{f.promise_hours:g} h); {h['streak']} in a row; good on {h['good_days_of_7']} of the last 7 days")
    return 1 if late else 0


if __name__ == "__main__":
    sys.exit(main())
