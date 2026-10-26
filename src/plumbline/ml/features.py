"""Point-in-time feature snapshots: build the dbt features as of each cutoff and keep each one.

A snapshot for cutoff T is built by dbt with as_of=T, so every input it reads was known by T.
Snapshots accumulate in ml.feature_snapshots (as_of, person_key, features...), which is what
training reads.

Snapshots are built on a throwaway copy of the warehouse, never on the live one. An earlier version
rebuilt the live products for each past date and rebuilt them back at the end: one failure in
between left the live golden person at 17,109 people as of 2024-06-30 instead of 68,454 as of
2025-12-31, and the activation audience would have gone out a year stale.
"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import duckdb
import pandas as pd

from ..products.build import build

NUMERIC = ["log_zip_income", "unitsres", "log_units", "yearbuilt", "orders_window", "revenue_window",
           "avg_order_value", "days_since_last_order_in_window", "opens_90d", "clicks_90d", "moves_window",
           "work_emails_seen", "income_band", "age_years"]
BOOLEAN = ["small_building", "has_unit", "property_matched", "new_job_window", "vendor_homeowner"]
CATEGORICAL = ["bldg_family"]
# Attributes that describe a person's state and change over time. A value delivered after the
# cutoff may already reflect the outcome (a buyer becomes a homeowner), so these may never be
# backfilled into an earlier snapshot. test_ml enforces it.
MUTABLE = {"vendor_homeowner", "income_band", "unitsres", "log_units", "small_building", "yearbuilt",
           "bldg_family", "has_unit", "property_matched", "log_zip_income"}


def build_snapshots(warehouse: str | Path, cutoffs: list[str], restore_as_of: str | None = None) -> pd.DataFrame:
    """restore_as_of is accepted for compatibility and no longer needed: the live tables are never rebuilt."""
    frames = []
    work = Path(tempfile.mkdtemp(prefix="plumbline-snapshots-"))
    try:
        copy = work / "warehouse.duckdb"
        shutil.copy(warehouse, copy)
        for t in cutoffs:
            build(copy, as_of=t)
            with duckdb.connect(str(copy), read_only=True) as con:
                frames.append(con.execute("SELECT * FROM products.fct_person_features").df())
    finally:
        shutil.rmtree(work, ignore_errors=True)
    snaps = pd.concat(frames, ignore_index=True)
    with duckdb.connect(str(warehouse)) as con:
        con.execute("CREATE SCHEMA IF NOT EXISTS ml")
        con.register("_s", snaps)
        con.execute("CREATE OR REPLACE TABLE ml.feature_snapshots AS SELECT * FROM _s")
        con.unregister("_s")
    return snaps


def matrix(df: pd.DataFrame, families: list[str] | None = None, drop: tuple[str, ...] = ()) -> pd.DataFrame:
    """Model input: numeric and boolean columns as floats (NaN kept), building family one-hot."""
    cols = [c for c in NUMERIC + BOOLEAN if c not in drop]
    X = df[cols].astype("float64")
    if "bldg_family" not in drop:
        fams = families if families is not None else sorted(df["bldg_family"].dropna().unique())
        for f in fams:
            X[f"bldg_{f}"] = (df["bldg_family"] == f).astype("float64")
    return X
