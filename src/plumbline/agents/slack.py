"""Slack front end: the Events API and button clicks, handled safely.

What goes wrong with a Slack webhook, and what this does about it:
  forged requests      every request carries an HMAC signature made with the app's signing secret;
                       anything that does not verify is refused (401)
  replayed requests    the signed timestamp must be within five minutes, so a captured request
                       cannot be re-sent later
  Slack retries        if Slack does not get a 200 within three seconds it sends the event again;
                       each event id is recorded and a second delivery is acknowledged and dropped,
                       so one question never gets two answers and one click never runs twice
  bot loops            messages from bots, the agent's own replies included, are ignored
  slow answers         Slack wants the 200 fast; with async=True the work runs after the ack
Secrets (SLACK_SIGNING_SECRET, SLACK_BOT_TOKEN) come from the environment and are never logged.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable

import duckdb

from . import audit
from .agent import Context, handle

MAX_AGE_SECONDS = 300
MENTION = re.compile(r"<@[A-Z0-9]+>\s*")


def sign(secret: str, timestamp: str, body: bytes) -> str:
    base = b"v0:" + timestamp.encode() + b":" + body
    return "v0=" + hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()


def verify(secret: str, headers: dict, body: bytes, now: float) -> str | None:
    """None if the request is genuine and fresh, else the reason it is not."""
    h = {k.lower(): v for k, v in headers.items()}
    ts, sig = h.get("x-slack-request-timestamp"), h.get("x-slack-signature")
    if not ts or not sig:
        return "missing signature"
    try:
        age = abs(now - int(ts))
    except ValueError:
        return "bad timestamp"
    if age > MAX_AGE_SECONDS:
        return "request too old (possible replay)"
    if not hmac.compare_digest(sign(secret, ts, body), sig):
        return "bad signature"
    return None


class SlackPoster:
    """chat.postMessage with a bot token; honours 429 Retry-After; raises on an error reply."""

    def __init__(self, token: str, opener: Callable | None = None, sleep: Callable[[float], None] = time.sleep, retries: int = 3):
        if not token:
            raise ValueError("no Slack bot token")
        self._token, self._open, self.sleep, self.retries = token, opener or urllib.request.urlopen, sleep, retries

    def __repr__(self) -> str:
        return "SlackPoster(token=***)"

    def post(self, channel: str, text: str, thread_ts: str | None = None, blocks: list | None = None) -> dict:
        payload = {"channel": channel, "text": text}
        if thread_ts:
            payload["thread_ts"] = thread_ts
        if blocks:
            payload["blocks"] = blocks
        data = json.dumps(payload).encode()
        for attempt in range(self.retries + 1):
            req = urllib.request.Request("https://slack.com/api/chat.postMessage", data=data, method="POST",
                                         headers={"Authorization": f"Bearer {self._token}",
                                                  "Content-Type": "application/json; charset=utf-8"})
            try:
                with self._open(req, timeout=10) as r:
                    out = json.loads(r.read())
                if not out.get("ok"):
                    raise RuntimeError(f"Slack refused the message: {out.get('error')}")
                return out
            except urllib.error.HTTPError as e:
                if e.code != 429 or attempt == self.retries:
                    raise RuntimeError(f"Slack HTTP {e.code}") from None
                self.sleep(float(e.headers.get("Retry-After") or 1))
        raise AssertionError("unreachable")


def approval_blocks(text: str, proposals: list[str]) -> list:
    blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": text}}]
    for pid in proposals:
        blocks.append({"type": "actions", "block_id": f"decide_{pid}", "elements": [
            {"type": "button", "action_id": "approve", "value": pid, "style": "primary", "text": {"type": "plain_text", "text": f"Approve {pid}"}},
            {"type": "button", "action_id": "reject", "value": pid, "style": "danger", "text": {"type": "plain_text", "text": f"Reject {pid}"}}]})
    return blocks


class SlackApp:
    def __init__(self, ctx: Context, signing_secret: str, poster, now: Callable[[], float] = time.time,
                 run_async: bool = False):
        if not signing_secret:
            raise ValueError("no Slack signing secret")
        self.ctx, self._secret, self.poster, self.now, self.run_async = ctx, signing_secret, poster, now, run_async
        self._lock = threading.Lock()

    def _first_time(self, event_id: str) -> bool:
        with self._lock:
            self.ctx.open()
            try:
                self.ctx.agent_con.execute("INSERT INTO ops.slack_events VALUES (?, now())", [event_id])
                return True
            except duckdb.ConstraintException:
                return False

    def handle_http(self, headers: dict, body: bytes) -> tuple[int, str]:
        why = verify(self._secret, headers, body, self.now())
        if why:
            self.ctx.open()
            audit.log(self.ctx.agent_con, actor="unknown", channel="slack", skill=None, action="request", outcome="refused",
                      detail={"why": why})
            return 401, json.dumps({"error": why})
        ctype = {k.lower(): v for k, v in headers.items()}.get("content-type", "")
        if ctype.startswith("application/x-www-form-urlencoded"):
            payload = json.loads(urllib.parse.parse_qs(body.decode())["payload"][0])
            return self._interaction(payload)
        d = json.loads(body)
        if d.get("type") == "url_verification":
            return 200, json.dumps({"challenge": d["challenge"]})
        if d.get("type") != "event_callback":
            return 200, "{}"
        ev = d.get("event", {})
        if ev.get("bot_id") or ev.get("subtype") == "bot_message":
            return 200, "{}"
        if ev.get("type") not in ("app_mention", "message"):
            return 200, "{}"
        if not self._first_time(d["event_id"]):
            return 200, "{}"                          # Slack retried; already handled
        work = lambda: self._answer(ev)
        if self.run_async:
            threading.Thread(target=work, daemon=True).start()
        else:
            work()
        return 200, "{}"

    def _answer(self, ev: dict) -> None:
        text = MENTION.sub("", ev.get("text", "")).strip()
        with self._lock:
            r = handle(self.ctx, ev["user"], text, channel="slack")
        thread = ev.get("thread_ts") or ev.get("ts")
        self.poster.post(ev["channel"], r.text, thread_ts=thread,
                         blocks=approval_blocks(r.text, r.proposals) if r.proposals and r.tool else None)

    def _interaction(self, p: dict) -> tuple[int, str]:
        if p.get("type") != "block_actions":
            return 200, "{}"
        act = p["actions"][0]
        if act.get("action_id") not in ("approve", "reject"):
            return 200, "{}"
        click_id = f"click:{p.get('trigger_id') or act.get('action_ts')}"
        if not self._first_time(click_id):
            return 200, "{}"
        with self._lock:
            r = handle(self.ctx, p["user"]["id"], f"{act['action_id']} {act['value']}", channel="slack")
        ch = (p.get("channel") or {}).get("id")
        thread = (p.get("message") or {}).get("ts")
        if ch:
            self.poster.post(ch, r.text, thread_ts=thread)
        return 200, "{}"


def serve(app: SlackApp, port: int = 3000) -> None:
    """Minimal HTTP endpoint for Slack's Request URL. Put it behind HTTPS (a tunnel or a proxy)."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):                 # request lines can carry tokens in query strings
            pass

        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            code, out = app.handle_http(dict(self.headers), body)
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(out.encode())

    ThreadingHTTPServer(("0.0.0.0", port), H).serve_forever()
