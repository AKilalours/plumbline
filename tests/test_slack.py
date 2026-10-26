"""The Slack front end: only genuine, fresh requests are served; a retried event or a double click
runs once; the agent ignores bots; approvals work from a button. No real Slack is contacted."""
from __future__ import annotations

import io
import json
import shutil
import urllib.error
import urllib.parse
from pathlib import Path

import duckdb
import pytest

from plumbline.agents.agent import Context
from plumbline.agents.slack import SlackApp, SlackPoster, sign, verify
from plumbline.agents.usage import report

ROOT = Path(__file__).resolve().parents[1]
SECRET = "test-signing-secret"
NOW = 1_800_000_000


class FakePoster:
    def __init__(self):
        self.sent = []

    def post(self, channel, text, thread_ts=None, blocks=None):
        self.sent.append({"channel": channel, "text": text, "thread_ts": thread_ts, "blocks": blocks})
        return {"ok": True}


@pytest.fixture
def app(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    for p in ("README.md", "docs", "skills", "evals", "sources"):
        (shutil.copytree if (ROOT / p).is_dir() else shutil.copy)(ROOT / p, repo / p)
    make_runbook_stale(repo)
    wh = tmp_path / "w.duckdb"
    with duckdb.connect(str(wh)) as con:      # just enough warehouse for the docs check
        con.execute("CREATE TABLE deletion_review (tombstone_id VARCHAR, person_key VARCHAR, reason VARCHAR, logged_at TIMESTAMP)")
        con.execute("CREATE TABLE deletion_tombstones (tombstone_id VARCHAR, key_hash VARCHAR, reason VARCHAR, request_id VARCHAR, strength VARCHAR, created_at TIMESTAMP)")
        con.execute("INSERT INTO deletion_review SELECT 't' || i, 'PK' || lpad(i::VARCHAR, 12, '0'), 'one_key_matches', now() FROM range(7) r(i)")
    ctx = Context(wh, repo, repo / "sources", tmp_path / "agent.duckdb",
                  roles={"U_ASK": {"data-platform"}, "U_DOCS": {"docs"}})
    a = SlackApp(ctx, SECRET, FakePoster(), now=lambda: NOW)
    yield a
    if ctx.agent_con is not None:
        ctx.agent_con.close()


def make_runbook_stale(repo: Path) -> None:
    """Put the old, wrong passage back into this copy of the docs, so the test does not depend on
    whether the real runbook is currently stale (the agent fixed the real one)."""
    import yaml
    c = next(c for c in yaml.safe_load((repo / "docs" / "claims.yaml").read_text())["claims"]
             if c["id"] == "deletion_is_exact_match_only")
    p = repo / c["doc"]
    text = p.read_text()
    if c["text"] not in text:
        assert text.count(c["replacement"]) == 1
        p.write_text(text.replace(c["replacement"], c["text"]))


def _req(body: dict | str, ts: int = NOW, secret: str = SECRET, form: bool = False):
    raw = (urllib.parse.urlencode({"payload": json.dumps(body)}) if form else json.dumps(body)).encode()
    h = {"X-Slack-Request-Timestamp": str(ts), "X-Slack-Signature": sign(secret, str(ts), raw),
         "Content-Type": "application/x-www-form-urlencoded" if form else "application/json"}
    return h, raw


def _event(text, event_id="Ev1", user="U_ASK", **extra):
    return {"type": "event_callback", "event_id": event_id,
            "event": {"type": "app_mention", "user": user, "text": f"<@UBOT> {text}", "channel": "C1", "ts": "1.1", **extra}}


def test_signature_and_freshness_are_required():
    body = b'{"type":"url_verification","challenge":"x"}'
    good = {"X-Slack-Request-Timestamp": str(NOW), "X-Slack-Signature": sign(SECRET, str(NOW), body)}
    assert verify(SECRET, good, body, NOW) is None
    assert verify("wrong-secret", good, body, NOW) == "bad signature"
    assert verify(SECRET, good, body + b" ", NOW) == "bad signature"              # body tampered
    assert "replay" in verify(SECRET, good, body, NOW + 301)                      # captured and re-sent later
    assert verify(SECRET, {}, body, NOW) == "missing signature"


def test_forged_requests_are_refused_and_audited(app):
    h, raw = _req(_event("hello"), secret="attacker")
    assert app.handle_http(h, raw)[0] == 401
    assert not app.poster.sent
    assert app.ctx.agent_con.execute("SELECT count(*) FROM ops.audit WHERE outcome = 'refused'").fetchone()[0] == 1


def test_url_verification_challenge(app):
    h, raw = _req({"type": "url_verification", "challenge": "abc123"})
    assert app.handle_http(h, raw) == (200, '{"challenge": "abc123"}')


def test_a_question_is_answered_in_thread_with_sources(app):
    h, raw = _req(_event("A load crashed halfway through. How do I recover?"))
    assert app.handle_http(h, raw)[0] == 200
    (m,) = app.poster.sent
    assert m["thread_ts"] == "1.1" and "[docs/runbooks/ingestion.md#4-a-load-crashed-halfway]" in m["text"]


def test_a_retried_event_is_answered_once(app):
    """Slack re-sends an event it did not see acknowledged within three seconds."""
    h, raw = _req(_event("What is our parental leave policy?", event_id="EvRetry"))
    app.handle_http(h, raw)
    h2 = {**h, "X-Slack-Retry-Num": "1", "X-Slack-Retry-Reason": "http_timeout"}
    assert app.handle_http(h2, raw)[0] == 200
    assert len(app.poster.sent) == 1


def test_bot_messages_are_ignored(app):
    h, raw = _req(_event("I could not find this in our documents.", bot_id="B1"))
    app.handle_http(h, raw)
    assert not app.poster.sent


def test_a_proposal_comes_with_buttons_and_a_click_runs_once(app):
    h, raw = _req(_event("fix the stale docs", event_id="EvFix"))
    app.handle_http(h, raw)
    msg = app.poster.sent[-1]
    pid = next(e["value"] for b in msg["blocks"] if b["type"] == "actions" for e in b["elements"]
               if e["action_id"] == "approve" and "deletion_is_exact_match_only" in msg["text"].split(e["value"])[1].split("\n")[0])
    runbook = app.ctx.repo / "docs" / "runbooks" / "ingestion.md"
    before = runbook.read_text()
    click = {"type": "block_actions", "trigger_id": "T1", "user": {"id": "U_DOCS"}, "channel": {"id": "C1"},
             "message": {"ts": "1.1"}, "actions": [{"action_id": "approve", "value": pid, "action_ts": "9.9"}]}
    h, raw = _req(click, form=True)
    app.handle_http(h, raw)
    app.handle_http(h, raw)                                                     # double click: same trigger id
    assert "approved and done" in app.poster.sent[-1]["text"]
    assert sum("approved and done" in m["text"] for m in app.poster.sent) == 1
    assert runbook.read_text() != before


def test_the_asker_cannot_approve_from_slack_either(app):
    h, raw = _req(_event("fix the stale docs", event_id="EvFix2"))
    app.handle_http(h, raw)
    pid = app.poster.sent[-1]["blocks"][1]["elements"][0]["value"]
    click = {"type": "block_actions", "trigger_id": "T2", "user": {"id": "U_ASK"}, "channel": {"id": "C1"},
             "message": {"ts": "1.1"}, "actions": [{"action_id": "approve", "value": pid}]}
    h, raw = _req(click, form=True)
    app.handle_http(h, raw)
    assert app.poster.sent[-1]["text"].startswith("Not done")


def test_poster_retries_throttling_and_surfaces_errors():
    sleeps, calls = [], []

    def opener(req, timeout):
        calls.append(req)
        if len(calls) == 1:
            raise urllib.error.HTTPError("u", 429, "x", {"Retry-After": "2"}, io.BytesIO(b""))
        if len(calls) == 2:
            return io.BytesIO(b'{"ok": true, "ts": "2.2"}')
        return io.BytesIO(b'{"ok": false, "error": "channel_not_found"}')
    p = SlackPoster("xoxb-secret", opener=opener, sleep=sleeps.append)
    assert p.post("C1", "hi")["ok"] and sleeps == [2.0]
    with pytest.raises(RuntimeError, match="channel_not_found"):
        p.post("C1", "hi")
    assert "xoxb-secret" not in repr(p)


def test_usage_report_counts_people_skills_and_unused_skills(app):
    for i, q in enumerate(["What is our parental leave policy?", "A load crashed halfway through. How do I recover?",
                           "are the docs still accurate?"]):
        h, raw = _req(_event(q, event_id=f"EvU{i}"))
        app.handle_http(h, raw)
    r = report(app.ctx.agent_con, app.ctx.skills)
    by = {x["skill"]: x for x in r["by_skill"]}
    assert by["knowledge_qa"]["requests"] == 2 and by["knowledge_qa"]["refused"] == 1
    assert "docs_freshness" in by and "privacy_review" in r["unused_skills"]
