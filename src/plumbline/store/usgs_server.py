"""A local stand-in for the USGS earthquake API, for tests and for trying the kit offline.

Same request shape as the real endpoint (format=geojson, updatedafter, orderby=time-asc, limit, offset;
offset counts from 1) and the same response shape. The events are generated, not real. Their update
times are deliberately not in time order, as on the real feed (a quake is revised hours or days later),
which is what makes a careless cursor lose rows.

`fail` injects trouble: a list of HTTP codes to return, one per request, before answering normally. `stale`
answers that many requests from a copy that is behind: an empty page, marked by an X-Data-Out-Of-Date header.
"""
from __future__ import annotations

import json
import random
import threading
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

T0 = datetime(2026, 11, 1, tzinfo=timezone.utc)


def _ms(t: datetime) -> int:
    return int(t.timestamp() * 1000)


@dataclass
class Feed:
    events: list[dict] = field(default_factory=list)
    fail: list[int] = field(default_factory=list)
    retry_after: str | None = None
    requests: int = 0
    extra: dict = field(default_factory=dict)     # property -> value added to every event (drift)
    stale: int = 0                                # answer this many requests from a copy that is behind: empty, flagged

    @classmethod
    def generated(cls, n: int = 1200, seed: int = 7) -> "Feed":
        rnd = random.Random(seed)
        ev = []
        for i in range(n):
            t = T0 + timedelta(minutes=10 * i + rnd.randint(0, 9))
            upd = t + timedelta(minutes=rnd.choice([5, 30, 240, 2880, 7200]))     # revised later, out of order
            ev.append({"id": f"zz{i:07d}", "time": _ms(t), "updated": _ms(upd), "mag": round(rnd.uniform(0.5, 6.5), 2),
                       "place": f"{rnd.randint(1, 90)} km {rnd.choice(['N', 'S', 'E', 'W'])} of Testville",
                       "status": rnd.choice(["automatic", "reviewed"])})
        return cls(ev)

    def revise(self, ids: list[str], at: datetime, mag: float) -> None:
        for e in self.events:
            if e["id"] in ids:
                e["updated"], e["mag"] = _ms(at), mag

    def query(self, q: dict) -> dict:
        since = q.get("updatedafter")
        cut = _ms(datetime.fromisoformat(since).replace(tzinfo=timezone.utc)) if since else 0
        rows = sorted((e for e in self.events if e["updated"] > cut), key=lambda e: (e["time"], e["id"]))
        off, lim = int(q.get("offset", 1)), int(q.get("limit", 20000))
        page = rows[off - 1: off - 1 + lim]
        feats = [{"type": "Feature", "id": e["id"],
                  "properties": {k: e[k] for k in ("mag", "place", "time", "updated", "status")} | self.extra}
                 for e in page]
        return {"type": "FeatureCollection", "metadata": {"count": len(feats)}, "features": feats}


def serve(feed: Feed) -> tuple[ThreadingHTTPServer, str]:
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            feed.requests += 1
            if feed.fail:
                code = feed.fail.pop(0)
                self.send_response(code)
                if code == 429 and feed.retry_after:
                    self.send_header("Retry-After", feed.retry_after)
                self.end_headers()
                self.wfile.write(b'{"error": "injected"}')
                return
            q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(self.path).query))
            if q.get("format") != "geojson":
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"Error 400: format must be geojson")
                return
            stale = feed.stale > 0
            if stale:
                feed.stale -= 1
            body = json.dumps({"type": "FeatureCollection", "metadata": {"count": 0}, "features": []} if stale
                              else feed.query(q)).encode()
            self.send_response(200)
            if stale:
                self.send_header("X-Data-Out-Of-Date", "true")
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}/fdsnws/event/1/query"
