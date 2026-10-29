"""Experiment: a learned second stage that re-scores Splink's candidate pairs. Measured, not shipped.

    python -m plumbline.identity.second_stage TRAIN_WORLD TEST_WORLD

Trained on the answer key of one city, its threshold chosen there by out-of-fold predictions,
then scored once on a different city it never saw. Features: Splink's match weight, string
similarity per field, equality flags with missing kept apart from different, and how common the
last name is and how many records share the home (namesake risk). Result in
docs/results/identity_second_stage.json; why it does not help is in the error breakdown there.
"""
from __future__ import annotations

import itertools
import json
import shutil
import sys
import tempfile
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

FEATURES_SQL = """
SELECT p.unique_id_l, p.unique_id_r, p.match_probability, p.match_weight,
  coalesce(jaro_winkler_similarity(l.first_name, r.first_name), -1) jw_first,
  coalesce(jaro_winkler_similarity(l.last_name, r.last_name), -1) jw_last,
  CASE WHEN l.email IS NULL OR r.email IS NULL THEN -1 WHEN l.email = r.email THEN 1 ELSE 0 END email_eq,
  coalesce(jaro_winkler_similarity(l.email_user, r.email_user), -1) jw_email_user,
  CASE WHEN l.phone IS NULL OR r.phone IS NULL THEN -1 WHEN l.phone = r.phone THEN 1 ELSE 0 END phone_eq,
  CASE WHEN l.house IS NULL OR r.house IS NULL THEN -1 WHEN l.house = r.house THEN 1 ELSE 0 END house_eq,
  coalesce(jaro_winkler_similarity(l.street, r.street), -1) jw_street,
  CASE WHEN l.unit IS NULL OR r.unit IS NULL THEN -1 WHEN l.unit = r.unit THEN 1 ELSE 0 END unit_eq,
  CASE WHEN l.zip IS NULL OR r.zip IS NULL THEN -1 WHEN l.zip = r.zip THEN 1 ELSE 0 END zip_eq,
  CASE WHEN l.dob IS NULL OR r.dob IS NULL THEN -1 WHEN l.dob = r.dob THEN 1 ELSE 0 END dob_eq,
  CASE WHEN l.birth_year IS NULL OR r.birth_year IS NULL THEN -1 WHEN l.birth_year = r.birth_year THEN 1 ELSE 0 END year_eq,
  CASE WHEN l.first_group IS NULL OR r.first_group IS NULL THEN -1 WHEN l.first_group = r.first_group THEN 1 ELSE 0 END first_group_eq,
  ln(1 + coalesce(nl.n, 0)) last_name_freq,
  ln(1 + coalesce(nh.n, 0)) same_home_records,
  (l.source = r.source)::INT same_source
FROM pairs p JOIN recs l ON l.unique_id = p.unique_id_l JOIN recs r ON r.unique_id = p.unique_id_r
LEFT JOIN (SELECT last_name, count(*) n FROM recs GROUP BY 1) nl ON nl.last_name = l.last_name
LEFT JOIN (SELECT house, street, zip, count(*) n FROM recs GROUP BY ALL) nh
       ON nh.house = l.house AND nh.street = l.street AND nh.zip = l.zip
"""
FEATURES = ["match_probability", "match_weight", "jw_first", "jw_last", "email_eq", "jw_email_user", "phone_eq",
            "house_eq", "jw_street", "unit_eq", "zip_eq", "dob_eq", "year_eq", "first_group_eq", "last_name_freq",
            "same_home_records", "same_source"]


def features(recs: pd.DataFrame, pairs: pd.DataFrame) -> pd.DataFrame:
    con = duckdb.connect()
    con.register("recs", recs)
    con.register("pairs", pairs)
    return con.execute(FEATURES_SQL).df()


def load_world(root: Path):
    from .evaluate import truth_for
    from .match import train_and_predict
    from .prepare import build_records
    tmp = Path(tempfile.mkdtemp()) / "w.duckdb"
    shutil.copy(root / "warehouse.duckdb", tmp)          # build_records may create views: never on the original
    with duckdb.connect(str(tmp)) as con:
        recs, po = build_records(con)
    pairs, clusters, _ = train_and_predict(recs)
    truth = truth_for(recs, po, next(root.glob("seed*")))
    f = features(recs, pairs)
    tl, tr = f.unique_id_l.map(truth), f.unique_id_r.map(truth)
    f["y"] = ((tl == tr) & tl.notna()).astype(int)
    return recs, pairs, clusters, truth, f


