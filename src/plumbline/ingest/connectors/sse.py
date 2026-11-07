"""A streaming source: Server-Sent Events, read in micro-batches through the same contract loader.

A stream is not a file or a page of an API. It never ends, it drops the connection when it likes, and after
a reconnect the server may send some events again. What keeps the landed data right:

- The parser follows the HTML standard for event streams: data split over several lines, comments used as
  heartbeats, CR, LF or CRLF line endings, a byte-order mark, a `retry:` hint, and a half-sent event at the
  moment the connection drops (thrown away, never landed).
- Resume: every event carries an id. On a reconnect the client sends the last one back as Last-Event-ID and
  the server continues from there. 429 and 5xx back off with jitter; any other 4xx stops with the reason.
- Batches, then the checkpoint: events are written as a CSV batch (atomically), and only then is the
  checkpoint moved to the last event in that batch. A crash between the two replays that batch on the next
  run; the event's own id is the contract key, so the loader keeps one copy (effectively once).
- Duplicates are dropped as they arrive: the ids of recent events are kept with the checkpoint.
- A fixed column set per landing folder, as for every other connector: the first batch records it; fields
  that appear later are reported as drift and not written.
- Lag: for every event, the seconds between the event happening (its own timestamp) and it landing here.

    python -m plumbline.ingest.stream_daily        # what the daily job runs against Wikimedia's live stream
"""
from __future__ import annotations

import csv
import http.client
import io
import json
import random
import time
import urllib.error
import urllib.request
from collections import Counter, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator

from .store_api import _atomic_write

USER_AGENT = "plumbline-stream/0.1 (portfolio project; github.com/AKilalours/plumbline)"


@dataclass
class Event:
    data: str
    id: str | None = None          # the last event id in force when this event was dispatched
    event: str = "message"


class Parser:
    """Feed it bytes as they arrive; it returns the events completed so far. Follows the WHATWG rules."""

    def __init__(self):
        self.buf, self.started = "", False
        self.data, self.etype, self.last_id, self.retry = [], "", None, None
        self._dec = None

    def feed(self, chunk: bytes) -> list[Event]:
        import codecs
        if self._dec is None:
            self._dec = codecs.getincrementaldecoder("utf-8")("replace")
        text = self._dec.decode(chunk)
        if not self.started and text:
            text, self.started = text.lstrip("﻿") if text.startswith("﻿") else text, True
        self.buf += text
        out = []
        while True:
            i = min((k for k in (self.buf.find("\r"), self.buf.find("\n")) if k >= 0), default=-1)
            if i < 0:
                break
            if self.buf[i] == "\r" and i == len(self.buf) - 1:
                break                                  # a CR at the end may be half of a CRLF: wait for more
            line = self.buf[:i]
            self.buf = self.buf[i + (2 if self.buf[i:i + 2] == "\r\n" else 1):]
            ev = self._line(line)
            if ev:
                out.append(ev)
        return out

    def _line(self, line: str) -> Event | None:
        if line == "":                                 # blank line: dispatch
            if not self.data:
                self.etype = ""
                return None
            ev = Event("\n".join(self.data), self.last_id, self.etype or "message")
            self.data, self.etype = [], ""
            return ev
        if line.startswith(":"):                       # a comment, usually a heartbeat
            return None
        name, _, value = line.partition(":")
        value = value[1:] if value.startswith(" ") else value
        if name == "data":
            self.data.append(value)
        elif name == "event":
            self.etype = value
        elif name == "id":
            if "\0" not in value:
                self.last_id = value
        elif name == "retry" and value.isdigit():
            self.retry = int(value)
        return None

    def drop_partial(self) -> None:
        """The connection ended: an event without its blank line was never complete."""
        self.buf, self.data, self.etype = "", [], ""


class StreamError(RuntimeError):
    pass


@dataclass
class StreamStats:
    connects: int = 0
    reconnects: int = 0
    by_cause: Counter = field(default_factory=Counter)


