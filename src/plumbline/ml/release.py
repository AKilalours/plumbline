"""Should a new model replace the one in production? A release gate, not a leaderboard.

Both models score the same held-out people. The challenger is released only if every gate passes:

  not_worse_auc     paired bootstrap of AUC(challenger) - AUC(champion); the lower 95% bound must be
                    above -margin. "Higher on this sample" is not enough when the interval spans zero.
  calibrated        expected calibration error <= 0.05. Agents read scores as probabilities; a model
                    that ranks well but says 30% when it means 3% would pass an AUC-only gate.
  top_decile_kept   the challenger's top decile catches at least 95% of the buyers the champion's does.
  slices_hold       AUC within 0.03 of the champion on every named slice (first-party people, vendor-only).

The gate records what it decided and why. A challenger that is only "not worse" is released only
when it brings something else (stated in the reason); the default is to keep what is running.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from sklearn.metrics import roc_auc_score

MARGIN, ECE_MAX, TOP_KEEP, SLICE_DROP = 0.01, 0.05, 0.95, 0.03


def ece(y: np.ndarray, p: np.ndarray, bins: int = 10) -> float:
    """Expected calibration error over equal-count bins: |mean score - observed rate|, weighted."""
    order = np.argsort(p, kind="mergesort")
    parts = np.array_split(order, bins)
    return float(sum(len(ix) * abs(p[ix].mean() - y[ix].mean()) for ix in parts if len(ix)) / len(y))


def top_decile_hits(y: np.ndarray, p: np.ndarray) -> int:
    k = max(1, len(p) // 10)
    return int(y[np.argsort(-p, kind="mergesort")[:k]].sum())


def paired_auc_diff(y, champ, chall, n: int = 1000, seed: int = 0) -> tuple[float, float, float]:
    rng = np.random.default_rng(seed)
    diffs = []
    idx = np.arange(len(y))
    for _ in range(n):
        b = rng.choice(idx, len(idx), replace=True)
        if y[b].min() == y[b].max():
            continue
        diffs.append(roc_auc_score(y[b], chall[b]) - roc_auc_score(y[b], champ[b]))
    d = roc_auc_score(y, chall) - roc_auc_score(y, champ)
    lo, hi = np.percentile(diffs, [2.5, 97.5])
    return float(d), float(lo), float(hi)


@dataclass
class Decision:
    release: bool
    gates: list[dict]
    reason: str


def decide(y, champion, challenger, slices: dict[str, np.ndarray] | None = None, brings: str = "") -> Decision:
    y, champion, challenger = (np.asarray(a, dtype=float) for a in (y, champion, challenger))
    gates = []

    def gate(name, ok, value, rule):
        gates.append({"name": name, "passed": bool(ok), "value": value, "rule": rule})

    d, lo, hi = paired_auc_diff(y, champion, challenger)
    gate("not_worse_auc", lo > -MARGIN, {"diff": round(d, 4), "ci95": [round(lo, 4), round(hi, 4)]},
         f"lower 95% bound of the AUC difference above -{MARGIN}")
    e = ece(y, challenger)
    gate("calibrated", e <= ECE_MAX, round(e, 4), f"expected calibration error <= {ECE_MAX}")
    tc, tn = top_decile_hits(y, champion), top_decile_hits(y, challenger)
    gate("top_decile_kept", tn >= TOP_KEEP * tc, {"champion": tc, "challenger": tn}, f"top decile catches >= {TOP_KEEP:.0%} of the champion's buyers")
    worst = {}
    for name, m in (slices or {}).items():
        m = np.asarray(m, bool)
        if m.sum() < 30 or y[m].min() == y[m].max():       # too small to say anything: not judged
            continue
        worst[name] = round(roc_auc_score(y[m], challenger[m]) - roc_auc_score(y[m], champion[m]), 4)
    gate("slices_hold", all(v >= -SLICE_DROP for v in worst.values()), worst, f"AUC within {SLICE_DROP} of the champion on every slice")
    failed = [g["name"] for g in gates if not g["passed"]]
    if failed:
        return Decision(False, gates, "held: " + ", ".join(failed))
    if lo > 0:
        return Decision(True, gates, f"released: better (AUC +{d:.3f}, 95% interval {lo:+.3f} to {hi:+.3f})")
    if brings:
        return Decision(True, gates, f"released: not worse, and {brings}")
    return Decision(False, gates, f"held: not shown better (AUC {d:+.3f}, interval {lo:+.3f} to {hi:+.3f}) and brings nothing else")


# ------------------------------------------------------------------ the gate on real challengers
def challengers(warehouse, run_dir) -> dict:
    """The champion is whatever production would run: the candidate chosen on the validation period,
    refit on the training snapshots (the same rule as ml/train.py; a first version hard-coded gradient
    boosting and so judged challengers against a model production never shipped). Three challengers a
    team might propose, all scored on the same held-out test snapshot:
      other_algorithm    the candidate validation did not choose ("the fancier model")
      no_property_data   the champion without PLUTO and census enrichment: cheaper, is it as good?
      balanced_classes   the champion with classes reweighted to "help" the rare outcome: same idea,
                         broken probabilities
    """
    from pathlib import Path

    import duckdb
    import pandas as pd
    from sklearn.metrics import average_precision_score

    from .features import build_snapshots, matrix
    from .labels import attach_labels, person_owner
    from .train import ALL_CUTOFFS, FINAL_TRAIN, SELECT_TRAIN, SELECT_VAL, TEST, _prep, candidates, usable_columns
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
    prop = {c for c in cols if c.startswith("bldg_") or c in ("unitsres", "log_units", "small_building", "yearbuilt",
                                                             "property_matched", "log_zip_income")}
    X = lambda df, drop=(): matrix(df, families)[[c for c in cols if c not in drop]]
    val = {n: average_precision_score(part[SELECT_VAL].label, m.fit(X(tr_sel), tr_sel.label).predict_proba(X(part[SELECT_VAL]))[:, 1])
           for n, m in candidates().items()}
    chosen = max(val, key=val.get)
    other = next(n for n in candidates() if n != chosen)
    fit = lambda name, drop=(), **kw: _with(candidates()[name], **kw).fit(X(tr, drop), tr.label).predict_proba(X(te, drop))[:, 1]
    fp = te.first_party.fillna(False).to_numpy(bool)
    return {"y": te.label.to_numpy(), "chosen": chosen, "other": other,
            "champion": fit(chosen), "other_algorithm": fit(other), "no_property_data": fit(chosen, prop),
            "balanced_classes": fit(chosen, balanced=True),
            "slices": {"first_party": fp, "vendor_or_none": ~fp}}


def _with(model, balanced: bool = False):
    """The same estimator, with class_weight='balanced' on its classifier step when asked."""
    if not balanced:
        return model
    final = model.steps[-1][1] if hasattr(model, "steps") else model
    if hasattr(final, "members_") or hasattr(final, "seeds"):          # the seed ensemble passes params through
        final.params["class_weight"] = "balanced"
    else:
        final.set_params(class_weight="balanced")
    return model


def report(warehouse, run_dir) -> dict:
    c = challengers(warehouse, run_dir)
    from sklearn.metrics import roc_auc_score
    names = ("other_algorithm", "no_property_data", "balanced_classes")
    res = {"champion_algorithm": c["chosen"], "challenger_algorithm": c["other"],
           "test_rows": int(len(c["y"])), "test_buyers": int(c["y"].sum()),
           "auc": {k: round(float(roc_auc_score(c["y"], c[k])), 4) for k in ("champion",) + names}}
    brings = {"other_algorithm": "it is simpler and its weights can be read"} if c["other"] == "logistic" else {}
    for name in names:
        d = decide(c["y"], c["champion"], c[name], c["slices"], brings=brings.get(name, ""))
        res[name] = {"release": d.release, "reason": d.reason, "gates": d.gates}
    res["champion_ece"] = round(ece(c["y"], c["champion"]), 4)
    return res
