"""A mock store API that misbehaves on purpose, in the ways real commerce APIs do.

    GET /orders.json?updated_at_min=<ISO>&limit=<1..250>&page_info=<cursor>
    header X-Store-Token: <token>

Pagination is keyset on (updated_at, order_id), like Shopify's cursor pagination, so a page boundary
never skips or repeats an unchanged order. Faults, each with its own probability:
- 429 with a Retry-After header (simulated throttling)
- 503 (server hiccup)
- a dropped connection: headers and half a body, then the socket closes
- orders changing between pages (a refund moves an order to a later updated_at)
"""
from __future__ import annotations

import base64
import json
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import numpy as np
import pandas as pd


@dataclass
class Faults:
    p_429: float = 0.0
    p_503: float = 0.0
    p_drop: float = 0.0
    retry_after: int = 1
    refund_every: int = 0      # every n-th request, refund one paid order (0 = never)
    seed: int = 0


def _cursor(updated_at: str, order_id: str, since: str) -> str:
    return base64.urlsafe_b64encode(json.dumps([updated_at, order_id, since]).encode()).decode()


def _uncursor(c: str) -> tuple[str, str, str]:
    a, b, s = json.loads(base64.urlsafe_b64decode(c.encode()))
    return a, b, s


class StoreState:
    def __init__(self, orders: pd.DataFrame, token: str, faults: Faults):
        self.lock = threading.Lock()
        self.orders = {r["order_id"]: dict(r) for r in orders.to_dict("records")}
        self.token = token
        self.faults = faults
        self.rng = np.random.default_rng(faults.seed)
        self.requests = 0
        self.served_ok = 0
        self.mutations: list[str] = []

    def _maybe_mutate(self) -> None:
        f = self.faults
        if f.refund_every and self.requests % f.refund_every == 0:
            paid = sorted(k for k, v in self.orders.items() if v["financial_status"] == "paid")
            if paid:
                k = paid[int(self.rng.integers(0, len(paid)))]
                latest = max(v["updated_at"] for v in self.orders.values())
                self.orders[k] = {**self.orders[k], "financial_status": "refunded",
                                  "updated_at": (pd.Timestamp(latest) + pd.Timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%S")}
                self.mutations.append(k)

    def page(self, since: str, limit: int, cursor: str | None) -> tuple[list[dict], str | None]:
        if cursor:
            after_u, after_id, since = _uncursor(cursor)
        else:
            after_u, after_id = "", ""
        rows = sorted((v for v in self.orders.values() if v["updated_at"] >= since),
                      key=lambda v: (v["updated_at"], v["order_id"]))
        rows = [v for v in rows if (v["updated_at"], v["order_id"]) > (after_u, after_id)]
        page = rows[:limit]
        nxt = _cursor(page[-1]["updated_at"], page[-1]["order_id"], since) if len(rows) > limit else None
        return page, nxt


def _handler(state: StoreState):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):  # keep test output quiet; never log headers (they carry the token)
            pass

        def _send(self, code: int, body: bytes, headers: dict | None = None, truncate: bool = False):
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body[: len(body) // 2] if truncate else body)
            if truncate:
                self.close_connection = True

        def do_GET(self):
            u = urlparse(self.path)
            if u.path != "/orders.json":
                return self._send(404, b'{"errors":"not found"}')
            if self.headers.get("X-Store-Token") != state.token:
                return self._send(401, b'{"errors":"invalid token"}')
            with state.lock:
                state.requests += 1
                state._maybe_mutate()
                f, r = state.faults, state.rng.random()
                if r < f.p_429:
                    return self._send(429, b'{"errors":"rate limited"}', {"Retry-After": str(f.retry_after)})
                if r < f.p_429 + f.p_503:
                    return self._send(503, b'{"errors":"unavailable"}')
                drop = r < f.p_429 + f.p_503 + f.p_drop
                q = {k: v[0] for k, v in parse_qs(u.query).items()}
                try:
                    limit = max(1, min(250, int(q.get("limit", "50"))))
                except ValueError:
                    return self._send(400, b'{"errors":"bad limit"}')
                page, nxt = state.page(q.get("updated_at_min", ""), limit, q.get("page_info"))
                if not drop:
                    state.served_ok += 1
            headers = {}
            if nxt:
                headers["Link"] = f'</orders.json?{urlencode({"limit": limit, "page_info": nxt})}>; rel="next"'
            self._send(200, json.dumps({"orders": page}).encode(), headers, truncate=drop)
    return H


class MockStore:
    """Run the API on a background thread: `with MockStore(...) as s: s.url`."""

    def __init__(self, orders: pd.DataFrame, token: str, faults: Faults | None = None, port: int = 0):
        self.state = StoreState(orders, token, faults or Faults())
        self.httpd = ThreadingHTTPServer(("127.0.0.1", port), _handler(self.state))
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()

    @classmethod
    def from_run(cls, run_dir: str | Path, token: str, faults: Faults | None = None, port: int = 0) -> "MockStore":
        return cls(pd.read_parquet(Path(run_dir) / "store" / "orders.parquet"), token, faults, port)
