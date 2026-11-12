"""Tell a person when a real feed needs one: a failed run, a feed past its promise, a spike of quarantined rows.

    python -m plumbline.ingest.alerts               # after the daily runs (reads the run history files here)
    python -m plumbline.ingest.alerts --remote      # from another job: reads the history the daily job published

Each problem becomes one GitHub issue (label `feed-alert`), opened through the Actions token the workflow
already has; GitHub emails the repository owner about new issues, so this needs no extra account or secret.
The same problem on a later run adds a comment instead of a second issue, and when the problem clears the issue
is closed with a note saying when. If a SLACK_WEBHOOK_URL secret is set, new and resolved alerts are posted
there too. Without GITHUB_TOKEN and GITHUB_REPOSITORY (on a laptop), alerts are printed, nothing is sent.

Why two places: a job that never runs cannot report that it did not run. The daily data job reports its own
failures; the separate daily app check runs this with --remote, so a data job that silently stopped shows up
as a late feed.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from datetime import datetime, timezone

from .freshness import Feed, health

LABEL = "feed-alert"
QUARANTINE_ALERT = 0.05            # more than 5% of a real feed's rows held back in its latest run


def problems(feeds: list[Feed], now: datetime) -> dict[str, str]:
    """{issue title: body} for every feed that needs a person, right now."""
    out = {}
    for f in feeds:
        runs = f.history()
        h = health(runs, f.promise_hours, now)
        last = max(runs, key=lambda r: r["run_at"]) if runs else None
        if h["status"] == "late":
            out[f"{f.name}: no successful run in {h['hours_since_success']:g} hours"] = (
                f"The last successful run was {h['last_success']} ({h['hours_since_success']:g} h ago); the promise is "
                f"{f.promise_hours:g} h. Successful runs in the last 7 days: {h['good_days_of_7']} of 7."
                + (f"\n\nLast error: `{h['last_error']}`" if h["last_error"] else "\n\nNo failed run is recorded: the job may "
                   "not have run at all. Check the Actions tab."))
        if h["status"] == "behind":
            out[f"{f.name}: the data is {h['hours_behind']:g} hours behind the source"] = (
                f"Runs succeed, but the newest update landed is {h['hours_behind']:g} h older than the publisher's newest "
                f"(promise: {f.promise_hours:g} h). A sync that passes without moving forward: check for a reload the "
                f"connector cannot get past, or a page budget too small for the day's changes.")
        if last and not last.get("ok"):
            out[f"{f.name}: the latest run failed"] = f"Run at {last['run_at']} failed:\n\n`{last.get('error', 'no reason recorded')}`"
        rc = (last or {}).get("reconcile")
        if isinstance(rc, dict) and rc.get("error"):
            out[f"{f.name}: the check against the source failed"] = f"Run at {last['run_at']}: `{rc['error']}`. The sync itself succeeded."
        elif isinstance(rc, dict) and rc.get("checked") and rc.get("gone", 0) / rc["checked"] > 0.01:
            out[f"{f.name}: rows we hold are gone from the source"] = (
                f"Run at {last['run_at']}: {rc['gone']:,} of {rc['checked']:,} rows checked are no longer at the source "
                f"(for example {', '.join(rc.get('gone_ids', [])[:5])}). Nothing was deleted: decide whether the source "
                "removed them on purpose.")
        if last and last.get("publish_error"):
            out[f"{f.name}: the cloud publish failed"] = (
                f"Run at {last['run_at']} synced and loaded, but publishing to the cloud warehouse failed (the cursor was "
                f"saved, so nothing is re-pulled):\n\n`{last['publish_error']}`")
        if last and last.get("ok") and last.get("rows"):
            share = (last.get("rows_quarantined") or 0) / last["rows"]
            if share > QUARANTINE_ALERT:
                out[f"{f.name}: {share:.0%} of rows quarantined"] = (
                    f"Run at {last['run_at']}: {last.get('rows_quarantined'):,} of {last['rows']:,} rows failed the contract. "
                    f"Reasons: {json.dumps(last.get('quarantine_reasons') or {})}")
    return out


class GitHub:
    """The few issue calls alerting needs, over plain HTTPS with the workflow's own token."""

    def __init__(self, repo: str, token: str, api: str = "https://api.github.com"):
        self.repo, self.token, self.api = repo, token, api.rstrip("/")

    def _call(self, method: str, path: str, body: dict | None = None):
        req = urllib.request.Request(f"{self.api}/repos/{self.repo}{path}", method=method,
                                     data=None if body is None else json.dumps(body).encode(),
                                     headers={"Authorization": f"Bearer {self.token}", "Accept": "application/vnd.github+json",
                                              "Content-Type": "application/json", "User-Agent": "plumbline-alerts"})
        with urllib.request.urlopen(req, timeout=20) as r:
            text = r.read().decode()
            return json.loads(text) if text else None

    def open_alerts(self) -> dict[str, int]:
        issues = self._call("GET", f"/issues?state=open&labels={LABEL}&per_page=100") or []
        return {i["title"]: i["number"] for i in issues if "pull_request" not in i}

    def open(self, title: str, body: str) -> int:
        return self._call("POST", "/issues", {"title": title, "body": body, "labels": [LABEL]})["number"]

    def comment(self, number: int, body: str) -> None:
        self._call("POST", f"/issues/{number}/comments", {"body": body})

    def close(self, number: int, body: str) -> None:
        self.comment(number, body)
        self._call("PATCH", f"/issues/{number}", {"state": "closed", "state_reason": "completed"})


