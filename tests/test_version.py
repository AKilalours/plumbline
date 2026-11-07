"""The code fingerprint the live check uses: the same code gives the same value anywhere, and any change to the
app's code changes it. On 2026-11-07 the live app kept serving old code after a push while every check passed."""
from __future__ import annotations

import shutil
from pathlib import Path

from plumbline.app.version import fingerprint, files

ROOT = Path(__file__).resolve().parents[1]


def _copy(tmp: Path) -> Path:
    for p in files(ROOT):
        dst = tmp / p.relative_to(ROOT)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(p, dst)
    return tmp


def test_a_copy_of_the_code_has_the_same_fingerprint_wherever_it_lives(tmp_path):
    copy = _copy(tmp_path / "elsewhere")
    (copy / "src/plumbline/__pycache__").mkdir(parents=True, exist_ok=True)
    (copy / "src/plumbline/__pycache__/x.py").write_text("ignored")                  # caches do not count
    (copy / "src/plumbline/app/dos.py").write_bytes(b"x = 1\r\n")
    (copy / "src/plumbline/app/dos.py").unlink()
    assert fingerprint(copy) == fingerprint(ROOT) and len(fingerprint(ROOT)) == 10


def test_any_change_to_the_app_code_changes_the_fingerprint(tmp_path):
    copy = _copy(tmp_path / "changed")
    before = fingerprint(copy)
    p = copy / "streamlit_app.py"
    p.write_text(p.read_text().replace("Send a vendor file", "Break a vendor file"))
    fingerprint.cache_clear()
    assert fingerprint(copy) != before
