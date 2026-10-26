"""Train and evaluate the home-buyer model with a time split that cannot see the future.

    select:  train on snapshots 2024-03-31, 2024-06-30, 2024-09-30; choose the model on 2025-03-31
    final:   refit the chosen model on 2024-03-31 .. 2024-12-31; report once on 2025-06-30

A snapshot is used for training only if its whole label window (cutoff + 180 days) closed before
the date it is evaluated on; check_no_label_leak enforces that. People already known to have bought
by the cutoff are excluded, as they would be in production (deed records up to that day).
"""
from __future__ import annotations

import hashlib
import json
import pickle
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.inspection import permutation_importance
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from .features import CATEGORICAL, matrix
from .labels import HORIZON_DAYS, attach_labels, oracle_score

SELECT_TRAIN = ["2024-03-31", "2024-06-30", "2024-09-30"]
SELECT_VAL = "2025-03-31"
FINAL_TRAIN = ["2024-03-31", "2024-06-30", "2024-09-30", "2024-12-31"]
TEST = "2025-06-30"
LATEST = "2025-12-31"
ALL_CUTOFFS = sorted(set(SELECT_TRAIN + [SELECT_VAL] + FINAL_TRAIN + [TEST, LATEST]))
SEED = 7


class LeakError(RuntimeError):
    pass


class NotEnoughOutcomes(RuntimeError):
    pass


MIN_POSITIVES = 20


def check_enough_outcomes(parts: dict) -> None:
    """A model fitted or scored on a handful of purchases says nothing; refuse instead of reporting noise."""
    low = {name: int(df.label.sum()) for name, df in parts.items() if int(df.label.sum()) < MIN_POSITIVES}
    if low:
        raise NotEnoughOutcomes(f"fewer than {MIN_POSITIVES} home purchases in {low}; a larger world is needed")


def check_no_label_leak(train_cutoffs: list[str], eval_cutoff: str, horizon: int = HORIZON_DAYS) -> None:
    for t in train_cutoffs:
        closes = pd.Timestamp(t) + pd.Timedelta(days=horizon)
        if closes > pd.Timestamp(eval_cutoff):
            raise LeakError(f"training snapshot {t} has outcomes until {closes.date()}, after the evaluation date {eval_cutoff}")


def check_not_above_ceiling(model_metrics: dict, ceiling: dict, tolerance: float = 0.01) -> None:
    """No honest model can rank better than the rule that generated the outcomes. A backtest that
    does is reading the future. With real data the ceiling is unknown, but the same idea applies to
    any score that looks too good: the vendor-leak backtest below lands above this line."""
    if model_metrics["auc"] > ceiling["auc"] + tolerance:
        raise LeakError(f"backtest AUC {model_metrics['auc']} beats the best possible {ceiling['auc']}: features see the future")


def candidates() -> dict:
    return {
        "logistic": make_pipeline(SimpleImputer(strategy="median", add_indicator=True), StandardScaler(),
                                  LogisticRegression(C=1.0, max_iter=3000)),
        "gradient_boosting": HistGradientBoostingClassifier(max_iter=300, learning_rate=0.05, max_leaf_nodes=15,
                                                            l2_regularization=1.0, min_samples_leaf=50, random_state=SEED),
    }


def usable_columns(X: pd.DataFrame) -> tuple[list[str], list[str]]:
    """Columns the model may learn from: not (almost) entirely missing and not constant in training."""
    keep, dropped = [], []
    for c in X.columns:
        s = X[c]
        if s.notna().mean() < 0.01 or s.nunique(dropna=True) <= 1:
            dropped.append(c)
        else:
            keep.append(c)
    return keep, dropped


def metrics(y: np.ndarray, p: np.ndarray) -> dict:
    y, p = np.asarray(y), np.asarray(p, dtype=float)
    base = y.mean()
    top = p >= np.quantile(p, 0.9)
    dec = pd.qcut(pd.Series(p).rank(method="first"), 10, labels=False)
    calib = pd.DataFrame({"d": dec, "p": p, "y": y}).groupby("d").agg(predicted=("p", "mean"), observed=("y", "mean"))
    return {"rows": int(len(y)), "positives": int(y.sum()), "base_rate": round(float(base), 5),
            "auc": round(float(roc_auc_score(y, p)), 4), "average_precision": round(float(average_precision_score(y, p)), 4),
            "lift_top_decile": round(float(y[top].mean() / base), 3) if base else None,
            "brier": round(float(brier_score_loss(y, np.clip(p, 0, 1))), 6),
            "calibration_by_decile": {int(k): {kk: round(float(vv), 5) for kk, vv in v.items()} for k, v in calib.iterrows()}}


@dataclass
class Result:
    chosen: str
    columns: list[str]
    dropped: list[str]
    families: list[str]
    selection: dict
    test: dict
    oracle_test: dict
    baselines_test: dict
    ablations_test: dict
    leakage_demo: dict
    importance: list
    model: object = field(repr=False)


