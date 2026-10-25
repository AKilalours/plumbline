"""Incremental, resumable sync of orders from the store API.

What makes it safe:
- Incremental: asks only for orders updated since the last watermark, minus a 5-minute overlap,
  because two orders can share a timestamp and a strict "greater than" would lose one. The overlap
  re-fetches a few orders; the loader's row dedup makes that free.
- Retries: 429 waits for Retry-After; 5xx, dropped connections and half-sent bodies back off
  exponentially with jitter. A 401 stops at once (retrying a bad token only gets it locked).
  After max_retries the sync raises and the watermark does not move.
- Resumable: each page is written to the landing folder atomically, then the state file records the
  next cursor. A crash between pages resumes from the last saved cursor; a crash inside a page re-fetches
  that page, whose rows the loader already knows.
- The token is read from the environment and is never written to a landing file, the state file or a log.
"""
from __future__ import annotations

import csv
import http.client
import io
import json
import os
import random
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

from ...store.db import ORDER_COLUMNS

TRANSIENT = (urllib.error.URLError, http.client.IncompleteRead, http.client.RemoteDisconnected,
             ConnectionResetError, socket.timeout, json.JSONDecodeError)


class StoreAPIError(RuntimeError):
    pass


class AuthError(StoreAPIError):
    pass


@dataclass
class SyncStats:
    requests: int = 0
    retries: int = 0
    throttled: int = 0
    pages: int = 0
    orders: int = 0
    waited_seconds: float = 0.0
    resumed: bool = False
    by_error: dict = field(default_factory=dict)


class StoreClient:
    def __init__(self, base_url: str, token: str, *, max_retries: int = 8, base_delay: float = 0.5,
                 max_delay: float = 30.0, timeout: float = 30.0, sleep: Callable[[float], None] = time.sleep,
                 jitter_seed: int | None = None):
        if not token:
            raise AuthError("no store token; set STORE_API_TOKEN")
        self.base_url = base_url.rstrip("/")
        self._token = token
        self.max_retries, self.base_delay, self.max_delay, self.timeout = max_retries, base_delay, max_delay, timeout
        self.sleep = sleep
        self.rand = random.Random(jitter_seed)

    def __repr__(self) -> str:  # never show the token
        return f"StoreClient({self.base_url!r}, token=***)"

    def get(self, path_and_query: str, stats: SyncStats) -> tuple[dict, str | None]:
        url = self.base_url + path_and_query
        for attempt in range(self.max_retries + 1):
            stats.requests += 1
            try:
                req = urllib.request.Request(url, headers={"X-Store-Token": self._token, "Accept": "application/json"})
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    body = json.loads(r.read())
                    return body, _next_link(r.headers.get("Link"))
            except urllib.error.HTTPError as e:
                if e.code == 401:
                    raise AuthError("store rejected the token (401)") from None
                if e.code == 429:
                    stats.throttled += 1
                    wait = float(e.headers.get("Retry-After") or self._backoff(attempt))
                    reason = "429"
                elif 500 <= e.code < 600:
                    wait, reason = self._backoff(attempt), str(e.code)
                else:
                    raise StoreAPIError(f"HTTP {e.code} for {path_and_query.split('?')[0]}") from None
            except TRANSIENT as e:
                wait, reason = self._backoff(attempt), type(e).__name__
            if attempt == self.max_retries:
                raise StoreAPIError(f"gave up after {self.max_retries} retries (last: {reason})")
            stats.retries += 1
            stats.by_error[reason] = stats.by_error.get(reason, 0) + 1
            stats.waited_seconds += wait
            self.sleep(wait)
        raise AssertionError("unreachable")

    def _backoff(self, attempt: int) -> float:
        return min(self.max_delay, self.base_delay * 2 ** attempt) * (0.5 + self.rand.random() / 2)


def _next_link(link: str | None) -> str | None:
    if not link or 'rel="next"' not in link:
        return None
    return link.split(";")[0].strip().strip("<>")


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".partial")
    tmp.write_text(text)
    os.replace(tmp, path)


def _to_csv(orders: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=ORDER_COLUMNS, extrasaction="ignore", lineterminator="\n")
    w.writeheader()
    w.writerows(orders)
    return buf.getvalue()


def sync_orders(client: StoreClient, landing_dir: str | Path, *, page_size: int = 250,
                overlap: timedelta = timedelta(minutes=5),
                after_page: Callable[[int], None] | None = None) -> SyncStats:
    """Fetch new and changed orders into landing_dir. `after_page` exists so tests can crash mid-sync."""
    landing = Path(landing_dir)
    landing.mkdir(parents=True, exist_ok=True)
    state_path = landing / "_sync_state.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {"watermark": None, "next_sync": 1}
    stats = SyncStats()

    if state.get("in_progress"):
        run = state["in_progress"]
        stats.resumed = True
        path = run["next_path"]
    else:
        since = ""
        if state["watermark"]:
            since = (datetime.fromisoformat(state["watermark"]) - overlap).strftime("%Y-%m-%dT%H:%M:%S")
        run = {"sync_id": state["next_sync"], "page": 0, "max_seen": state["watermark"],
               "next_path": "/orders.json?" + urllib.parse.urlencode({"updated_at_min": since, "limit": page_size})}
        path = run["next_path"]

    while path:
        body, nxt = client.get(path, stats)
        orders = body.get("orders", [])
        run["page"] += 1
        if orders:
            _atomic_write(landing / f"orders_s{run['sync_id']:04d}_p{run['page']:05d}.csv", _to_csv(orders))
            top = max(o["updated_at"] for o in orders)
            run["max_seen"] = max(run["max_seen"] or top, top)
        stats.pages += 1
        stats.orders += len(orders)
        run["next_path"] = nxt
        state["in_progress"] = run if nxt else None
        if not nxt:
            state["watermark"] = run["max_seen"]
            state["next_sync"] = run["sync_id"] + 1
        _atomic_write(state_path, json.dumps(state, indent=2))
        if after_page:
            after_page(run["page"])
        path = nxt
    return stats
