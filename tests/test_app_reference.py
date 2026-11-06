"""The public app's city is built on real NYC buildings and real Census income, as its footer says."""
from __future__ import annotations

import hashlib
import json

from plumbline.app import demo as D
from plumbline.synth.reference import load_reference


def test_the_app_city_is_built_on_real_reference_data_not_the_test_fixtures():
    ref = load_reference(D.REFERENCE)
    assert ref.source == "real" and "fixtures" not in str(D.REFERENCE)
    assert len(ref.lots) > 20_000 and not ref.lots.address.str.contains("FIXTURE").any()
    assert set(ref.lots.borough) == {"BK", "BX", "MN", "QN", "SI"}


def test_the_reference_files_are_the_ones_its_marker_describes():
    marker = (D.REFERENCE / "REFERENCE_SOURCE").read_text()
    for f in ("pluto_lots.csv", "acs_income.csv"):
        assert f"\n{f} sha256={hashlib.sha256((D.REFERENCE / f).read_bytes()).hexdigest()}" in marker


def test_a_world_built_from_other_reference_files_is_rebuilt_not_reused(tmp_path, monkeypatch):
    """The reuse check once compared size and seed only, so a running app kept its fixture city."""
    root = tmp_path / "w"
    root.mkdir()
    (root / "warehouse.duckdb").write_bytes(b"")
    (root / "summary.json").write_text(json.dumps({"n_people": 50, "seed": 1, "reference": "fixture-built", "pipeline": {}}))
    rebuilt = []
    monkeypatch.setattr(D.shutil, "rmtree", lambda p, **k: (rebuilt.append(p), (_ for _ in ()).throw(SystemExit)))
    try:
        D.build(root, n_people=50, seed=1)
    except SystemExit:
        pass
    assert rebuilt == [root]


def test_the_ai_eval_city_stays_on_the_data_its_recordings_were_made_on():
    """Switching the app to real buildings changed the eval's partner files, so no recorded model call matched
    and the replay scored the model 5 of 31 instead of 29 of 31."""
    from plumbline.ai import evals as E
    assert E.EVAL_REFERENCE != D.REFERENCE and (E.EVAL_REFERENCE / "REFERENCE_SOURCE").read_text().startswith("fixture")


def test_the_page_caches_are_keyed_on_the_world_not_just_its_size():
    """Keyed on size and seed, the live Overview showed the fixture city's numbers after the rebuild."""
    import ast
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "streamlit_app.py").read_text()
    fn = next(n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.FunctionDef) and n.name == "key")
    assert "reference" in ast.unparse(fn)
    a = D.Demo(Path("r"), Path("r"), Path("w"), 4000, 11, {}, "aaa")
    b = D.Demo(Path("r"), Path("r"), Path("w"), 4000, 11, {}, "bbb")
    assert a != b