def _prep(df: pd.DataFrame) -> pd.DataFrame:
    return df[~df.bought_before].reset_index(drop=True)


def run(snaps: pd.DataFrame, owner: pd.Series, run_dir: str | Path) -> Result:
    check_no_label_leak(SELECT_TRAIN, SELECT_VAL)
    check_no_label_leak(FINAL_TRAIN, TEST)
    lab = attach_labels(snaps, owner, run_dir)
    lab["as_of"] = pd.to_datetime(lab.as_of).dt.strftime("%Y-%m-%d")
    part = {t: _prep(lab[lab.as_of == t]) for t in ALL_CUTOFFS}
    tr_sel = pd.concat([part[t] for t in SELECT_TRAIN], ignore_index=True)
    check_enough_outcomes({"selection training": tr_sel, "validation": part[SELECT_VAL], "test": part[TEST]})
    families = sorted(tr_sel["bldg_family"].dropna().unique())
    cols, dropped = usable_columns(matrix(tr_sel, families))

    def X(df, drop=()):
        return matrix(df, families)[[c for c in cols if c not in drop]]

    # 1. choose the model on validation, never on test
    sel = {}
    for name, m in candidates().items():
        m.fit(X(tr_sel), tr_sel.label)
        sel[name] = metrics(part[SELECT_VAL].label, m.predict_proba(X(part[SELECT_VAL]))[:, 1])
    chosen = max(sel, key=lambda k: sel[k]["average_precision"])

    # 2. refit on all training snapshots whose outcomes closed before the test date; report once
    tr = pd.concat([part[t] for t in FINAL_TRAIN], ignore_index=True)
    te = part[TEST]
    model = candidates()[chosen].fit(X(tr), tr.label)
    p_test = model.predict_proba(X(te))[:, 1]
    test = metrics(te.label, p_test)
    oracle = metrics(te.label, oracle_score(te, run_dir).to_numpy())
    check_not_above_ceiling(test, oracle)
    for k in ("lift_top_decile", "average_precision", "auc"):
        test[f"share_of_ceiling_{k}"] = round((test[k] - (1 if k == "lift_top_decile" else (0.5 if k == "auc" else test["base_rate"])))
                                              / max(oracle[k] - (1 if k == "lift_top_decile" else (0.5 if k == "auc" else test["base_rate"])), 1e-9), 3)

    baselines = {
        "zip_income_only": metrics(te.label, te.log_zip_income.fillna(te.log_zip_income.median()).to_numpy()),
        "small_building_only": metrics(te.label, te.small_building.astype(float).fillna(0.5).to_numpy()),
        "random": metrics(te.label, np.random.default_rng(SEED).random(len(te))),
    }
    property_cols = {c for c in cols if c.startswith("bldg_") or c in
                     ("unitsres", "log_units", "small_building", "yearbuilt", "property_matched", "log_zip_income")}
    abl = {}
    for name, drop in {"without_property_and_census_enrichment": property_cols,
                       "without_store_behaviour": {"orders_window", "revenue_window", "avg_order_value", "days_since_last_order_in_window"}}.items():
        m = candidates()[chosen].fit(X(tr, drop), tr.label)
        abl[name] = {k: v for k, v in metrics(te.label, m.predict_proba(X(te, drop))[:, 1]).items() if k != "calibration_by_decile"}

    # 3. the leak, measured: vendor attributes as delivered at the end (Dec 2025) pasted onto every snapshot
    latest = part[LATEST].set_index("person_key")
    def leaky(df):
        d = df.copy()
        for c in ("vendor_homeowner", "income_band", "age_years"):
            d[c] = d.person_key.map(latest[c]) if c in latest else np.nan
        return d
    tr_l, te_l = leaky(tr), leaky(te)
    lcols, _ = usable_columns(matrix(tr_l, families))
    ml_ = candidates()[chosen].fit(matrix(tr_l, families)[lcols], tr_l.label)
    leak = {k: v for k, v in metrics(te_l.label, ml_.predict_proba(matrix(te_l, families)[lcols])[:, 1]).items()
            if k != "calibration_by_decile"}
    try:
        check_not_above_ceiling(leak, oracle)
        leak_caught = False
    except LeakError:
        leak_caught = True
    leak_demo = {"backtest_with_leak": leak, "caught_by_ceiling_check": leak_caught, "honest_backtest": {k: test[k] for k in ("auc", "average_precision", "lift_top_decile")},
                 "leaked_columns": ["vendor_homeowner", "income_band", "age_years"]}

    # 4. what-if: the same model with an on-time third-party feed (age, renter status, recent job
    # change, each as of the cutoff). These columns come from the answer key and stand in for what
    # an honest vendor would deliver; the result prices that feed, it is not a model to deploy.
    def enriched(df):
        return df.join(point_in_time_vendor(df, run_dir))
    tr_e, te_e = enriched(tr), enriched(te)
    ecols = cols + ["pit_age", "pit_renter", "pit_job_change_365d", "pit_income_band"]
    # gradient boosting here, not the chosen model: buying peaks in a middle age band, and a linear
    # model cannot draw that without being told where the band is
    me = candidates()["gradient_boosting"].fit(pd.concat([X(tr_e), tr_e[ecols[len(cols):]]], axis=1), tr_e.label)
    what_if = {k: v for k, v in metrics(te_e.label, me.predict_proba(pd.concat([X(te_e), te_e[ecols[len(cols):]]], axis=1))[:, 1]).items()
               if k != "calibration_by_decile"}

    imp = permutation_importance(model, X(te), te.label, scoring="average_precision", n_repeats=3, random_state=SEED)
    importance = sorted(({"feature": c, "drop_in_average_precision": round(float(v), 5)}
                         for c, v in zip(X(te).columns, imp.importances_mean)), key=lambda d: -d["drop_in_average_precision"])[:8]
    abl["with_an_on_time_vendor_feed_what_if"] = what_if
    return Result(chosen, cols, dropped, families, sel, test, oracle, baselines, abl, leak_demo, importance, model)


