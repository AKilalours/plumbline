"""Reason codes are exact for the champion, not an after-the-fact approximation: for every person the
reasons add back up to the model's own score."""
from __future__ import annotations

import numpy as np
import pandas as pd

from plumbline.ml import explain as E
from plumbline.ml.train import candidates


def _fit(n=3000, seed=0):
    rng = np.random.default_rng(seed)
    X = pd.DataFrame({"log_zip_income": rng.normal(11, 0.5, n), "orders_window": rng.poisson(1, n).astype(float),
                      "age_years": rng.normal(40, 12, n), "small_building": rng.integers(0, 2, n).astype(float)})
    X.loc[rng.random(n) < 0.2, "age_years"] = np.nan                      # missing values get their own reason
    logit = -4 + 1.2 * (X.log_zip_income - 11) + 0.5 * X.orders_window
    y = (rng.random(n) < 1 / (1 + np.exp(-logit))).astype(int)
    return candidates()["logistic"].fit(X, y), X


def test_reasons_rebuild_every_score_exactly():
    model, X = _fit()
    assert E.faithfulness(model, X) < 1e-9


def test_the_strongest_reason_is_the_input_that_drives_the_outcome_and_labels_are_plain():
    model, X = _fit()
    C, _ = E.contributions(model, X)
    top = C.abs().idxmax(axis=1).value_counts()
    assert top.index[0] == "log_zip_income"
    r = E.reasons(C.iloc[[0]])[0]
    assert len(r) == 3 and all(isinstance(lbl, str) and "_" not in lbl for lbl, _ in r)
    assert E.label("missingindicator_age_years") == "no age on file"


def test_the_saved_100k_reasons_are_exact_and_the_readme_quotes_them():
    import json
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    r = json.loads((root / "docs" / "results" / "reason_codes.json").read_text())
    assert r["faithfulness_max_gap"] < 1e-9 and len(r["examples"]) == 5
    first = next(iter(r["top_decile_first_reason"]))
    assert first in (root / "README.md").read_text()
