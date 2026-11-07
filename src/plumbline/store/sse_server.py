"""A local stand-in for a live event stream (shaped like Wikimedia's recent changes), with a stream's failures.

Used by the tests. It serves a fixed log of events as Server-Sent Events, resumes from Last-Event-ID the way
the real stream does, and can, on purpose:
- fail the first connections (503, or 429 with Retry-After),
- drop the connection every N events, sometimes in the middle of an event,
- replay a few events the client already has after a reconnect (duplicates),
- send heartbeats (comment lines), a `retry:` hint, CRLF line endings, one event split over two data lines,
  bytes cut at awkward places (inside a line, between CR and LF, inside a UTF-8 character),
- mix in the stream's own health-check events (domain "canary") and one event that is not JSON.
Event types carry different fields, as on the real stream: `length` and `revision` only on edits and new pages,
`log_*` only on log events.
"""
from __future__ import annotations

import json
import random
import threading
import time
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

T0 = datetime(2026, 11, 6, 12, 0, 0)


def make_events(n: int, seed: int = 0, canary_every: int = 97, bad_json_at: int | None = 41) -> list[dict]:
    rnd = random.Random(seed)
    out = []
    for i in range(n):
        t = rnd.choices(["edit", "categorize", "new", "log"], [47, 45, 4, 4])[0]
        dom = "canary" if canary_every and i % canary_every == canary_every - 1 else rnd.choice(
            ["commons.wikimedia.org", "www.wikidata.org", "en.wikipedia.org", "fr.wikipedia.org"])
        d = {"$schema": "/mediawiki/recentchange/1.0.0",
             "meta": {"id": f"evt-{i:06d}", "dt": (T0 + timedelta(milliseconds=40 * i)).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
                      "domain": dom, "topic": "codfw.mediawiki.recentchange", "partition": 0, "offset": 9_000_000 + i,
                      "request_id": "x", "uri": "https://example.test/wiki/x"},
             "type": t, "namespace": rnd.choice([0, 6, 14]), "title": f"Page {i} é",   # a non-ASCII title
             "wiki": dom.split(".")[0] + "wiki", "bot": rnd.random() < 0.4, "user": f"Editor{i % 13}",
             "comment": "free text", "parsedcomment": "<b>free text</b>", "timestamp": 1794000000 + i,
             "server_name": dom}
        if rnd.random() > 0.01:
            d["id"] = 3_500_000_000 + i                     # a few real events have no rc id
        if t in ("edit", "new"):
            d.update(minor=rnd.random() < 0.2, length={"old": rnd.randint(0, 9000), "new": rnd.randint(0, 9000)},
                     revision={"old": 100 + i, "new": 101 + i})
        if t == "log":
            d.update(log_type="upload", log_action="overwrite", log_id=i, log_params={"x": 1}, log_action_comment="x")
        out.append(d)
    return out


class Stream:
    def __init__(self, events: list[dict], *, fail_first: int = 0, fail_code: int = 503, drop_every: int | None = None,
                 cut_mid_event: bool = True, replay_on_resume: int = 0, crlf: bool = False, chunk: int | None = 509,
                 heartbeat_every: int = 25, retry_ms: int = 10, multiline_at: int | None = 3, bad_json_at: int | None = 41,
                 tail_seconds: float = 60.0, delay: float = 0.0):
        self.events, self.fail_first, self.fail_code = events, fail_first, fail_code
        self.drop_every, self.cut_mid, self.replay = drop_every, cut_mid_event, replay_on_resume
        self.crlf, self.chunk, self.hb, self.retry_ms = crlf, chunk, heartbeat_every, retry_ms
        self.multiline_at, self.bad_json_at, self.tail, self.delay = multiline_at, bad_json_at, tail_seconds, delay
        self.lock = threading.Lock()
        self.connects = 0
        self.resumed_from: list[str | None] = []

    def frame(self, i: int) -> str:
        nl = "\r\n" if self.crlf else "\n"
        eid = json.dumps([{"topic": "codfw.mediawiki.recentchange", "partition": 0, "offset": 9_000_000 + i}])
        if i == self.bad_json_at:
            return f"event: message{nl}id: {eid}{nl}data: {{not json{nl}{nl}"
        data = json.dumps(self.events[i], ensure_ascii=False)
        if i == self.multiline_at:                          # one event's data split over two lines
            cut = data.index('"type"')
            body = f"data: {data[:cut]}{nl}data: {data[cut:]}{nl}"
        else:
            body = f"data: {data}{nl}"
        return f"event: message{nl}id: {eid}{nl}{body}{nl}"

    def start_index(self, last_id: str | None) -> int:
        if not last_id:
            return 0
        try:
            off = json.loads(last_id)[0]["offset"]
        except (ValueError, KeyError, IndexError, TypeError):
            return 0
        return max(0, off - 9_000_000 + 1 - self.replay)


def serve(stream: Stream, port: int = 0):
    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def do_GET(self):
            with stream.lock:
                stream.connects += 1
                n = stream.connects
            last = self.headers.get("Last-Event-ID")
            stream.resumed_from.append(last)
            if n <= stream.fail_first:
                body = b'{"error":"busy"}'
                self.send_response(stream.fail_code)
                if stream.fail_code == 429:
                    self.send_header("Retry-After", "0")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Connection", "close")
            self.end_headers()
            nl = "\r\n" if stream.crlf else "\n"
            out = ("﻿" if n == stream.fail_first + 1 else "") + f":ok{nl}{nl}retry: {stream.retry_ms}{nl}{nl}"
            i0 = stream.start_index(last)
            sent = 0
            try:
                for i in range(i0, len(stream.events)):
                    if stream.drop_every and sent and sent % stream.drop_every == 0:
                        f = stream.frame(i)
                        if stream.cut_mid:                  # half an event, then the line goes dead
                            out += f[: len(f) // 2]
                        self._send(out)
                        return
                    out += stream.frame(i)
                    sent += 1
                    if stream.hb and sent % stream.hb == 0:
                        out += f": heartbeat{nl}"
                    if len(out) > 4000:
                        self._send(out)
                        out = ""
                        if stream.delay:
                            time.sleep(stream.delay)
                self._send(out)
                t = time.time()
                while time.time() - t < stream.tail:        # nothing new: heartbeats, as an idle live stream sends
                    self._send(f": heartbeat{nl}")
                    time.sleep(0.05)
            except (BrokenPipeError, ConnectionResetError):
                return

        def _send(self, text: str):
            b = text.encode("utf-8")
            step = stream.chunk or len(b) or 1
            for k in range(0, len(b), step):                 # small writes: lines and characters arrive in pieces
                self.wfile.write(b[k:k + step])
            self.wfile.flush()

    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}/v2/stream/recentchange"
