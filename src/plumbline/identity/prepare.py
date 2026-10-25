"""Turn the current_* views into one table of person records with comparable columns.

CRM and vendor rows are one record each. Store orders are collapsed to distinct customer profiles
(same email, name and ship-to), because one shopper with 12 orders is one identity claim made 12
times, not 12 people. Email events carry only an address, so they are attached after matching.
"""
from __future__ import annotations

import duckdb
import pandas as pd

from .standardize import clean_name, email_user, name_group, split_full_name, standardize_street

COLUMNS = ["unique_id", "source", "record_id", "first_name", "first_group", "last_name", "email",
           "email_user", "phone", "house", "street", "unit", "zip", "dob", "birth_year"]


def _finish(df: pd.DataFrame, source: str) -> pd.DataFrame:
    parts = df["street_raw"].map(standardize_street)
    df["house"], df["street"], df["unit"] = zip(*parts) if len(df) else ([], [], [])
    df["first_group"] = df["first_name"].map(name_group)
    df["email"] = df["email"].where(df["email"].notna(), None)
    df["email_user"] = df["email"].map(email_user)
    df["source"] = source
    df["unique_id"] = source + ":" + df["record_id"]
    for c in ("dob", "birth_year", "phone"):
        if c not in df:
            df[c] = None
    return df[COLUMNS]


def build_records(con: duckdb.DuckDBPyConnection) -> tuple[pd.DataFrame, pd.DataFrame]:
    """-> (records, store_profile_orders mapping profile record_id -> order_id)."""
    crm = con.execute("""SELECT crm_id AS record_id, first_name, last_name, email, phone,
                                street_address AS street_raw, zip FROM current_crm ORDER BY crm_id""").df()
    crm["first_name"] = crm["first_name"].map(clean_name)
    crm["last_name"] = crm["last_name"].map(clean_name)

    ven = con.execute("""SELECT vendor_record_id AS record_id, full_name, dob, phone, email,
                                address_line AS street_raw, zip FROM current_vendor ORDER BY vendor_record_id""").df()
    names = ven["full_name"].map(split_full_name)
    ven["first_name"], ven["last_name"] = zip(*names) if len(ven) else ([], [])
    ven["birth_year"] = ven["dob"].str[:4]
    ven["dob"] = ven["dob"].where(ven["dob"].str.len() == 10, None)

    orders = con.execute("""SELECT order_id, customer_email AS email, first_name, last_name,
                                   ship_street AS street_raw, ship_zip AS zip FROM current_store ORDER BY order_id""").df()
    for c in ("first_name", "last_name"):
        orders[c] = orders[c].map(clean_name)
    key = ["email", "first_name", "last_name", "street_raw", "zip"]
    orders["record_id"] = orders.groupby(key, dropna=False)["order_id"].transform("min")
    profile_orders = orders[["record_id", "order_id"]]
    store = orders.drop_duplicates("record_id").drop(columns="order_id")

    recs = pd.concat([_finish(crm, "crm"), _finish(ven, "vendor"), _finish(store, "store")], ignore_index=True)
    recs = recs.astype(object).where(recs.notna(), None)
    return recs, profile_orders