def slack(url: str, text: str) -> None:
    req = urllib.request.Request(url, data=json.dumps({"text": text}).encode(), headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=20).read()


def reconcile(found: dict[str, str], gh: GitHub | None, now: datetime, slack_url: str | None = None) -> dict:
    """Open an issue per new problem, comment on one still open, close those that cleared."""
    stamp = now.strftime("%Y-%m-%d %H:%M UTC")
    if gh is None:
        for t, b in found.items():
            print(f"ALERT {t}\n  {b}")
        return {"opened": list(found), "still_open": [], "closed": [], "sent": False}
    open_now = gh.open_alerts()
    opened, still, closed = [], [], []
    for title, body in found.items():
        if title in open_now:
            gh.comment(open_now[title], f"Still happening at {stamp}.\n\n{body}")
            still.append(title)
        else:
            gh.open(title, f"{body}\n\nFirst seen {stamp} by the daily job.")
            opened.append(title)
    for title, number in open_now.items():
        if title not in found:
            gh.close(number, f"Recovered: the problem was gone at {stamp}.")
            closed.append(title)
    if slack_url and (opened or closed):
        lines = [f":rotating_light: {t}" for t in opened] + [f":white_check_mark: resolved: {t}" for t in closed]
        slack(slack_url, "Plumbline feeds\n" + "\n".join(lines))
    return {"opened": opened, "still_open": still, "closed": closed, "sent": True}


def local_feeds() -> list[Feed]:
    from . import open_data_daily as OD
    from . import stream_daily as SD
    return [Feed("NYC Open Data permits", 26, lambda: OD.history()), Feed("Wikimedia live stream", 26, lambda: SD.history())]


def main(argv: list[str] | None = None) -> int:
    import argparse
    from .freshness import feeds
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--remote", action="store_true", help="read the published history instead of the files here")
    a = ap.parse_args(argv)
    now = datetime.now(timezone.utc)
    found = problems(feeds() if a.remote else local_feeds(), now)
    token, repo = os.environ.get("GITHUB_TOKEN"), os.environ.get("GITHUB_REPOSITORY")
    r = reconcile(found, GitHub(repo, token) if token and repo else None, now, os.environ.get("SLACK_WEBHOOK_URL") or None)
    print(json.dumps(r))
    return 0


if __name__ == "__main__":
    sys.exit(main())
