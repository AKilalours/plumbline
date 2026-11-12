"""The decision records name files and tests as evidence; a record pointing at something that no longer exists
is worse than none. Every path and test name they cite must exist."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ADR = ROOT / "docs" / "adr"


def test_every_record_is_listed_and_has_the_sections_a_decision_needs():
    pages = sorted(p.name for p in ADR.glob("0*.md"))
    index = (ADR / "README.md").read_text()
    assert pages and all(f"({p})" in index for p in pages)
    for p in pages:
        text = (ADR / p).read_text()
        for part in ("**Context.**", "**Decision.**", "**Rejected.**", "**Costs.**", "**Evidence.**", "**Revisit when**"):
            assert part in text, (p, part)


def test_every_file_and_test_a_record_cites_exists():
    tests = {m for f in (ROOT / "tests").glob("test_*.py") for m in re.findall(r"^def (test_\w+)", f.read_text(), re.M)}
    for p in ADR.glob("0*.md"):
        text = p.read_text()
        for path in re.findall(r"`((?:tests|docs|src|evals|sources)/[\w./-]+)`", text):
            assert (ROOT / path).exists(), (p.name, path)
        for name in re.findall(r"`(test_\w+)`", text):
            if name == "test_decoding":          # Postgres's decoding plugin, not one of ours
                continue
            assert name in tests, (p.name, name)
