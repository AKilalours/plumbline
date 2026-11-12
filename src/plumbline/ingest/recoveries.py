"""Hands-on recoveries per feed: how often a person had to act, and how long the feed was hurt.

The ingestion role is measured by two numbers: time to onboard a source, and how many recoveries need a
person. This module counts the second from evidence, not memory:

- **hands-on:** an entry in docs/reliability/recoveries.yaml (every postmortem must have one; a test checks),
  plus any run a person started by hand (`trigger: workflow_dispatch` on the run line, recorded since
  2026-11-12) that has no entry: a person touched the feed and nobody wrote down why.
- **on its own:** a failed run followed by a good run with no hands-on entry in between.
- **retries:** failures a run absorbed itself (429, 5xx, dropped connections), never seen by a consumer.

    python -m plumbline.ingest.recoveries          # the table, from the run history
"""
from __future__ import annotations

import json
import statistics
from datetime import datetime, timedelta
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
LEDGER = ROOT / "docs" / "reliability" / "recoveries.yaml"


def _t(s: str) -> datetime:
    return datetime.fromisoformat(str(s).replace("Z", "+00:00"))


def ledger(path: Path = LEDGER) -> list[dict]:
    return yaml.safe_load(path.read_text()) or [] if path.exists() else []


def measure(feed: str, runs: list[dict], entries: list[dict], *, now: datetime, window_days: int = 28) -> dict:
    since = now - timedelta(days=window_days)
    runs = sorted((r for r in runs if _t(r["run_at"]) >= since), key=lambda r: r["run_at"])
    mine = [e for e in entries if e["feed"] == feed and _t(e["recovered"]) >= since]
    hours = [(_t(e["recovered"]) - _t(e["started"])).total_seconds() / 3600 for e in mine]

    def hands_on_between(a: str, b: str) -> bool:
        return any(_t(a) <= _t(e["recovered"]) <= _t(b) for e in mine)

    on_own, unexplained = 0, []
    for prev, cur in zip(runs, runs[1:]):
        if not prev.get("ok") and cur.get("ok") and not hands_on_between(prev["run_at"], cur["run_at"]):
            on_own += 1
    for r in runs:
        if r.get("trigger") == "workflow_dispatch" and not any(
                _t(e["started"]) <= _t(r["run_at"]) <= _t(e["recovered"]) + timedelta(hours=1) for e in mine):
            unexplained.append(r["run_at"])
    hands_on = len(mine) + len(unexplained)
    return {"feed": feed, "window_days": window_days, "runs": len(runs),
            "failed_runs": sum(1 for r in runs if not r.get("ok")),
            "hands_on": hands_on, "by_hand_unexplained": unexplained, "on_its_own": on_own,
            "retries_absorbed": sum(int(r.get("retries") or 0) for r in runs),
            "runs_per_hands_on": round(len(runs) / hands_on, 1) if hands_on else None,
            "hours_hurt_median": round(statistics.median(hours), 1) if hours else None,
            "hours_hurt_max": round(max(hours), 1) if hours else None,
            "postmortems": sorted({e["postmortem"] for e in mine if e.get("postmortem")})}


def table(feeds: dict[str, list[dict]], *, now: datetime, entries: list[dict] | None = None) -> list[dict]:
    entries = ledger() if entries is None else entries
    return [measure(f, runs, entries, now=now) for f, runs in feeds.items()]


def main(argv=None) -> int:
    from datetime import timezone
    from . import open_data_daily
    hist = ROOT / "docs" / "results"
    feeds = {"dob_now_permits": open_data_daily._parse((hist / "open_data" / "runs.jsonl").read_text())
             if (hist / "open_data" / "runs.jsonl").exists() else [],
             "wikimedia_recentchange": open_data_daily._parse((hist / "streams" / "runs.jsonl").read_text())
             if (hist / "streams" / "runs.jsonl").exists() else []}
    for row in table(feeds, now=datetime.now(timezone.utc)):
        print(json.dumps(row))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
