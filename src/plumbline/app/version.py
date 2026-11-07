"""A fingerprint of the code the app is running, so a stale deploy cannot pass for the new one.

The deployed app shows it in its footer; the live-app check computes the same fingerprint from the commit it is
checking and fails if the two differ. It hashes the files that decide what the app does (the page script, the
package source and the source contracts), not git metadata, so it works wherever the code was copied to.
"""
from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PATTERNS = ("streamlit_app.py", "src/plumbline/**/*.py", "sources/**/*.yaml")


def files(root: Path = ROOT) -> list[Path]:
    out = set()
    for pat in PATTERNS:
        out.update(p for p in root.glob(pat) if p.is_file() and "__pycache__" not in p.parts)
    return sorted(out, key=lambda p: p.relative_to(root).as_posix())


@lru_cache(maxsize=4)
def fingerprint(root: Path = ROOT) -> str:
    h = hashlib.sha256()
    for p in files(root):
        h.update(p.relative_to(root).as_posix().encode() + b"\0")
        h.update(p.read_bytes().replace(b"\r\n", b"\n") + b"\0")
    return h.hexdigest()[:10]


if __name__ == "__main__":
    print(fingerprint())
