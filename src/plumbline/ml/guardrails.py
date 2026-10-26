"""Checks a batch of scores must pass before agents may use it. A failed check publishes nothing.

Agents act on these scores without a person reading them, so the checks ask what a careful analyst
would ask first: is this the population the model learned from, do the inputs look like the
training inputs, and do the scores look like scores.
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

PSI_FAIL, PSI_WARN = 0.25, 0.10
NULL_SHIFT_FAIL = 0.25


class GuardrailFailed(RuntimeError):
    def __init__(self, checks):
        self.checks = checks
        super().__init__("scores not published: " + ", ".join(c["name"] for c in checks if not c["passed"]))


def psi(train: pd.Series, now: pd.Series, bins: int = 10) -> float:
    """Population stability index. Features with few distinct values (flags, counts, one-hot) are
    compared value by value; others on the training deciles. Missing values are their own bucket.
    Works on plain arrays: an earlier version mixed two pandas indexes, which pandas aligned
    silently, and reported a drift of 18 on flags that had not moved."""
    a = np.asarray(train, dtype=float)
    b = np.asarray(now, dtype=float)
    seen = np.unique(a[~np.isnan(a)])

    def buckets(x):
        lab = np.full(len(x), "missing", dtype=object)
        ok = ~np.isnan(x)
        if len(seen) <= bins:
            lab[ok] = np.char.mod("%g", x[ok])
        else:
            edges = np.unique(np.quantile(a[~np.isnan(a)], np.linspace(0, 1, bins + 1)))[1:-1]
            lab[ok] = np.searchsorted(edges, x[ok], side="right").astype(str)
        return pd.Series(lab).value_counts(normalize=True)

    p, q = buckets(a), buckets(b)
    keys = p.index.union(q.index)
    p, q = p.reindex(keys, fill_value=0) + 1e-4, q.reindex(keys, fill_value=0) + 1e-4
    return float(((q - p) * np.log(q / p)).sum())


def check_batch(train_X: pd.DataFrame, score_X: pd.DataFrame, scores: np.ndarray, train_base_rate: float) -> list[dict]:
    checks = []
    def add(name, value, ok, rule):
        checks.append({"name": name, "value": value, "passed": bool(ok), "rule": rule})
    add("same_features_as_training", list(score_X.columns) == list(train_X.columns), list(score_X.columns) == list(train_X.columns),
        "the scoring inputs are exactly the training inputs, in order")
    add("enough_rows", int(len(score_X)), len(score_X) >= 100, ">= 100 people")
    finite = np.isfinite(scores).all() and ((scores >= 0) & (scores <= 1)).all()
    add("scores_are_probabilities", bool(finite), finite, "every score finite and within [0, 1]")
    add("scores_vary", round(float(np.std(scores)), 6), np.std(scores) > 1e-4, "standard deviation > 0.0001")
    mean = float(np.mean(scores))
    add("mean_score_plausible", round(mean, 5), train_base_rate / 3 <= mean <= train_base_rate * 3,
        f"within 3x of the training base rate {round(train_base_rate, 5)}")
    worst, worst_col = 0.0, None
    for c in train_X.columns:
        v = psi(train_X[c], score_X[c])
        if v > worst:
            worst, worst_col = v, c
        shift = abs(train_X[c].isna().mean() - score_X[c].isna().mean())
        if shift > NULL_SHIFT_FAIL:
            add(f"missing_rate_stable:{c}", round(float(shift), 3), False, f"share missing moves by <= {NULL_SHIFT_FAIL}")
    add("input_drift_psi", {"max": round(worst, 3), "feature": worst_col, "warn_above": PSI_WARN},
        worst <= PSI_FAIL, f"population stability index <= {PSI_FAIL} for every feature")
    return checks


def publish(con, scores: pd.DataFrame, version: str, as_of: str, checks: list[dict], not_scored: pd.DataFrame) -> dict:
    """Record the run; write scores only if every check passed."""
    ok = all(c["passed"] for c in checks)
    con.execute("CREATE SCHEMA IF NOT EXISTS ml")
    con.execute("""CREATE TABLE IF NOT EXISTS ml.model_runs (model_version VARCHAR, as_of DATE, run_at TIMESTAMP,
                   published BOOLEAN, scored INTEGER, not_scored INTEGER, checks JSON)""")
    con.execute("""CREATE TABLE IF NOT EXISTS ml.home_buyer_scores (person_key VARCHAR, as_of DATE, score DOUBLE,
                   decile INTEGER, model_version VARCHAR)""")
    con.execute("BEGIN")
    try:
        con.execute("INSERT INTO ml.model_runs VALUES (?, ?, now(), ?, ?, ?, ?)",
                    [version, as_of, ok, len(scores), len(not_scored), json.dumps(checks, default=str)])
        if ok:
            con.execute("DELETE FROM ml.home_buyer_scores WHERE as_of = ? AND model_version = ?", [as_of, version])
            con.register("_s", scores)
            con.execute("INSERT INTO ml.home_buyer_scores SELECT person_key, CAST(as_of AS DATE), score, decile, model_version FROM _s")
            con.unregister("_s")
            con.register("_n", not_scored)
            con.execute("CREATE OR REPLACE TABLE ml.home_buyer_not_scored AS SELECT * FROM _n")
            con.unregister("_n")
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    if not ok:
        raise GuardrailFailed(checks)
    return {"published": True, "scored": len(scores), "not_scored": len(not_scored)}
