"""Income band estimates for the people the vendor does not cover.

The vendor gives an income band for 51,502 of 68,088 people. The other 16,586 have none, and they
are who this model is for. It learns from the vendor-covered people and is scored on the people
it will actually be used on.

Two decisions the numbers depend on:
  - Features only from what the uncovered people have: the brand's own data, the building (PLUTO)
    and the zip (Census). Vendor age or homeowner flag would help on the training rows and be
    missing on every row it is used for (train/serve skew), so they are not features.
  - Train and test are split by household, not by person: people in one household share one income,
    so a spouse in training and the other in test would be partly memorised.

Labels are the vendor's bands, which are themselves noisy (right 65% of the time in this world),
so every result is reported twice: against the vendor label, and against the true band from the
answer key, including for the uncovered people, which only the answer key can check.
"""
from __future__ import annotations

import json
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import GroupShuffleSplit
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from .features import matrix
from .train import SEEDS, SeedEnsemble

AS_OF = "2025-12-31"
VENDOR_COLUMNS = ("income_band", "age_years", "vendor_homeowner")   # never features: absent for the target people
BANDS = np.arange(1, 7)


class IncomeGateFailed(RuntimeError):
    pass


def _features(snap: pd.DataFrame, families) -> pd.DataFrame:
    X = matrix(snap, families)
    return X[[c for c in X.columns if c not in VENDOR_COLUMNS]]


def band_metrics(true: np.ndarray, pred: np.ndarray) -> dict:
    true, pred = np.asarray(true, int), np.asarray(pred, int)
    return {"rows": int(len(true)), "exact": round(float((true == pred).mean()), 4),
            "within_one_band": round(float((np.abs(true - pred) <= 1).mean()), 4),
            "mean_abs_band_error": round(float(np.abs(true - pred).mean()), 4),
            # a constant prediction (the "most common band" baseline) has no rank order: no correlation
            "spearman": round(float(spearmanr(true, pred).statistic), 4) if len(set(pred)) > 1 else None}


def candidates() -> dict:
    return {"multinomial_logistic": make_pipeline(SimpleImputer(strategy="median", add_indicator=True), StandardScaler(),
                                                  LogisticRegression(C=1.0, max_iter=3000)),
            "gradient_boosting": SeedEnsemble(seeds=SEEDS, max_iter=300, learning_rate=0.05, max_leaf_nodes=15,
                                              l2_regularization=1.0, min_samples_leaf=50)}


def _predict_band(model, X) -> np.ndarray:
    """The band that minimises expected absolute error: the median of the predicted distribution."""
    p = model.predict_proba(X)
    classes = np.asarray(getattr(model, "classes_", None) if not hasattr(model, "steps") else model.steps[-1][1].classes_)
    cdf = np.cumsum(p, axis=1)
    return classes[(cdf >= 0.5).argmax(axis=1)]


