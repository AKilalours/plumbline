"""A local stand-in for a Socrata dataset (NYC Open Data), with the failures a public API has.

Used by the tests and by the app's "Open data" view, the way store/server.py stands in for the store.

It answers the two query forms the connector sends (`:updated_at >= 'T'` and the keyset
`:updated_at > 'T' OR (:updated_at = 'T' AND :id > 'X')`), ordered by (:updated_at, :id), and can:
throttle (429 with Retry-After), fail (503), drop the connection, send half a body, correct rows and
add a column while a sync is paging through it. Like the real API, a JSON row leaves out empty fields.
"""
from __future__ import annotations

import json
import random
import re
import threading
import urllib.parse
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

T0 = datetime(2026, 10, 1, 8, 0, 0)


def ts(t: datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%S.000")


def make_rows(n: int, seed: int = 0) -> list[dict]:
    rnd = random.Random(seed)
    rows = []
    for i in range(n):
        r = {":id": f"row-{i:06d}", ":updated_at": ts(T0 + timedelta(seconds=rnd.randint(0, 86_400 * 20))),
             "job__": str(100000 + i), "bin__": str(3000000 + rnd.randint(0, 99999)),
             "house__": str(rnd.randint(1, 999)), "street_name": rnd.choice(["KENT AVENUE", "BEDFORD AVENUE", "N 7 STREET"]),
             "zip_code": rnd.choice(["11211", "11249", "11206"]), "permit_type": rnd.choice(["AL", "NB", "DM", "EW"]),
             "issuance_date": (T0 + timedelta(days=rnd.randint(0, 20))).strftime("%m/%d/%Y"),
             "owner_s_first_name": rnd.choice(["Ana", "Ben", "Chen"]), "owner_s_phone__": f"555{rnd.randint(1000000, 9999999)}",
             "permittee_s_business_name": "ACME BUILDERS", "location": {"latitude": "40.7", "longitude": "-73.9"}}
        if rnd.random() < 0.3:
            del r["permit_type"]                            # empty fields are left out of the JSON row
        rows.append(r)
    # some rows share a timestamp: the keyset has to break ties by :id
    for i in range(0, n, 37):
        rows[i][":updated_at"] = rows[0][":updated_at"]
    return rows


class BadQuery(ValueError):
    """A query the real API refuses with HTTP 400."""


class Dataset:
    def __init__(self, rows: list[dict], *, p429=0.0, p503=0.0, p_drop=0.0, p_half=0.0, seed=0,
                 change_after_requests: int | None = None):
        self.rows = {r[":id"]: dict(r) for r in rows}
        self.p429, self.p503, self.p_drop, self.p_half = p429, p503, p_drop, p_half
        self.rnd = random.Random(seed)
        self.lock = threading.Lock()
        self.requests = 0
        self.change_after = change_after_requests
        self.changed: list[str] = []

    def now_max(self) -> datetime:
        return max(datetime.fromisoformat(r[":updated_at"]) for r in self.rows.values())

    def correct(self, ids: list[str], new_column: bool = False) -> None:
        """The city corrects rows (they move to the end of the order) and may add a column."""
        t = self.now_max() + timedelta(seconds=1)
        for i in ids:
            r = self.rows[i]
            r[":updated_at"] = ts(t)
            r["permit_type"] = "AL"
            if new_column:
                r["filing_status"] = "RENEWAL"
            self.changed.append(i)

    def query(self, q: dict) -> list[dict]:
        where = q.get("$where", "")
        # As strict as NYC's API where it was found to refuse (checked on data.cityofnewyork.us, 2026-11-06):
        # a star selection that is not first, and a timestamp or row id that does not parse. This server
        # once accepted ":id,:updated_at,*", so the connector's first real run failed with HTTP 400.
        sel = [c.strip() for c in q.get("$select", "*").split(",")]
        if "*" in sel and sel[0] != "*":
            raise BadQuery("Star selections must come at the start of the select-list")
        for lit in re.findall(r":updated_at [<>=]+ '([^']*)'", where):
            try:
                datetime.fromisoformat(lit.rstrip("Z"))
            except ValueError:
                raise BadQuery(f"Type mismatch: '{lit}' is not a fixed timestamp") from None
        for lit in re.findall(r":id [<>=]+ '([^']*)'", where):
            if lit not in self.rows and not re.fullmatch(r"row-[\w.~-]+", lit):
                raise BadQuery("Type mismatch for op$>, is text")
        at = lambda v: datetime.fromisoformat(v.rstrip("Z"))      # compared as times, as the API does, not as text
        rows = sorted(self.rows.values(), key=lambda r: (at(r[":updated_at"]), r[":id"]))
        m = re.fullmatch(r":updated_at > '([^']+)' OR \(:updated_at = '([^']+)' AND :id > '([^']+)'\)", where)
        if m:
            t, _, rid = m.groups()
            t = at(t)
            rows = [r for r in rows if at(r[":updated_at"]) > t or (at(r[":updated_at"]) == t and r[":id"] > rid)]
        elif (m := re.fullmatch(r":updated_at >= '([^']+)'", where)):
            rows = [r for r in rows if at(r[":updated_at"]) >= at(m.group(1))]
        elif where:
            raise ValueError(f"unexpected $where {where!r}")
        out = []
        for r in rows[: int(q.get("$limit", 1000))]:
            out.append({k: v for k, v in r.items() if v not in (None, "")})
        return out


def serve(ds: Dataset) -> tuple[ThreadingHTTPServer, str]:
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            u = urllib.parse.urlparse(self.path)
            q = dict(urllib.parse.parse_qsl(u.query))
            with ds.lock:
                ds.requests += 1
                n = ds.requests
                if ds.change_after and n == ds.change_after:
                    ids = sorted(ds.rows)[:5]
                    ds.correct(ids, new_column=True)
                roll = ds.rnd.random()
            if roll < ds.p429:
                self.send_response(429)
                self.send_header("Retry-After", "0")
                self.end_headers()
                return
            if roll < ds.p429 + ds.p503:
                self.send_response(503)
                self.end_headers()
                return
            if roll < ds.p429 + ds.p503 + ds.p_drop:
                self.close_connection = True
                return
            try:
                with ds.lock:
                    body = json.dumps(ds.query(q)).encode()
            except BadQuery as e:
                err = json.dumps({"code": "query.soql.type-mismatch", "error": True, "message": str(e)}).encode()
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(err)))
                self.end_headers()
                self.wfile.write(err)
                return
            half = ds.rnd.random() < ds.p_half
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body[: len(body) // 2] if half else body)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"
