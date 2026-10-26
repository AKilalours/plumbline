"""Module C end to end: snapshots, training, model card, scoring behind guardrails."""
from __future__ import annotations

import json
from pathlib import Path

import duckdb

from .features import build_snapshots
from .labels import person_owner
from .score import score_latest
from .train import ALL_CUTOFFS, LATEST, run as train_run, save


def train_and_score(warehouse: str | Path, run_dir: str | Path, models_dir: str | Path = "data/models/home_buyer") -> dict:
    snaps = build_snapshots(warehouse, ALL_CUTOFFS)
    with duckdb.connect(str(warehouse), read_only=True) as con:
        owner = person_owner(con, Path(run_dir))
    r = train_run(snaps, owner, run_dir)
    saved = save(r, snaps, models_dir)
    card = json.loads((Path(saved["dir"]) / "card.json").read_text())
    with duckdb.connect(str(warehouse)) as con:
        con.execute("CREATE SCHEMA IF NOT EXISTS ml")
        con.execute("CREATE OR REPLACE TABLE ml.training_summary AS SELECT ? AS model_version, ? AS base_rate, ? AS card",
                    [saved["version"], card["test"]["model"]["base_rate"], json.dumps(card, default=str)])
    scored = score_latest(warehouse, saved["dir"], LATEST)
    t, o = r.test, r.oracle_test
    return {"model_version": saved["version"], "algorithm": r.chosen,
            "test_auc": t["auc"], "test_lift_top_decile": t["lift_top_decile"],
            "ceiling_auc": o["auc"], "ceiling_lift_top_decile": o["lift_top_decile"],
            "zip_income_baseline_auc": r.baselines_test["zip_income_only"]["auc"],
            "leaky_backtest_auc": r.leakage_demo["backtest_with_leak"]["auc"],
            "leak_caught": r.leakage_demo["caught_by_ceiling_check"],
            "on_time_vendor_feed_auc": r.ablations_test["with_an_on_time_vendor_feed_what_if"]["auc"],
            "on_time_vendor_feed_single_fit_auc_range": [r.ablations_test["with_an_on_time_vendor_feed_what_if"]["seed_spread"]["member_auc_min"],
                                                         r.ablations_test["with_an_on_time_vendor_feed_what_if"]["seed_spread"]["member_auc_max"]],
            "published": scored["published"], "scored": scored["scored"], "not_scored": scored["not_scored"],
            "max_input_drift_psi": scored["checks"]["input_drift_psi"]}


def other_models(warehouse: str | Path, run_dir: str | Path) -> dict:
    """Income estimates for people the vendor does not cover, and the home-seller assessment. Both
    reuse the snapshots the buyer model built."""
    from .income import IncomeGateFailed, publish as publish_income, run as run_income
    from .seller import assess_and_record
    out = {}
    try:
        inc = run_income(warehouse, run_dir)
        res = publish_income(warehouse, inc)
        out["income"] = {"published": True, "people_estimated": res["people_estimated"], "algorithm": inc["algorithm"],
                         "uncovered_within_one_band": inc["uncovered_people_vs_true_band"]["model"]["within_one_band"],
                         "zip_only_within_one_band": inc["uncovered_people_vs_true_band"]["zip_income_only"]["within_one_band"],
                         "vendor_label_within_one_band": inc["test_vs_true_band"]["vendor_label_itself"]["within_one_band"]}
    except IncomeGateFailed as e:
        out["income"] = {"published": False, "reason": str(e)}
    with duckdb.connect(str(warehouse), read_only=True) as con:
        snaps = con.execute("SELECT * FROM ml.feature_snapshots").df()
        owner = person_owner(con, Path(run_dir))
    out["home_seller"] = assess_and_record(warehouse, snaps, owner, run_dir)
    return out
