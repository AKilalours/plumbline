"""Scratch folders on a public server: what is left behind is removed.

A visitor's agent-lab folder (plumbline-agent-<id>) must live as long as their session, so it cannot be removed
when a request ends. `sweep` removes any whose newest file nobody has touched for `idle_s`; the app calls it when a
new session starts. Folders made for one computation use tempfile.TemporaryDirectory and are gone when it ends.
"""
from __future__ import annotations

import shutil
import tempfile
import time
from pathlib import Path

IDLE_S = 6 * 3600


def _newest(p: Path) -> float:
    try:
        return max([p.stat().st_mtime] + [f.stat().st_mtime for f in p.rglob("*")])
    except OSError:
        return time.time()                                   # being written right now: leave it


def sweep(prefix: str = "plumbline-agent-", idle_s: float = IDLE_S, root: str | Path | None = None, now: float | None = None) -> int:
    """Remove `<root>/<prefix>*` folders idle for longer than idle_s. Returns how many were removed."""
    now, root = now or time.time(), Path(root or tempfile.gettempdir())
    removed = 0
    for p in root.glob(prefix + "*"):
        if p.is_dir() and now - _newest(p) > idle_s:
            shutil.rmtree(p, ignore_errors=True)
            removed += 1
    return removed
