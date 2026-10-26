"""Labels: did the person's household buy a home within the horizon after the cutoff?

In production these come from an outcome feed (in New York, deed records are public through
ACRIS). Here the synthetic answer key stands in for that feed. Labels are only ever joined to a
snapshot after its features were built, and a snapshot is used for training only if its label
window closed before the evaluation cutoff (see train.check_no_label_leak).
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

HORIZON_DAYS = 180


def person_owner(con, run: Path) -> pd.Series:
    from ..products.evaluate import _owner
    return _owner(con, run)


def attach_labels(snaps: pd.DataFrame, owner: pd.Series, run_dir: str | Path, horizon: int = HORIZON_DAYS) -> pd.DataFrame:
    run = Path(run_dir)
    ev = pd.read_parquet(run / "truth" / "events.parquet")
    buys = ev[ev.event_type == "home_purchase"][["person_id", "event_date"]]
    buys = buys.assign(event_date=pd.to_datetime(buys.event_date)).groupby("person_id").event_date.min()
    out = snaps.copy()
    out["person_id"] = out.person_key.map(owner)
    t = pd.to_datetime(out.as_of)
    d = out.person_id.map(buys)
    out["bought_before"] = (d <= t).fillna(False).astype(bool)
    out["label"] = ((d > t) & (d <= t + pd.Timedelta(days=horizon))).fillna(False).astype(int)
    return out


def oracle_score(df: pd.DataFrame, run_dir: str | Path, horizon: int = HORIZON_DAYS) -> pd.Series:
    """The best possible score: the generator's own purchase probability for the household, spread
    over the dates the purchase could fall on, given it had not happened by the cutoff. No model
    built on observable data can beat this; it is the ceiling the model is compared against."""
    run = Path(run_dir)
    ppl = pd.read_parquet(run / "truth" / "people.parquet", columns=["person_id", "household_id"])
    hh = pd.read_parquet(run / "truth" / "households.parquet", columns=["household_id", "head_person_id", "purchase_probability"])
    jobs = pd.read_parquet(run / "truth" / "events.parquet").query("event_type == 'job_change'")
    jobs = jobs.assign(event_date=pd.to_datetime(jobs.event_date)).groupby("person_id").event_date.min()
    man = __import__("json").loads((run / "manifest.json").read_text())
    cfg = man.get("config", man)
    start, end = pd.Timestamp(cfg.get("window_start", "2024-01-01")), pd.Timestamp(cfg.get("window_end", "2025-12-31"))
    hh["lo"] = hh.head_person_id.map(jobs) + pd.Timedelta(days=30)
    hh["lo"] = hh["lo"].fillna(start + pd.Timedelta(days=30)).clip(upper=end)
    m = df[["person_id", "as_of"]].merge(ppl, on="person_id", how="left").merge(hh, on="household_id", how="left")
    t = pd.to_datetime(m.as_of)
    span = (end - m.lo).dt.days.clip(lower=1)
    before = ((t - m.lo).dt.days.clip(lower=0, upper=span)) / span
    inside = (((t + pd.Timedelta(days=horizon)).clip(upper=end) - t.clip(lower=m.lo)).dt.days.clip(lower=0)) / span
    p = m.purchase_probability.fillna(0)
    score = p * inside / (1 - p * before).clip(lower=1e-9)
    return pd.Series(score.to_numpy(), index=df.index)
