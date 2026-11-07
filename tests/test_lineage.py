"""Lineage: dbt's graph, joined to the Python that writes the tables dbt reads. The two halves must meet."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from plumbline.products import lineage as L

ROOT = Path(__file__).resolve().parents[1]
SAVED = json.loads((ROOT / "docs" / "results" / "lineage.json").read_text())


def test_every_table_dbt_reads_has_a_writer_in_this_repo():
    written = {b for _, b, _ in L.WRITERS}
    assert set(SAVED["dbt_sources"]) <= written, set(SAVED["dbt_sources"]) - written
    for _, _, module in L.WRITERS:
        assert (ROOT / module).exists(), module


def test_no_edge_points_at_a_table_that_is_not_in_the_graph():
    ids = {n["id"] for n in SAVED["nodes"]}
    assert all(a in ids and b in ids for a, b in SAVED["edges"])
    roots = {n["id"] for n in SAVED["nodes"] if n["kind"] == "delivery"}
    assert {"NYC PLUTO (real)", "Census ACS (real)", "Store API", "Privacy requests"} <= roots


@pytest.mark.skipif(not (shutil.which("dbt") or Path(__import__("sys").executable).with_name("dbt").exists()), reason="dbt not installed")
def test_the_saved_lineage_is_what_dbt_parses_today():
    """A model added or rewired without regenerating lineage.json would leave the app's picture wrong."""
    fresh = L.build(L.manifest())
    assert fresh["edges"] == SAVED["edges"] and [n["id"] for n in fresh["nodes"]] == [n["id"] for n in SAVED["nodes"]]