def run(warehouse: str | Path, run_dir: str | Path) -> dict:
    run = Path(run_dir)
    with duckdb.connect(str(warehouse), read_only=True) as con:
        snap = con.execute("SELECT * FROM ml.feature_snapshots WHERE as_of = ?", [AS_OF]).df()
        hh = con.execute("SELECT person_key, household_key FROM products.dim_person").df()
        from ..products.evaluate import _owner
        owner = _owner(con, run)
    truth = pd.read_parquet(run / "truth" / "people.parquet", columns=["person_id", "income_band"]).set_index("person_id").income_band
    snap = snap.merge(hh, on="person_key", how="left")
    snap["true_band"] = snap.person_key.map(owner).map(truth)
    snap["household_key"] = snap.household_key.fillna(snap.person_key)
    covered = snap[snap.income_band.notna()].reset_index(drop=True)
    target = snap[snap.income_band.isna() & snap.first_party.fillna(False).astype(bool)].reset_index(drop=True)
    families = sorted(covered.bldg_family.dropna().unique())

    split = GroupShuffleSplit(n_splits=1, test_size=0.25, random_state=7)
    tr_i, te_i = next(split.split(covered, groups=covered.household_key))
    tr, te = covered.iloc[tr_i], covered.iloc[te_i]
    assert not set(tr.household_key) & set(te.household_key), "a household is on both sides of the split"
    val_split = GroupShuffleSplit(n_splits=1, test_size=0.25, random_state=11)
    fit_i, val_i = next(val_split.split(tr, groups=tr.household_key))
    fit, val = tr.iloc[fit_i], tr.iloc[val_i]

    # 1. choose on validation (vendor labels), never on test
    sel = {}
    for name, m in candidates().items():
        m.fit(_features(fit, families), fit.income_band.astype(int))
        sel[name] = band_metrics(val.income_band, _predict_band(m, _features(val, families)))
    chosen = min(sel, key=lambda k: sel[k]["mean_abs_band_error"])
    model = candidates()[chosen].fit(_features(tr, families), tr.income_band.astype(int))
    pred_te = _predict_band(model, _features(te, families))

    # baselines: the most common band, and zip income alone
    majority = int(tr.income_band.mode().iat[0])
    zip_only = make_pipeline(SimpleImputer(strategy="median"), LogisticRegression(max_iter=2000)).fit(
        tr[["log_zip_income"]].astype(float), tr.income_band.astype(int))

    def zip_band(df):
        p = zip_only.predict_proba(df[["log_zip_income"]].astype(float))
        return zip_only.classes_[(np.cumsum(p, axis=1) >= 0.5).argmax(axis=1)]

    has_truth_te = te.true_band.notna()
    out = {"as_of": AS_OF, "algorithm": chosen, "selection_on_validation": sel,
           "people_with_vendor_band": int(len(covered)), "people_without_one_known_first_party": int(len(target)),
           "test_vs_vendor_label": {"model": band_metrics(te.income_band, pred_te),
                                    "zip_income_only": band_metrics(te.income_band, zip_band(te)),
                                    "most_common_band": band_metrics(te.income_band, np.full(len(te), majority))},
           "test_vs_true_band": {"model": band_metrics(te.true_band[has_truth_te], pred_te[has_truth_te.to_numpy()]),
                                 "vendor_label_itself": band_metrics(te.true_band[has_truth_te], te.income_band[has_truth_te]),
                                 "zip_income_only": band_metrics(te.true_band[has_truth_te], zip_band(te[has_truth_te]))}}
    pred_target = _predict_band(model, _features(target, families))
    ht = target.true_band.notna()
    out["uncovered_people_vs_true_band"] = {"model": band_metrics(target.true_band[ht], pred_target[ht.to_numpy()]),
                                            "zip_income_only": band_metrics(target.true_band[ht], zip_band(target[ht]))}
    out["_model"], out["_target"], out["_pred"] = model, target, pred_target
    return out


def check(out: dict) -> list[dict]:
    """Publish only if the model beats zip income alone on held-out people, by the vendor's own labels."""
    m, z = out["test_vs_vendor_label"]["model"], out["test_vs_vendor_label"]["zip_income_only"]
    shares = pd.Series(out["_pred"]).value_counts(normalize=True)
    return [
        {"name": "beats_zip_income_alone", "value": [m["mean_abs_band_error"], z["mean_abs_band_error"]],
         "passed": bool(m["mean_abs_band_error"] < z["mean_abs_band_error"]), "rule": "lower mean band error than zip income alone"},
        {"name": "within_one_band", "value": m["within_one_band"], "passed": bool(m["within_one_band"] >= 0.6),
         "rule": "at least 60% of held-out people within one band"},
        {"name": "not_one_band_for_everyone", "value": round(float(shares.max()), 3), "passed": bool(shares.max() < 0.8),
         "rule": "no single band for more than 80% of people"},
    ]


def publish(warehouse, out: dict) -> dict:
    checks = check(out)
    t = out["_target"]
    rows = pd.DataFrame({"person_key": t.person_key, "as_of": AS_OF, "estimated_income_band": out["_pred"].astype(int),
                         "model": "income_band_v1", "algorithm": out["algorithm"]})
    with duckdb.connect(str(warehouse)) as con:
        con.execute("CREATE SCHEMA IF NOT EXISTS ml")
        con.execute("CREATE TABLE IF NOT EXISTS ml.model_runs_income (as_of DATE, run_at TIMESTAMP, published BOOLEAN, checks JSON, report JSON)")
        ok = all(c["passed"] for c in checks)
        report = {k: v for k, v in out.items() if not k.startswith("_")}
        con.execute("BEGIN")
        con.execute("INSERT INTO ml.model_runs_income VALUES (?, now(), ?, ?, ?)", [AS_OF, ok, json.dumps(checks), json.dumps(report, default=str)])
        if ok:
            con.register("_r", rows)
            con.execute("CREATE OR REPLACE TABLE ml.income_estimates AS SELECT * FROM _r")
            con.unregister("_r")
        con.execute("COMMIT")
    if not ok:
        raise IncomeGateFailed("income estimates not published: " + ", ".join(c["name"] for c in checks if not c["passed"]))
    return {"published": True, "people_estimated": int(len(rows)), "checks": {c["name"]: c["value"] for c in checks}}
