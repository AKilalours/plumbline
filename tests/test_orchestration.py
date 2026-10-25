"""The Dagster layer runs the same loader and reports the same invariants."""
from __future__ import annotations

from pathlib import Path

import pytest

dg = pytest.importorskip("dagster")

from plumbline.ingest.orchestration import PlumblineConfig, _violations, checks, defs, person_level_deletion, persons, warehouse  # noqa: E402
from plumbline.synth.config import SynthConfig  # noqa: E402
from plumbline.synth.generate import generate  # noqa: E402
from plumbline.synth.reference import load_reference  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def test_definitions_load():
    assert defs.resolve_job_def("ingest_job") is not None


def test_materialize_runs_checks_and_they_pass(tmp_path):
    run = tmp_path / "run"
    generate(load_reference(ROOT / "tests/fixtures/reference"), SynthConfig(seed=4, n_people=800), run)
    cfg = PlumblineConfig(run_dir=str(run), warehouse=str(tmp_path / "wh.duckdb"), contracts_dir=str(ROOT / "sources"))
    result = dg.materialize([warehouse, persons, person_level_deletion, *checks], resources={"plumbline": cfg})
    assert result.success
    evals = result.get_asset_check_evaluations()
    assert len(evals) == 4 and all(e.passed for e in evals)
    mat = result.asset_materializations_for_node("warehouse")[0]
    assert mat.metadata["deliveries_loaded"].value == 29


def test_a_check_whose_counter_was_renamed_fails_loudly(tmp_path):
    """Regression: renaming an invariant left a check matching nothing, which reads as green."""
    run = tmp_path / "run"
    generate(load_reference(ROOT / "tests/fixtures/reference"), SynthConfig(seed=4, n_people=300), run)
    from plumbline.ingest.contracts import load_contracts
    from plumbline.ingest.engine import ingest
    ingest(run, tmp_path / "wh.duckdb", load_contracts(ROOT / "sources"))
    cfg = PlumblineConfig(run_dir=str(run), warehouse=str(tmp_path / "wh.duckdb"), contracts_dir=str(ROOT / "sources"))
    assert _violations(cfg, ("erasable_rows_in_bronze",))
    with pytest.raises(RuntimeError, match="no invariant"):
        _violations(cfg, ("a_name_that_does_not_exist",))
