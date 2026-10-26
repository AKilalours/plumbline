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
            "published": scored["published"], "scored": scored["scored"], "not_scored": scored["not_scored"],
            "max_input_drift_psi": scored["checks"]["input_drift_psi"]}