class SSEClient:
    """Yields events forever, reconnecting with Last-Event-ID. The caller decides when to stop."""

    RETRYABLE = {429, 500, 502, 503, 504}

    def __init__(self, url: str, *, last_event_id: str | None = None, headers: dict | None = None,
                 read_timeout: float = 30.0, max_reconnects: int = 50, base_delay: float = 0.5, max_delay: float = 30.0,
                 sleep: Callable[[float], None] = time.sleep, rng: random.Random | None = None):
        self.url, self.last_id, self.extra = url, last_event_id, headers or {}
        self.read_timeout, self.max_reconnects = read_timeout, max_reconnects
        self.base, self.cap, self.sleep, self.rng = base_delay, max_delay, sleep, rng or random.Random()
        self.stats = StreamStats()
        self.server_retry_ms: int | None = None
        self._resp = None
        self.closed = False

    def _open(self):
        h = {"Accept": "text/event-stream", "Cache-Control": "no-cache", "User-Agent": USER_AGENT, **self.extra}
        if self.last_id:
            h["Last-Event-ID"] = self.last_id
        return urllib.request.urlopen(urllib.request.Request(self.url, headers=h), timeout=self.read_timeout)

    def _wait(self, attempt: int, retry_after: float | None = None) -> None:
        if retry_after is not None:
            d = retry_after
        else:
            base = self.server_retry_ms / 1000 if self.server_retry_ms else self.base
            d = min(self.cap, base * 2 ** min(attempt, 8)) * (0.5 + self.rng.random() / 2)
        self.sleep(d)

    def close(self) -> None:
        """Hang up now (a planned disconnect); the next read reconnects from the last id."""
        if self._resp is not None:
            try:
                self._resp.close()
            except Exception:
                pass
            self._resp = None

    def events(self) -> Iterator[Event | None]:
        """Events as they complete. None after a read that completed none (a heartbeat, part of an event), so
        the caller's clock keeps running on a quiet stream instead of only when an event arrives."""
        failures = 0
        while not self.closed:
            parser = Parser()
            try:
                self._resp = resp = self._open()
                self.stats.connects += 1
                if resp.status == 204:                       # the standard's "stop reconnecting"
                    return
                ctype = resp.headers.get("Content-Type", "")
                if not ctype.startswith("text/event-stream"):
                    raise StreamError(f"not an event stream: Content-Type {ctype!r}")
                while True:
                    chunk = resp.read1(65536) if hasattr(resp, "read1") else resp.read(4096)
                    if not chunk:
                        raise ConnectionResetError("server closed the stream")
                    got = parser.feed(chunk)
                    for ev in got:
                        failures = 0
                        if ev.id is not None:
                            self.last_id = ev.id
                        yield ev
                    if not got:
                        yield None
                    if parser.retry is not None:
                        self.server_retry_ms = parser.retry
            except urllib.error.HTTPError as e:
                if e.code not in self.RETRYABLE:
                    raise StreamError(f"HTTP {e.code}: {e.read()[:200].decode('utf-8', 'replace')}") from None
                cause, ra = f"http_{e.code}", e.headers.get("Retry-After")
                retry_after = float(ra) if ra and ra.replace(".", "", 1).isdigit() else None
            except StreamError:
                raise
            except (OSError, http.client.HTTPException, ValueError, AttributeError) as e:
                # AttributeError/ValueError: reading a response that close() just shut from this side
                cause, retry_after = ("planned" if self._resp is None else type(e).__name__), None
            finally:
                parser.drop_partial()
            if self.closed:
                return
            failures += 1
            self.stats.reconnects += 1
            self.stats.by_cause[cause] += 1
            if failures > self.max_reconnects:
                raise StreamError(f"gave up after {failures - 1} reconnects in a row (last: {cause})")
            if cause != "planned":
                self._wait(failures - 1, retry_after)


@dataclass
class SyncResult:
    events: int = 0           # events read off the wire
    written: int = 0          # rows landed in batches
    duplicates: int = 0
    filtered: int = 0         # e.g. the stream's own health-check events
    bad_json: int = 0
    batches: int = 0
    by_type: Counter = field(default_factory=Counter)
    lag: list = field(default_factory=list)
    resume_check: dict | None = None
    per_second: dict = field(default_factory=dict)   # second of the window -> Counter of event types landed
    disconnect_second: int | None = None
    reconnect_seconds: list = field(default_factory=list)

    TYPES = ("edit", "categorize", "new", "log")

    def timeline(self) -> list[list[int]]:
        """[second, edit, categorize, new, log, other] for every second of the window, gaps included."""
        if not self.per_second:
            return []
        out = []
        for sec in range(max(self.per_second) + 1):
            c = self.per_second.get(sec, Counter())
            out.append([sec, *(c[t] for t in self.TYPES), sum(v for k, v in c.items() if k not in self.TYPES)])
        return out


def _pct(xs: list[float], q: float) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    return round(s[min(len(s) - 1, int(q * len(s)))], 2)