def point_in_time_vendor(df: pd.DataFrame, run_dir: str | Path) -> pd.DataFrame:
    """Age, renter status, a job change in the last year and household income band, each as of the
    row's cutoff (answer key): what an on-time vendor feed would deliver."""
    run = Path(run_dir)
    ppl = pd.read_parquet(run / "truth" / "people.parquet", columns=["person_id", "dob", "income_band"]).set_index("person_id")
    hist = pd.read_parquet(run / "truth" / "address_history.parquet", columns=["person_id", "valid_from", "owner"])
    jobs = pd.read_parquet(run / "truth" / "events.parquet").query("event_type == 'job_change'")[["person_id", "event_date"]]
    t = pd.to_datetime(df.as_of)
    age = (t - pd.to_datetime(df.person_id.map(ppl.dob))).dt.days / 365.25
    h = hist.assign(valid_from=pd.to_datetime(hist.valid_from)).sort_values("valid_from")
    q = pd.DataFrame({"person_id": df.person_id.to_numpy(), "t": t.to_numpy(), "i": df.index}).dropna(subset=["person_id"]).sort_values("t")
    m = pd.merge_asof(q, h, left_on="t", right_on="valid_from", by="person_id", direction="backward").set_index("i")
    j = jobs.assign(event_date=pd.to_datetime(jobs.event_date)).merge(q, on="person_id")
    recent = j[(j.event_date <= j.t) & (j.event_date > j.t - pd.Timedelta(days=365))].groupby("i").size()
    return pd.DataFrame({"pit_age": age.to_numpy(), "pit_renter": (~m.owner.reindex(df.index).fillna(False).astype(bool)).astype(float).to_numpy(),
                         "pit_job_change_365d": recent.reindex(df.index).fillna(0).clip(upper=1).to_numpy(),
                         "pit_income_band": df.person_id.map(ppl.income_band).astype(float).to_numpy()}, index=df.index)


def save(result: Result, snaps: pd.DataFrame, out_dir: str | Path) -> dict:
    """Write the model and its card. The version is a hash of what went into it."""
    fp = hashlib.sha256(pd.util.hash_pandas_object(snaps.sort_values(["as_of", "person_key"]), index=False).values.tobytes()).hexdigest()[:12]
    cfg = {"chosen": result.chosen, "columns": result.columns, "final_train": FINAL_TRAIN, "horizon_days": HORIZON_DAYS, "seed": SEED}
    version = "hb-" + hashlib.sha256((json.dumps(cfg, sort_keys=True) + fp).encode()).hexdigest()[:10]
    d = Path(out_dir) / version
    d.mkdir(parents=True, exist_ok=True)
    with open(d / "model.pkl", "wb") as f:
        pickle.dump({"model": result.model, "columns": result.columns, "families": result.families, "version": version}, f)
    card = {
        "model": "home_buyer_propensity", "version": version, "predicts": f"household buys a home within {HORIZON_DAYS} days of the scoring date",
        "algorithm": result.chosen, "features": result.columns,
        "features_not_used": {c: "missing or constant in every training snapshot (the vendor feed starts in Nov 2025; email data in Dec 2025)"
                              for c in result.dropped},
        "training_cutoffs": FINAL_TRAIN, "selection": {"train": SELECT_TRAIN, "validate": SELECT_VAL, "results": result.selection},
        "test": {"cutoff": TEST, "model": result.test, "ceiling_from_answer_key": result.oracle_test,
                 "baselines": result.baselines_test, "ablations": result.ablations_test},
        "leakage_demo": result.leakage_demo, "top_features": result.importance,
        "snapshot_fingerprint": fp,
        "limits": ["Synthetic data; scores are evidence about the method, not about real people.",
                   "Trained before third-party attributes existed; retrain once they cover a full label window.",
                   "Labels come from the answer key, standing in for a deed-records feed.",
                   "Person keys come from identity resolution over all records, not as of each cutoff."],
    }
    (d / "card.json").write_text(json.dumps(card, indent=2, default=str))
    return {"version": version, "dir": str(d)}
