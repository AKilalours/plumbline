"""Load the real public reference tables, refusing anything that carries owner data.

PLUTO has an owner-name field. It is never requested by scripts/fetch_reference.py,
and this loader refuses a file that contains any owner* column, so a manually
downloaded full PLUTO export cannot slip a real person's name into the pipeline.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

REQUIRED_LOT_COLUMNS = ["bbl", "address", "zipcode", "borough", "unitsres", "yearbuilt", "bldgclass"]
FORBIDDEN_PREFIXES = ("owner",)
VALID_SOURCES = ("real", "fixture")


class ReferenceDataError(RuntimeError):
    pass


@dataclass(frozen=True)
class Reference:
    lots: pd.DataFrame    # one row per residential tax lot, with median_income joined on
    income: pd.DataFrame  # zcta, median_income
    source: str           # "real" (fetched) or "fixture" (test-only, invented)


def forbidden_columns(columns) -> list[str]:
    return [c for c in columns if str(c).strip().lower().startswith(FORBIDDEN_PREFIXES)]


def load_reference(ref_dir: str | Path) -> Reference:
    ref_dir = Path(ref_dir)
    marker = ref_dir / "REFERENCE_SOURCE"
    if not marker.exists():
        raise ReferenceDataError(
            f"{marker} is missing. Run scripts/fetch_reference.py first, which writes it."
        )
    source = marker.read_text().split()[0].strip()
    if source not in VALID_SOURCES:
        raise ReferenceDataError(f"REFERENCE_SOURCE must start with one of {VALID_SOURCES}, got {source!r}")

    lots = pd.read_csv(ref_dir / "pluto_lots.csv", dtype=str)
    bad = forbidden_columns(lots.columns)
    if bad:
        raise ReferenceDataError(f"owner fields must never be loaded, found {bad}")
    lots.columns = [c.strip().lower() for c in lots.columns]
    missing = [c for c in REQUIRED_LOT_COLUMNS if c not in lots.columns]
    if missing:
        raise ReferenceDataError(f"pluto_lots.csv lacks {missing}; has {list(lots.columns)}")
    lots = lots[REQUIRED_LOT_COLUMNS].copy()

    lots["bbl"] = lots["bbl"].str.replace(r"\.0+$", "", regex=True)
    lots["zipcode"] = lots["zipcode"].str.extract(r"(\d{5})", expand=False)
    lots["address"] = lots["address"].str.strip().str.upper()
    lots["unitsres"] = pd.to_numeric(lots["unitsres"], errors="coerce")
    lots["yearbuilt"] = pd.to_numeric(lots["yearbuilt"], errors="coerce")
    lots = lots.dropna(subset=["bbl", "address", "zipcode", "unitsres"])
    lots = lots[lots["unitsres"] > 0]

    income = pd.read_csv(ref_dir / "acs_income.csv", dtype={"zcta": str})
    income["median_income"] = pd.to_numeric(income["median_income"], errors="coerce")
    # The Census API marks suppressed or unavailable estimates with large negative sentinels.
    income = income[income["median_income"] > 0][["zcta", "median_income"]]

    lots = lots.merge(income, left_on="zipcode", right_on="zcta", how="inner").drop(columns="zcta")
    if lots.empty:
        raise ReferenceDataError("no tax lot has a ZIP with a Census income; check both files")
    # A stable order makes sampling by index reproducible regardless of file row order.
    lots = lots.sort_values("bbl", kind="mergesort").drop_duplicates("bbl").reset_index(drop=True)
    lots["unitsres"] = lots["unitsres"].astype(int)
    return Reference(lots=lots, income=income.reset_index(drop=True), source=source)
