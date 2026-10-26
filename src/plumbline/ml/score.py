"""Score the latest snapshot with a saved model, behind the guardrails."""
from __future__ import annotations

import pickle
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from .features import matrix
from .guardrails import check_batch, publish
from .train import FINAL_TRAIN


def score_latest(warehouse: str | Path, model_dir: str | Path, as_of: str) -> dict:
    with open(Path(model_dir) / "model.pkl", "rb") as f:
        art = pickle.load(f)
    with duckdb.connect(str(warehouse), read_only=True) as con:
        snaps = con.execute("SELECT * FROM ml.feature_snapshots").df()
    snaps["as_of"] = pd.to_datetime(snaps.as_of).dt.strftime("%Y-%m-%d")
    train = snaps[snaps.as_of.isin(FINAL_TRAIN)]
    now = snaps[snaps.as_of == as_of]
    if now.empty:
        raise ValueError(f"no feature snapshot for {as_of}; build it first")
    # The model learned only from people the brand knew first-hand (no vendor data existed yet).
    # Anyone known only through the vendor is outside what it has seen: report them, do not guess.
    in_scope = now[now.first_party.fillna(False).astype(bool)]
    out_scope = now[~now.person_key.isin(in_scope.person_key)][["person_key"]].assign(
        reason="known only from third-party data; the model was trained before any existed")
    Xt = matrix(train, art["families"])[art["columns"]]
    Xs = matrix(in_scope, art["families"])[art["columns"]]
    p = art["model"].predict_proba(Xs)[:, 1]
    base = float(train_labels_base_rate(warehouse))
    checks = check_batch(Xt, Xs, p, base)
    scores = pd.DataFrame({"person_key": in_scope.person_key.to_numpy(), "as_of": as_of, "score": p,
                           "decile": (pd.Series(p).rank(pct=True, method="first") * 10).clip(upper=9.999).astype(int).to_numpy() + 1,
                           "model_version": art["version"]})
    with duckdb.connect(str(warehouse)) as con:
        res = publish(con, scores, art["version"], as_of, checks, out_scope)
    return {**res, "checks": {c["name"]: c["value"] for c in checks}}


def train_labels_base_rate(warehouse) -> float:
    with duckdb.connect(str(warehouse), read_only=True) as con:
        return con.execute("SELECT base_rate FROM ml.training_summary").fetchone()[0]
