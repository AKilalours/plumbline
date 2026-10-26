"""Home seller propensity: which homeowners sell within 180 days.

A household sells when it owned its home at the cutoff and moves out within the horizon. Ownership at
the cutoff and the move come from the answer key, standing in for deed records (in New York, ACRIS
records sales); they are labels and population, never features. Features are the same point-in-time
snapshots as the buyer model, with the same time split and the same leak checks.

In this world an owner sells for two reasons: they buy another home (driven by income, age and job
changes, so learnable), or they move for reasons the world does not model (pure noise). The ceiling
below says how much of selling is predictable at all, before anyone reads the model's score.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from .features import matrix
from .labels import HORIZON_DAYS
from .train import (FINAL_TRAIN, SELECT_TRAIN, SELECT_VAL, TEST, candidates, check_no_label_leak,
                    check_not_above_ceiling, metrics, usable_columns)


def _cfg(run: Path) -> dict:
    man = json.loads((run / "manifest.json").read_text())
    return man.get("config", man)


def attach(snaps: pd.DataFrame, owner: pd.Series, run_dir, horizon: int = HORIZON_DAYS) -> pd.DataFrame:
    """Adds owns_at_cutoff, moved_before (already gone) and label (sells within the horizon)."""
    run = Path(run_dir)
    hist = pd.read_parquet(run / "truth" / "address_history.parquet", columns=["person_id", "valid_from", "owner"])
    # Both sides of merge_asof must share one time resolution. Parquet dates arrive as microseconds and
    # parsed strings as nanoseconds on pandas 2 (both microseconds on pandas 3), so pin both to ns.
    hist = hist.assign(valid_from=pd.to_datetime(hist.valid_from).astype("datetime64[ns]")).sort_values("valid_from")
    out = snaps.copy()
    out["person_id"] = out.person_key.map(owner)
    out["t"] = pd.to_datetime(out.as_of).astype("datetime64[ns]")
    q = out[["person_id", "t"]].reset_index().dropna(subset=["person_id"]).sort_values("t")
    cur = pd.merge_asof(q, hist, left_on="t", right_on="valid_from", by="person_id", direction="backward").set_index("index")
    nxt = pd.merge_asof(q.assign(t1=q.t + pd.Timedelta(seconds=1)), hist.rename(columns={"valid_from": "next_move"}),
                        left_on="t1", right_on="next_move", by="person_id", direction="forward").set_index("index")
    out["owns_at_cutoff"] = cur.owner.reindex(out.index).fillna(False).astype(bool)
    nm = nxt.next_move.reindex(out.index)
    out["label"] = (out.owns_at_cutoff & (nm > out.t) & (nm <= out.t + pd.Timedelta(days=horizon))).fillna(False).astype(int)
    first_move = hist.groupby("person_id").valid_from.agg(lambda s: s.iloc[1] if len(s) > 1 else pd.NaT)
    out["moved_before"] = (out.person_id.map(first_move) <= out.t).fillna(False).astype(bool)
    return out


def oracle(df: pd.DataFrame, run_dir, horizon: int = HORIZON_DAYS) -> pd.Series:
    """The true chance the household's one event (a purchase, or else a move) falls inside the window,
    given it has not happened by the cutoff: the best any model could score."""
    run = Path(run_dir)
    cfg = _cfg(run)
    start, end = pd.Timestamp(cfg["window_start"]), pd.Timestamp(cfg["window_end"])
    m = float(cfg["move_rate"])
    ppl = pd.read_parquet(run / "truth" / "people.parquet", columns=["person_id", "household_id"])
    hh = pd.read_parquet(run / "truth" / "households.parquet", columns=["household_id", "head_person_id", "purchase_probability"])
    jobs = pd.read_parquet(run / "truth" / "events.parquet").query("event_type == 'job_change'")
    jobs = jobs.assign(event_date=pd.to_datetime(jobs.event_date)).groupby("person_id").event_date.min()
    hh["lo"] = (hh.head_person_id.map(jobs) + pd.Timedelta(days=30)).fillna(start + pd.Timedelta(days=30)).clip(upper=end)
    x = df[["person_id", "as_of"]].merge(ppl, on="person_id", how="left").merge(hh, on="household_id", how="left")
    t, t1 = pd.to_datetime(x.as_of), pd.to_datetime(x.as_of) + pd.Timedelta(days=horizon)

    def frac(lo, a, b):
        span = (end - lo).dt.days.clip(lower=1)
        # pandas methods, not np.minimum/np.maximum: pandas 2 refuses numpy ufuncs on a datetime
        # Series against a Timestamp
        hi, low = b.clip(upper=end), a.where(a >= lo, lo)
        return ((hi - low).dt.days.clip(lower=0)) / span
    p = x.purchase_probability.fillna(0)
    mlo = pd.Series(start + pd.Timedelta(days=30), index=x.index)
    inside = p * frac(x.lo, t, t1) + m * (1 - p) * frac(mlo, t, t1)
    before = p * frac(x.lo, pd.Series(start, index=x.index), t) + m * (1 - p) * frac(mlo, pd.Series(start, index=x.index), t)
    return pd.Series((inside / (1 - before).clip(lower=1e-9)).to_numpy(), index=df.index)


def run(snaps: pd.DataFrame, owner: pd.Series, run_dir) -> dict:
    check_no_label_leak(SELECT_TRAIN, SELECT_VAL)
    check_no_label_leak(FINAL_TRAIN, TEST)
    lab = attach(snaps, owner, run_dir)
    lab["as_of"] = pd.to_datetime(lab.as_of).dt.strftime("%Y-%m-%d")
    # the population: homeowners at the cutoff who have not already moved
    part = {t: lab[(lab.as_of == t) & lab.owns_at_cutoff & ~lab.moved_before].reset_index(drop=True)
            for t in sorted(set(SELECT_TRAIN + [SELECT_VAL] + FINAL_TRAIN + [TEST]))}
    tr_sel = pd.concat([part[t] for t in SELECT_TRAIN], ignore_index=True)
    families = sorted(tr_sel.bldg_family.dropna().unique())
    cols, dropped = usable_columns(matrix(tr_sel, families))
    X = lambda d: matrix(d, families)[cols]
    sel = {}
    for name, mdl in candidates().items():
        mdl.fit(X(tr_sel), tr_sel.label)
        sel[name] = {k: v for k, v in metrics(part[SELECT_VAL].label, mdl.predict_proba(X(part[SELECT_VAL]))[:, 1]).items()
                     if k != "calibration_by_decile"}
    chosen = max(sel, key=lambda k: sel[k]["average_precision"])
    tr = pd.concat([part[t] for t in FINAL_TRAIN], ignore_index=True)
    te = part[TEST]
    model = candidates()[chosen].fit(X(tr), tr.label)
    test = {k: v for k, v in metrics(te.label, model.predict_proba(X(te))[:, 1]).items() if k != "calibration_by_decile"}
    ceiling = {k: v for k, v in metrics(te.label, oracle(te, run_dir).to_numpy()).items() if k != "calibration_by_decile"}
    check_not_above_ceiling(test, ceiling)
    rng = np.random.default_rng(7)
    return {"population": "homeowners at the cutoff who had not moved yet", "algorithm": chosen,
            "selection_on_validation": sel, "train_rows": int(len(tr)), "train_sellers": int(tr.label.sum()),
            "test": test, "ceiling_from_answer_key": ceiling,
            "share_of_ceiling_auc": round((test["auc"] - 0.5) / max(ceiling["auc"] - 0.5, 1e-9), 3),
            "random": {k: v for k, v in metrics(te.label, rng.random(len(te))).items() if k != "calibration_by_decile"},
            "features_not_used": dropped, "_model": model, "_cols": cols, "_families": families}


MIN_VALIDATION_AUC = 0.55     # clearly better than chance on a period the model did not train on
MIN_TEST_LIFT = 1.2           # its top decile must hold noticeably more sellers than average


def gates(r: dict) -> list[dict]:
    val = r["selection_on_validation"][r["algorithm"]]
    return [
        {"name": "beats_chance_on_validation", "value": val["auc"], "passed": bool(val["auc"] >= MIN_VALIDATION_AUC),
         "rule": f"validation AUC >= {MIN_VALIDATION_AUC}"},
        {"name": "top_decile_lift", "value": r["test"]["lift_top_decile"], "passed": bool((r["test"]["lift_top_decile"] or 0) >= MIN_TEST_LIFT),
         "rule": f"test lift in the top decile >= {MIN_TEST_LIFT}"},
        {"name": "ceiling_leaves_room", "value": r["ceiling_from_answer_key"]["auc"],
         "passed": bool(r["ceiling_from_answer_key"]["auc"] >= 0.6),
         "rule": "the best possible AUC is at least 0.6; below that, selling is mostly unpredictable from any data"},
    ]


def assess_and_record(warehouse, snaps: pd.DataFrame, owner: pd.Series, run_dir, min_test_sales: int = 20) -> dict:
    """Train, evaluate, and publish scores only if every gate passes. A model that fails is recorded
    with its reasons, so nobody retrains it next month expecting a different answer."""
    import duckdb
    lab = attach(snaps, owner, run_dir)
    test_pos = int(lab[(pd.to_datetime(lab.as_of).dt.strftime("%Y-%m-%d") == TEST) & lab.owns_at_cutoff].label.sum())
    if test_pos < min_test_sales:
        return {"skipped": f"only {test_pos} sales in the test window; a larger world is needed"}
    r = run(snaps, owner, run_dir)
    g = gates(r)
    ok = all(c["passed"] for c in g)
    report = {k: v for k, v in r.items() if not k.startswith("_")}
    with duckdb.connect(str(warehouse)) as con:
        con.execute("CREATE SCHEMA IF NOT EXISTS ml")
        con.execute("CREATE TABLE IF NOT EXISTS ml.model_runs_seller (run_at TIMESTAMP, published BOOLEAN, gates JSON, report JSON)")
        con.execute("INSERT INTO ml.model_runs_seller VALUES (now(), ?, ?, ?)", [ok, json.dumps(g), json.dumps(report, default=str)])
        if not ok:
            con.execute("DROP TABLE IF EXISTS ml.home_seller_scores")      # never leave old scores looking current
    return {"published": ok, "test_auc": r["test"]["auc"], "ceiling_auc": r["ceiling_from_answer_key"]["auc"],
            "test_lift_top_decile": r["test"]["lift_top_decile"],
            "failed_gates": [c["name"] for c in g if not c["passed"]]}