def _csv(rows: list[dict], columns: list[str]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore", lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue()


def stream_sync(client: SSEClient, landing_dir: str | Path, *, name: str, transform: Callable[[dict], dict | None],
                key: str, seconds: float, max_events: int | None = None, batch_events: int = 500,
                batch_seconds: float = 5.0, planned_disconnect_at: float | None = None, keep_ids: int = 20000,
                clock: Callable[[], float] = time.monotonic, wall: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
                after_batch: Callable[[int], None] | None = None) -> SyncResult:
    """Read `client` for `seconds` (or `max_events`), landing CSV batches in landing_dir.

    transform(event_json) returns the row to land, or None to skip it (counted as filtered). The row's
    `key` column is the dedupe key. planned_disconnect_at hangs up once that many seconds in, then checks the
    resume: how many events after the reconnect were already seen."""
    landing = Path(landing_dir)
    landing.mkdir(parents=True, exist_ok=True)
    state_path = landing / "_stream_state.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {
        "name": name, "last_event_id": None, "next_batch": 1, "columns": None, "drift": [], "recent": []}
    if state["last_event_id"] and client.last_id is None:
        client.last_id = state["last_event_id"]
    recent = deque(state["recent"], maxlen=keep_ids)
    seen = set(recent)
    res = SyncResult()
    buf: list[dict] = []
    buf_last_id = None
    t0 = clock()
    last_flush = t0
    planned = planned_disconnect_at is not None
    after_reconnect = None                         # events seen since the planned reconnect, while checking

    def flush():
        nonlocal buf, last_flush
        last_flush = clock()
        if not buf:
            return
        if state["columns"] is None:
            state["columns"] = list(buf[0].keys())
        new = sorted({k for r in buf for k in r} - set(state["columns"]) - set(state["drift"]))
        state["drift"] += new
        _atomic_write(landing / f"{name}_b{state['next_batch']:06d}.csv", _csv(buf, state["columns"]))
        if after_batch:
            after_batch(state["next_batch"])       # tests crash here: the batch is landed, the checkpoint is not
        state["next_batch"] += 1
        state["last_event_id"] = buf_last_id
        state["recent"] = list(recent)
        _atomic_write(state_path, json.dumps(state, indent=1))
        res.batches += 1
        res.written += len(buf)
        buf = []

    try:
        for ev in client.events():
            now = clock()
            if ev is None:                         # a quiet moment: still flush on time and stop on time
                if now - last_flush >= batch_seconds:
                    flush()
                if now - t0 >= seconds:
                    break
                continue
            res.events += 1
            try:
                doc = json.loads(ev.data)
            except ValueError:
                res.bad_json += 1
                doc = None
            row = transform(doc) if isinstance(doc, dict) else None
            if row is None:
                res.filtered += doc is not None
            elif row[key] in seen:
                res.duplicates += 1
                if after_reconnect is not None:
                    after_reconnect["repeated"] += 1
            else:
                seen.add(row[key])
                if len(recent) == recent.maxlen:
                    seen.discard(recent[0])
                recent.append(row[key])
                buf.append(row)
                res.by_type[row.get("type") or ev.event] += 1
                res.per_second.setdefault(int(now - t0), Counter())[row.get("type") or ev.event] += 1
                at = row.get("event_time")
                if at:
                    try:
                        res.lag.append((wall() - datetime.fromisoformat(at).replace(tzinfo=timezone.utc)).total_seconds())
                    except ValueError:
                        pass
                if after_reconnect is not None:
                    after_reconnect["new"] += 1
            buf_last_id = ev.id or buf_last_id
            if after_reconnect is not None and after_reconnect["new"] + after_reconnect["repeated"] >= 200:
                res.resume_check = {**after_reconnect, "last_id_sent": after_reconnect["last_id_sent"]}
                after_reconnect = None
            if len(buf) >= batch_events or now - last_flush >= batch_seconds:
                flush()
            if planned and now - t0 >= planned_disconnect_at:
                planned = False
                res.disconnect_second = int(now - t0)
                after_reconnect = {"new": 0, "repeated": 0, "last_id_sent": client.last_id}
                client.close()
            if now - t0 >= seconds or (max_events and res.events >= max_events):
                break
    finally:
        flush()
        client.closed = True
        client.close()
    if after_reconnect is not None:
        res.resume_check = after_reconnect
    return res


# ------------------------------------------------------------------ Wikimedia's recent changes stream
WIKIMEDIA_URL = "https://stream.wikimedia.org/v2/stream/recentchange"
# Kept: what changed, where, when, and where in the stream it came from. Dropped before landing: who made the
# change (user names, and IP addresses for logged-out editors) and free-text comments, which can hold anything.
DROPPED = ["user", "comment", "parsedcomment", "log_params", "log_action_comment", "meta.request_id", "meta.uri",
           "notify_url", "title_url", "server_url", "server_script_path"]


def _yn(v) -> str:
    return "" if v is None else ("Y" if v else "N")


def recentchange_row(d: dict) -> dict | None:
    meta = d.get("meta") or {}
    if meta.get("domain") == "canary":            # the stream's own health-check events, not edits
        return None
    length, rev = d.get("length") or {}, d.get("revision") or {}
    dt = meta.get("dt") or ""
    return {
        "event_id": meta.get("id") or "",
        "event_time": dt[:-1] if dt.endswith("Z") else dt,
        "type": d.get("type") or "",
        "wiki": d.get("wiki") or "",
        "domain": meta.get("domain") or "",
        "namespace": "" if d.get("namespace") is None else d["namespace"],
        "title": d.get("title") or "",
        "rc_id": "" if d.get("id") is None else d["id"],
        "bot": _yn(d.get("bot")),
        "minor": _yn(d.get("minor")),
        "patrolled": _yn(d.get("patrolled")),
        "length_old": "" if length.get("old") is None else length["old"],
        "length_new": "" if length.get("new") is None else length["new"],
        "revision_old": "" if rev.get("old") is None else rev["old"],
        "revision_new": "" if rev.get("new") is None else rev["new"],
        "log_type": d.get("log_type") or "",
        "log_action": d.get("log_action") or "",
        "stream_topic": meta.get("topic") or "",
        "stream_partition": "" if meta.get("partition") is None else meta["partition"],
        "stream_offset": "" if meta.get("offset") is None else meta["offset"],
    }
