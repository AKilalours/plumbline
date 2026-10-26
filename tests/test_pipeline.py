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
    assert list(summary) == ["store_build", "store_sync", "ingest", "identity", "person_deletion", "products", "model"]
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
