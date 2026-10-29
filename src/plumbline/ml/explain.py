"""Reason codes: why a person got their home-buyer score, in terms an agent can repeat.

The champion is logistic regression (chosen on validation), so a score decomposes exactly: the
log-odds is the intercept plus one term per input, each term the weight times the person's
standardised value. A term is that input's push away from an average person. The top terms are the
reasons. Because the decomposition is exact, the reasons are checked, not trusted: for every person
they must add back up to the model's own score (`faithfulness`), which an approximate explainer
fitted after the fact cannot promise.

    python -m plumbline.ml.explain WAREHOUSE RUN_DIR      # writes docs/results/reason_codes.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

LABELS = {
    "log_zip_income": "ZIP income", "unitsres": "units in the building", "log_units": "building size",
    "yearbuilt": "year the building was built", "orders_window": "orders in the last 90 days",
    "revenue_window": "spend in the last 90 days", "avg_order_value": "average order value",
    "days_since_last_order_in_window": "days since last order", "opens_90d": "email opens",
    "clicks_90d": "email clicks", "moves_window": "recent moves", "work_emails_seen": "work emails seen",
    "income_band": "income band", "age_years": "age", "small_building": "lives in a small building",
    "has_unit": "lives in an apartment unit", "property_matched": "property record found",
    "new_job_window": "new job", "vendor_homeowner": "vendor says homeowner",
}


def label(feature: str) -> str:
    if feature.startswith("missingindicator_"):
        return f"no {LABELS.get(feature.removeprefix('missingindicator_'), feature)} on file"
    if feature.startswith("bldg_"):
        return f"building class {feature.removeprefix('bldg_')}"
    return LABELS.get(feature, feature.replace("_", " "))


def contributions(model, X: pd.DataFrame) -> tuple[pd.DataFrame, np.ndarray]:
    """-> (per-person contribution of every input to the log-odds, intercept). Logistic pipelines only."""
    imputer, scaler, lr = model.steps[0][1], model.steps[1][1], model.steps[-1][1]
    Z = scaler.transform(imputer.transform(X))
    names = imputer.get_feature_names_out(X.columns)
    C = pd.DataFrame(Z * lr.coef_[0], columns=names, index=X.index)
    return C, np.full(len(X), lr.intercept_[0])


def reasons(C: pd.DataFrame, k: int = 3) -> list[list[tuple[str, float]]]:
    out = []
    for _, row in C.iterrows():
        top = row.reindex(row.abs().sort_values(ascending=False).index)[:k]
        out.append([(label(n), round(float(v), 3)) for n, v in top.items() if abs(v) > 1e-9])
    return out


def faithfulness(model, X: pd.DataFrame) -> float:
    """Largest gap, over all people, between the model's probability and the one rebuilt from reasons."""
    C, b = contributions(model, X)
    rebuilt = 1 / (1 + np.exp(-(C.sum(axis=1).to_numpy() + b)))
    return float(np.max(np.abs(rebuilt - model.predict_proba(X)[:, 1])))


def report(warehouse, run_dir, examples: int = 5) -> dict:
    """Fit the production recipe (logistic, as chosen on validation at 100,000 people; the release gate
    re-checks that choice), refit on the training snapshots, explain the
    held-out test snapshot, and keep a few examples from the top decile."""
    import duckdb

    from .features import build_snapshots, matrix
    from .labels import attach_labels, person_owner
    from .train import ALL_CUTOFFS, FINAL_TRAIN, SELECT_TRAIN, TEST, _prep, candidates, usable_columns
    snaps = build_snapshots(warehouse, ALL_CUTOFFS)
    with duckdb.connect(str(warehouse), read_only=True) as con:
        owner = person_owner(con, Path(run_dir))
    lab = attach_labels(snaps, owner, run_dir)
    lab["as_of"] = pd.to_datetime(lab.as_of).dt.strftime("%Y-%m-%d")
    part = {t: _prep(lab[lab.as_of == t]) for t in ALL_CUTOFFS}
    tr_sel = pd.concat([part[t] for t in SELECT_TRAIN], ignore_index=True)
    tr = pd.concat([part[t] for t in FINAL_TRAIN], ignore_index=True)
    te = part[TEST]
    families = sorted(tr_sel["bldg_family"].dropna().unique())
    cols, _ = usable_columns(matrix(tr_sel, families))
    X = lambda df: matrix(df, families)[cols]
    model = candidates()["logistic"].fit(X(tr), tr.label)
    Xt = X(te)
    p = model.predict_proba(Xt)[:, 1]
    C, _ = contributions(model, Xt)
    top = np.argsort(-p)[: max(1, len(p) // 10)]
    first = pd.Series([label(C.iloc[i].abs().idxmax()) for i in top]).value_counts()
    ex = []
    for i in top[:: max(1, len(top) // examples)][:examples]:
        ex.append({"person_key": te.person_key.iloc[i], "score": round(float(p[i]), 4), "bought": bool(te.label.iloc[i]),
                   "reasons": reasons(C.iloc[[i]])[0]})
    return {"city": "seed 7, 100,000 people", "snapshot": TEST, "people": int(len(te)),
            "base_rate": round(float(te.label.mean()), 5), "faithfulness_max_gap": faithfulness(model, Xt),
            "top_decile_first_reason": {k: int(v) for k, v in first.head(6).items()}, "examples": ex}


if __name__ == "__main__":
    r = report(sys.argv[1], sys.argv[2])
    out = Path(__file__).resolve().parents[3] / "docs" / "results" / "reason_codes.json"
    out.write_text(json.dumps(r, indent=1))
    print(json.dumps({k: v for k, v in r.items() if k != "examples"}, indent=1))