def scores(recs, pairs, truth, threshold) -> dict:
    from .cluster import constrained_clusters
    from .evaluate import pairwise_scores
    cl = constrained_clusters(recs, pairs, threshold).set_index("unique_id")["cluster_id"]
    t = truth.reindex(cl.index)
    m = t.notna()
    return pairwise_scores(cl[m], t[m])


def error_breakdown(recs, pairs, clusters, truth, threshold) -> dict:
    """Where the baseline's remaining errors come from: what a pair re-scorer can and cannot reach."""
    cl = clusters.set_index("unique_id")["cluster_id"]
    df = pd.DataFrame({"c": cl, "p": truth.reindex(cl.index)}).dropna()
    prob = {}
    for a, b, p in zip(pairs.unique_id_l, pairs.unique_id_r, pairs.match_probability):
        prob[(a, b)] = prob[(b, a)] = p
    out = {"never_compared": 0, "scored_below_threshold": 0, "refused_by_cluster_rules": 0, "wrongly_joined": 0}
    for _, g in df.groupby("p"):
        for a, b in itertools.combinations(g.index, 2):
            if df.c[a] != df.c[b]:
                key = "never_compared" if (a, b) not in prob else ("scored_below_threshold" if prob[(a, b)] < threshold else "refused_by_cluster_rules")
                out[key] += 1
    for _, g in df.groupby("c"):
        out["wrongly_joined"] += sum(df.p[a] != df.p[b] for a, b in itertools.combinations(g.index, 2))
    return {k: int(v) for k, v in out.items()}


def evaluate(train_root: Path, test_root: Path) -> dict:
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.model_selection import GroupKFold

    from .match import MATCH_THRESHOLD
    A, B = load_world(train_root), load_world(test_root)
    fa, fb = A[4], B[4]
    make = lambda: HistGradientBoostingClassifier(max_iter=300, learning_rate=0.05, random_state=0)
    oof = np.zeros(len(fa))
    for tr, te in GroupKFold(5).split(fa, groups=fa.unique_id_l):
        oof[te] = make().fit(fa[FEATURES].iloc[tr], fa.y.iloc[tr]).predict_proba(fa[FEATURES].iloc[te])[:, 1]
    grid = {}
    for th in (0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9):
        grid[th] = scores(A[0], fa[["unique_id_l", "unique_id_r"]].assign(match_probability=oof), A[3], th)["f1"]
    th = max(grid, key=lambda t: (grid[t], t))
    model = make().fit(fa[FEATURES], fa.y)
    pb = fb[["unique_id_l", "unique_id_r"]].assign(match_probability=model.predict_proba(fb[FEATURES])[:, 1])
    base = scores(B[0], B[1], B[3], MATCH_THRESHOLD)
    second = scores(B[0], pb, B[3], th)
    keep = ("precision", "recall", "f1", "people_resolved_exactly")
    return {"train_world": str(train_root), "test_world": str(test_root), "threshold_chosen_on_train": th,
            "train_oof_f1_by_threshold": grid, "test_splink": {k: base[k] for k in keep},
            "test_second_stage": {k: second[k] for k in keep}, "test_true_pairs": base["true_pairs"],
            "splink_errors_on_test": error_breakdown(B[0], B[1], B[2], B[3], MATCH_THRESHOLD),
            "shipped": bool(second["f1"] > base["f1"] + 0.001)}


if __name__ == "__main__":
    r = evaluate(Path(sys.argv[1]), Path(sys.argv[2]))
    out = Path("docs/results/identity_second_stage.json")
    r["train_world"], r["test_world"] = Path(sys.argv[1]).name, Path(sys.argv[2]).name   # no local paths in the repo
    out.write_text(json.dumps(r, indent=1))
    print(json.dumps(r, indent=1))
