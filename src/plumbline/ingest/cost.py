"""What each scheduled run costs: runner minutes and dollars, measured inside the job, from real runs.

    python -m plumbline.ingest.cost record --started $JOB_STARTED     # last step of the daily job
    python -m plumbline.ingest.cost                                   # the summary, from the run history

The daily job runs on a standard GitHub-hosted Linux runner (2 cores). GitHub bills a job's minutes rounded up
to the whole minute, at $0.006 a minute for that runner, and charges nothing for standard runners in a public
repository, which this is. So each run records both: the list price (what the same job would cost in a private
repository) and what was charged ($0). Checked against GitHub's pricing page on 2026-11-12; PRICE_SOURCE.

Before job-level records began (2026-11-12), only each feed step's own seconds were recorded. Those runs are
shown as a lower bound ("steps only"): setup, checkout and installs are not in them.

Not counted here, because nothing measures them yet: the cloud warehouse (MotherDuck) and the source APIs.
"""
from __future__ import annotations

import argparse
import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
USD_PER_MINUTE = 0.006                      # standard Linux 2-core runner
PRICE_SOURCE = "https://docs.github.com/en/billing/reference/actions-runner-pricing"
PUBLIC_REPO = True
JOBS = ROOT / "docs" / "results" / "open_data" / "job_runs.jsonl"
JOBS_URL = "https://raw.githubusercontent.com/AKilalours/plumbline/open-data-history/job_runs.jsonl"


def jobs(url: str | None = JOBS_URL, timeout: float = 4.0) -> list[dict]:
    """Job cost lines from the history branch, else the local copy; a line that does not parse is skipped."""
    if url:
        import urllib.request
        try:
            with urllib.request.urlopen(url, timeout=timeout) as r:
                return _lines(r.read().decode("utf-8", "replace"))
        except Exception:
            pass
    return _lines(JOBS.read_text()) if JOBS.exists() else []


def _lines(text: str) -> list[dict]:
    out = []
    for line in text.splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if isinstance(r, dict) and "job_seconds" in r and "billed_minutes" in r:
            out.append(r)
    return out


def job_record(started: float, ended: float, *, feeds: dict[str, dict] | None = None, event: str = "local",
               public: bool = PUBLIC_REPO) -> dict:
    """One job's cost. `feeds` maps a feed name to its run line from this job (for seconds and rows)."""
    seconds = max(0.0, ended - started)
    minutes = math.ceil(seconds / 60) if seconds else 0
    feeds = feeds or {}
    rows = sum(int(f.get("rows") or f.get("events") or 0) for f in feeds.values())
    return {"run_at": datetime.fromtimestamp(started, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "trigger": event,
            "job_seconds": round(seconds, 1), "billed_minutes": minutes, "list_usd": round(minutes * USD_PER_MINUTE, 4),
            "charged_usd": 0.0 if public else round(minutes * USD_PER_MINUTE, 4), "usd_per_minute": USD_PER_MINUTE,
            "feeds": {n: {"seconds": f.get("seconds"), "rows": f.get("rows", f.get("events")), "ok": f.get("ok")}
                      for n, f in feeds.items()},
            "rows": rows, "list_usd_per_1k_rows": round(minutes * USD_PER_MINUTE / rows * 1000, 4) if rows else None}


def summary(jobs: list[dict], feed_runs: dict[str, list[dict]], *, runs_per_day: float = 8) -> dict:
    """Measured jobs first; for earlier runs, the feed steps' own seconds as a lower bound."""
    out = {"usd_per_minute": USD_PER_MINUTE, "price_source": PRICE_SOURCE, "public_repo": PUBLIC_REPO}
    if jobs:
        mins = [j["billed_minutes"] for j in jobs]
        avg = sum(mins) / len(mins)
        out["measured"] = {"jobs": len(jobs), "billed_minutes_avg": round(avg, 2), "billed_minutes_max": max(mins),
                           "job_seconds_avg": round(sum(j["job_seconds"] for j in jobs) / len(jobs), 1),
                           "list_usd_per_run": round(avg * USD_PER_MINUTE, 4),
                           "list_usd_per_30_days": round(avg * USD_PER_MINUTE * runs_per_day * 30, 2),
                           "charged_usd_total": round(sum(j["charged_usd"] for j in jobs), 4)}
    steps = {}
    for name, runs in feed_runs.items():
        secs = [float(r["seconds"]) for r in runs if r.get("seconds") is not None]
        if secs:
            steps[name] = {"runs": len(secs), "seconds_avg": round(sum(secs) / len(secs), 1), "seconds_max": round(max(secs), 1)}
    if steps:
        per_run = sum(s["seconds_avg"] for s in steps.values())
        out["steps_only"] = {"feeds": steps, "seconds_per_run": round(per_run, 1),
                             "list_usd_per_run_at_least": round(math.ceil(per_run / 60) * USD_PER_MINUTE, 4)}
    return out


def _last(path: Path) -> dict | None:
    if not path.exists():
        return None
    lines = [l for l in path.read_text().splitlines() if l.strip()]
    try:
        return json.loads(lines[-1]) if lines else None
    except json.JSONDecodeError:
        return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd")
    rec = sub.add_parser("record")
    rec.add_argument("--started", type=float, required=True, help="the job's start, in Unix seconds")
    rec.add_argument("--event", default="local")
    rec.add_argument("--out", default=str(JOBS))
    a = ap.parse_args(argv)
    res = ROOT / "docs" / "results"
    if a.cmd == "record":
        started = a.started
        feeds = {}
        for name, p in (("dob_now_permits", res / "open_data" / "runs.jsonl"), ("wikimedia_recentchange", res / "streams" / "runs.jsonl")):
            r = _last(p)
            if r and r.get("run_at", "") >= datetime.fromtimestamp(started, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"):
                feeds[name] = r                                  # only this job's lines
        j = job_record(started, time.time(), feeds=feeds, event=a.event)
        out = Path(a.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("a") as f:
            f.write(json.dumps(j) + "\n")
        print(json.dumps(j))
        return 0
    from .open_data_daily import _parse
    read = lambda p: _parse(p.read_text()) if p.exists() else []
    print(json.dumps(summary(jobs(None),
                             {"dob_now_permits": read(res / "open_data" / "runs.jsonl"),
                              "wikimedia_recentchange": read(res / "streams" / "runs.jsonl")}), indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
