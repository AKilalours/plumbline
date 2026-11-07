"""The loader's "current" rule on Spark gives the same rows as DuckDB, and the comparison would notice if it did
not: each deliberate break of the Spark version (forget deletions, forget that _v2 replaces _v1) is caught."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

pytest.importorskip("pyspark")
if not shutil.which("java"):
    pytest.skip("Spark needs Java", allow_module_level=True)

from plumbline.ingest import spark_current as SC  # noqa: E402
from plumbline.ingest.contracts import load_contracts  # noqa: E402
from plumbline.ingest.engine import ingest  # noqa: E402
from plumbline.synth.config import SynthConfig  # noqa: E402
from plumbline.synth.generate import generate  # noqa: E402
from plumbline.synth.reference import load_reference  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def warehouse(tmp_path_factory):
    """A world, plus the two situations the rule exists for: a corrected vendor file (_v2) that drops ten rows,
    and a deletion request recorded before the purge has run (the views must already hide that person)."""
    import csv
    import io
    import duckdb
    d = tmp_path_factory.mktemp("spark")
    generate(load_reference(ROOT / "tests" / "fixtures" / "reference"), SynthConfig(seed=21, n_people=1500), d / "run")
    week = sorted((d / "run" / "sources" / "vendor").glob("vendor_week_0[1-4]_*.csv"))[-1]
    rows = list(csv.DictReader(io.StringIO(week.read_text())))
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(rows[0]), lineterminator="\n")
    w.writeheader()
    w.writerows(rows[10:])
    (week.parent / (week.stem + "_v2.csv")).write_text(buf.getvalue())
    ingest(d / "run", d / "w.duckdb", load_contracts(ROOT / "sources"))
    with duckdb.connect(str(d / "w.duckdb")) as con:
        sha = con.execute("SELECT _sha_email FROM current_crm WHERE _sha_email IS NOT NULL AND _sha_phone IS NULL LIMIT 1").fetchone()[0]
        con.execute("INSERT INTO suppression VALUES ('REQ-NOT-PURGED-YET', ?, NULL, DATE '2026-01-01', 'test')", [sha])
    return d / "w.duckdb"


def test_spark_and_duckdb_agree_on_every_current_row(warehouse):
    r = SC.compare(warehouse)
    assert r["identical"], r
    assert all(r["spark_rows"][s] for s in ("crm", "vendor", "email"))


def test_a_spark_version_that_forgets_deletions_is_caught(warehouse):
    r = SC.compare(warehouse, erase=False)
    assert not r["identical"] and r["only_in_spark"]["crm"] > 0, r


def test_a_spark_version_that_ignores_corrected_files_is_caught(warehouse):
    r = SC.compare(warehouse, newest_only=False)
    assert not r["identical"] and r["only_in_spark"]["vendor"] > 0, r
