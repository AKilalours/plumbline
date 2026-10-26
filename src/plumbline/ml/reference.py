"""Load public reference data into the warehouse as enrichment tables.

ref_property: NYC PLUTO tax lots (residential units, building class, year built) keyed by a
              standardized house number, street and zip, the same standardization the golden
              person's address uses, so the two join without fuzzy matching.
ref_zip_income: Census ACS 5-year median household income by ZIP code tabulation area.

Reuses the generator's reference loader on purpose: it refuses any owner* column, so no real
person's name can enter the warehouse through this door either.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from ..identity.standardize import standardize_street
from ..synth.reference import load_reference


def load_reference_tables(con, ref_dir: str | Path) -> dict:
    ref = load_reference(ref_dir)
    lots = ref.lots
    parts = [standardize_street(a) for a in lots["address"]]
    prop = pd.DataFrame({
        "house": [p[0] for p in parts], "street": [p[1] for p in parts], "zip": lots["zipcode"].to_numpy(),
        "borough": lots["borough"].to_numpy(), "unitsres": lots["unitsres"].to_numpy(),
        "yearbuilt": lots["yearbuilt"].to_numpy(), "bldgclass": lots["bldgclass"].to_numpy(),
    }).dropna(subset=["house", "street"])
    # one lot per address key; a key that names several lots keeps the largest building
    prop = prop.sort_values("unitsres", ascending=False, kind="mergesort").drop_duplicates(["house", "street", "zip"])
    inc = ref.income.rename(columns={"zcta": "zip"})
    con.execute("BEGIN")
    try:
        for name, df in (("ref_property", prop), ("ref_zip_income", inc)):
            con.register("_df", df)
            con.execute(f"CREATE OR REPLACE TABLE {name} AS SELECT * FROM _df")
            con.unregister("_df")
        con.execute("CREATE OR REPLACE TABLE ref_source AS SELECT ? AS source", [ref.source])
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return {"properties": len(prop), "zips": len(inc), "source": ref.source}
