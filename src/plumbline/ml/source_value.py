"""Should we buy this data? What each source is worth, measured, before anyone pays for it.

A data company buys sources. The question is not "is it more data" but "what does it change": for
each source, take its inputs out of the home-buyer model, refit, and measure on the held-out test
snapshot what is lost (AUC with a paired-bootstrap 95% interval, buyers found in the top decile).
Then check the source's own claims against the answer key: a vendor's homeowner flag and income band
can be convenient and wrong. Identity coverage says how many people only that source reaches.

    python -m plumbline.ml.source_value WAREHOUSE RUN_DIR     # writes docs/results/source_value.json

The answer key scores; it never trains. The value of a buyer and the price of a record are the
reader's assumptions (the app has sliders for them); this file reports only what was measured.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

# Which model inputs each source supplies (dbt/models/marts/fct_person_features.sql).
GROUPS = {
    "property": {"label": "Property and census (NYC PLUTO, ACS)", "cost": "free public data",
                 "cols": {"unitsres", "log_units", "small_building", "yearbuilt", "property_matched", "log_zip_income"},
                 "prefix": "bldg_"},
    "vendor": {"label": "Data vendor append", "cost": "paid per record",
               "cols": {"income_band", "vendor_homeowner", "age_years", "work_emails_seen", "new_job_window"}},
    "store": {"label": "Online store orders", "cost": "first party",
              "cols": {"orders_window", "revenue_window", "avg_order_value", "days_since_last_order_in_window"}},
    "email": {"label": "Email engagement", "cost": "first party", "cols": {"opens_90d", "clicks_90d"}},
}


def _inputs(cols: list[str], group: str) -> set[str]:
    g = GROUPS[group]
    return {c for c in cols if c in g["cols"] or (g.get("prefix") and c.startswith(g["prefix"]))}


def model_value(warehouse, run_dir, resamples: int = 1000) -> dict:
    """Refit the production recipe without each source; score every variant on the same test rows."""
    import duckdb

    from .features import build_snapshots, matrix
    from .labels import attach_labels, person_owner
    from .release import paired_auc_diff, top_decile_hits
    from .train import ALL_CUTOFFS, FINAL_TRAIN, SELECT_TRAIN, SELECT_VAL, TEST, _prep, candidates, usable_columns
    from sklearn.metrics import average_precision_score, roc_auc_score

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
    X = lambda df, drop=frozenset(): matrix(df, families)[[c for c in cols if c not in drop]]
    val = {n: average_precision_score(part[SELECT_VAL].label, m.fit(X(tr_sel), tr_sel.label).predict_proba(X(part[SELECT_VAL]))[:, 1])
           for n, m in candidates().items()}
    chosen = max(val, key=val.get)                      # the same rule production uses (ml/train.py)
    fit = lambda drop=frozenset(): candidates()[chosen].fit(X(tr, drop), tr.label).predict_proba(X(te, drop))[:, 1]
    y = te.label.to_numpy()
    base = fit()
    out = {"champion": chosen, "test_people": int(len(y)), "test_buyers": int(y.sum()),
           "auc": round(float(roc_auc_score(y, base)), 4), "top_decile_buyers": top_decile_hits(y, base), "without": {}}
    for g in GROUPS:
        drop = frozenset(_inputs(cols, g))
        p = fit(drop)
        d, lo, hi = paired_auc_diff(y, base, p, n=resamples)
        out["without"][g] = {"inputs": sorted(drop), "auc": round(float(roc_auc_score(y, p)), 4),
                             "auc_change": round(d, 4), "ci95": [round(lo, 4), round(hi, 4)],
                             "top_decile_buyers": top_decile_hits(y, p)}
    return out


def vendor_truth(warehouse, run_dir) -> dict:
    """The vendor's homeowner flag and income band, checked against the answer key on matched people."""
    import duckdb

    from .labels import person_owner
    with duckdb.connect(str(warehouse), read_only=True) as con:
        owner = person_owner(con, Path(run_dir))
        dp = con.execute("SELECT person_key, homeowner, income_band FROM products.dim_person").df()
    truth = pd.read_parquet(Path(run_dir) / "truth" / "people.parquet", columns=["person_id", "owner", "income_band"])
    m = dp.assign(person_id=dp.person_key.map(owner)).merge(truth, on="person_id", suffixes=("", "_true"))
    h = m.dropna(subset=["homeowner"])
    i = m.dropna(subset=["income_band"])
    said_owner = h[h.homeowner.astype(bool)]
    return {"people": int(len(m)),
            "homeowner": {"covered": int(len(h)), "share_covered": round(len(h) / len(m), 4),
                          "accuracy": round(float((h.homeowner.astype(bool) == h.owner.astype(bool)).mean()), 4),
                          "said_owner": int(len(said_owner)),
                          "said_owner_but_rents": int((~said_owner.owner.astype(bool)).sum())},
            "income_band": {"covered": int(len(i)), "share_covered": round(len(i) / len(m), 4),
                            "exact": round(float((i.income_band.astype(int) == i.income_band_true.astype(int)).mean()), 4),
                            "within_one": round(float(((i.income_band.astype(int) - i.income_band_true.astype(int)).abs() <= 1).mean()), 4)}}


def coverage(warehouse) -> dict:
    """How many resolved people each source reaches, and how many only that source reaches."""
    import duckdb
    with duckdb.connect(str(warehouse), read_only=True) as con:
        df = con.execute("SELECT person_key, list(DISTINCT source) AS s FROM identity_records GROUP BY 1").df()
    total = len(df)
    out = {"people": total}
    for src in ("crm", "vendor", "store"):
        has = df.s.map(lambda s: src in s)
        only = df.s.map(lambda s: list(s) == [src])
        out[src] = {"reaches": int(has.sum()), "share": round(float(has.mean()), 4), "only_source": int(only.sum())}
    return out


def report(warehouse, run_dir) -> dict:
    return {"city": f"seed 7, {Path(run_dir).name.split('_n')[-1]} people", "groups": {g: {k: v for k, v in GROUPS[g].items() if k in ("label", "cost")} for g in GROUPS},
            "model": model_value(warehouse, run_dir), "vendor_vs_answer_key": vendor_truth(warehouse, run_dir),
            "identity_coverage": coverage(warehouse)}


def breakeven_price(buyers_gained_per_10k: float, value_per_buyer: float, records: int = 10_000) -> float:
    """The most a record is worth: buyers it adds to a campaign times what a buyer is worth, per record."""
    return max(0.0, buyers_gained_per_10k * value_per_buyer / records)


if __name__ == "__main__":
    r = report(sys.argv[1], sys.argv[2])
    out = Path(__file__).resolve().parents[3] / "docs" / "results" / "source_value.json"
    out.write_text(json.dumps(r, indent=1))
    print(json.dumps(r, indent=1))
