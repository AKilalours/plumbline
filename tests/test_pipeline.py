"""`python -m plumbline` runs every stage in order and reports each one."""
from __future__ import annotations

import json
from pathlib import Path

from plumbline.__main__ import main
from plumbline.synth.config import SynthConfig
from plumbline.synth.generate import generate
from plumbline.synth.reference import load_reference

ROOT = Path(__file__).resolve().parents[1]


def test_one_command_runs_the_whole_pipeline(tmp_path, capsys, monkeypatch):
    run = tmp_path / "run"
    generate(load_reference(ROOT / "tests" / "fixtures" / "reference"), SynthConfig(seed=3, n_people=1200), run)
    monkeypatch.setattr("plumbline.ingest.connectors.store_api.time.sleep", lambda s: None)
    args = ["--run-dir", str(run), "--warehouse", str(tmp_path / "w.duckdb"), "--contracts", str(ROOT / "sources"), "--score",
            "--ref", str(ROOT / "tests" / "fixtures" / "reference"), "--models", str(tmp_path / "models")]
    assert main(args) == 0
    out = capsys.readouterr().out
    summary = json.loads(out[out.index("{"):])
    assert list(summary) == ["store_build", "store_sync", "ingest", "identity", "person_deletion", "products", "model", "more_models", "experiment"]
    # 1,200 people buy too few homes to train on: the stage says so instead of reporting noise
    assert "fewer than 20 home purchases" in summary["model"]["skipped"]
    assert summary["products"]["tests"] == summary["products"]["tests_passed"] >= 20
    assert summary["ingest"]["violations"] == {}
    assert summary["identity"]["accuracy"]["f1"] > 0.9
    assert summary["person_deletion"]["innocent_records_removed"] == 0
    assert "Iteration" not in out, "library training logs must not flood the output"
    # a second run is safe: the store is not rebuilt and nothing is loaded twice
    assert main(args) == 0
    again = json.loads((o := capsys.readouterr().out)[o.index("{"):])
    assert "store_build" not in again and again["ingest"]["rows_loaded"] == 0



def test_missing_run_explains_how_to_make_one(tmp_path, capsys):
    assert main(["--run-dir", str(tmp_path / "nope"), "--warehouse", str(tmp_path / "w.duckdb")]) == 2
    assert "plumbline.synth.cli" in capsys.readouterr().out


def _fake_run(tmp_path, version=None, world="W1", store_world="W1"):
    import json as _j
    run = tmp_path / "run"
    (run / "store").mkdir(parents=True)
    (run / "sources" / "store").mkdir(parents=True)
    (run / "campaign").mkdir()
    (run / "truth").mkdir()
    m = {"fingerprint": world, "config": {"n_people": 100, "seed": 7}}
    if version is not None:
        m["generator_version"] = version
    (run / "manifest.json").write_text(_j.dumps(m))
    (run / "deliveries.json").write_text("[]")
    (run / "store" / "store_manifest.json").write_text(_j.dumps({"world_fingerprint": store_world}))
    (run / "store" / "orders.parquet").write_text("x")
    (run / "sources" / "store" / "orders_old.csv").write_text("x")
    (run / "truth" / "campaign_truth.parquet").write_text("x")
    return run


def test_a_world_from_another_generator_version_is_refused(tmp_path, capsys):
    """Regression: the generator changed (one household per home) while old worlds stayed on disk, and
    the scale script reused any world it found. Measuring an old world as a new one is now impossible."""
    run = _fake_run(tmp_path, version=1)
    assert main(["--run-dir", str(run), "--warehouse", str(tmp_path / "w.duckdb")]) == 2
    out = capsys.readouterr().out
    assert "generator version 1" in out and "--force" in out


def test_store_and_campaign_built_from_another_world_are_removed(tmp_path):
    from plumbline.__main__ import drop_derived_if_stale
    run = _fake_run(tmp_path, version=2, world="NEW", store_world="OLD")
    assert drop_derived_if_stale(run)
    assert not (run / "store").exists() and not (run / "sources" / "store").exists()
    assert not (run / "campaign").exists() and not (run / "truth" / "campaign_truth.parquet").exists()
    same = _fake_run(tmp_path / "b", version=2, world="W", store_world="W")
    assert not drop_derived_if_stale(same) and (same / "store" / "orders.parquet").exists()


def test_force_replaces_the_folder_instead_of_writing_into_it(tmp_path):
    from plumbline.synth.cli import main as synth
    args = ["--ref", str(ROOT / "tests" / "fixtures" / "reference"), "--out", str(tmp_path), "--n-people", "300",
            "--seed", "3", "--allow-fixture"]
    assert synth(args) == 0
    run = next(tmp_path.glob("*seed3_n300"))
    (run / "store").mkdir()
    (run / "store" / "orders.parquet").write_text("left over from another world")
    assert synth(args + ["--force"]) == 0
    assert not (run / "store").exists()
