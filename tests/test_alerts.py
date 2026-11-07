"""Alerts reach a person once per problem, follow up instead of repeating, and close when the problem clears.
Run against a local stand-in for GitHub's issues API that records every call."""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from plumbline.ingest.alerts import GitHub, problems, reconcile
from plumbline.ingest.freshness import Feed

NOW = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)


def run(day, ok=True, **kw):
    return {"run_at": f"2026-11-{day:02d}T11:23:00Z", "ok": ok, **kw}


class FakeGitHub:
    def __init__(self):
        self.issues, self.calls, self.slack = {}, [], []
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, obj=None):
                b = b"" if obj is None else json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

            def _body(self):
                n = int(self.headers.get("Content-Length") or 0)
                return json.loads(self.rfile.read(n) or b"{}")

            def do_GET(self):
                fake.calls.append(("GET", self.path, self.headers.get("Authorization")))
                self._send(200, [{"number": n, "title": i["title"]} for n, i in fake.issues.items() if i["state"] == "open"])

            def do_POST(self):
                b = self._body()
                fake.calls.append(("POST", self.path, b))
                if self.path == "/slack":
                    fake.slack.append(b["text"])
                    return self._send(200)
                if self.path.endswith("/issues"):
                    n = len(fake.issues) + 1
                    fake.issues[n] = {"title": b["title"], "body": b["body"], "labels": b["labels"], "state": "open", "comments": []}
                    return self._send(201, {"number": n})
                n = int(self.path.split("/")[-2])
                fake.issues[n]["comments"].append(b["body"])
                self._send(201, {})

            def do_PATCH(self):
                b = self._body()
                fake.calls.append(("PATCH", self.path, b))
                fake.issues[int(self.path.split("/")[-1])]["state"] = b["state"]
                self._send(200, {})

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"


@pytest.fixture
def gh():
    f = FakeGitHub()
    yield f, GitHub("AKilalours/plumbline", "token-for-tests", api=f.url)
    f.srv.shutdown()


def feed(runs, name="NYC Open Data permits"):
    return Feed(name, 26, lambda: runs)


def test_a_healthy_week_raises_nothing():
    assert problems([feed([run(d) for d in range(8, 15)])], NOW) == {}


def test_a_failed_run_a_late_feed_and_a_quarantine_spike_are_each_named():
    p = problems([feed([run(13), run(14, ok=False, error="StoreAPIError: HTTP 503")], "NYC"),
                  feed([run(11)], "Stream"),
                  feed([run(14, rows=1000, rows_quarantined=80, quarantine_reasons={"bad_zip": 80})], "Permits")], NOW)
    assert set(p) == {"NYC: the latest run failed", "Stream: no successful run in 72.6 hours", "Permits: 8% of rows quarantined"}
    assert "HTTP 503" in p["NYC: the latest run failed"] and "not have run at all" in p["Stream: no successful run in 72.6 hours"]


def test_a_problem_opens_one_issue_then_follows_up_then_closes_when_it_clears(gh):
    fake, client = gh
    bad = {"NYC: the latest run failed": "Run failed: `HTTP 503`"}
    assert reconcile(bad, client, NOW)["opened"] == ["NYC: the latest run failed"]
    assert reconcile(bad, client, NOW)["still_open"] == ["NYC: the latest run failed"]      # the next day: a comment, not a 2nd issue
    assert len(fake.issues) == 1 and fake.issues[1]["labels"] == ["feed-alert"] and len(fake.issues[1]["comments"]) == 1
    assert reconcile({}, client, NOW)["closed"] == ["NYC: the latest run failed"]
    assert fake.issues[1]["state"] == "closed" and "Recovered" in fake.issues[1]["comments"][-1]
    assert all(c[2] == "Bearer token-for-tests" for c in fake.calls if c[0] == "GET")


def test_slack_hears_new_and_resolved_alerts_only(gh):
    fake, client = gh
    bad = {"Stream: the latest run failed": "x"}
    reconcile(bad, client, NOW, slack_url=fake.url + "/slack")
    reconcile(bad, client, NOW, slack_url=fake.url + "/slack")            # still failing: no second message
    reconcile({}, client, NOW, slack_url=fake.url + "/slack")
    assert len(fake.slack) == 2 and ":rotating_light:" in fake.slack[0] and "resolved" in fake.slack[1]


def test_without_a_token_alerts_are_printed_and_nothing_is_sent(capsys):
    r = reconcile({"NYC: the latest run failed": "x"}, None, NOW)
    assert r["sent"] is False and "ALERT NYC: the latest run failed" in capsys.readouterr().out
