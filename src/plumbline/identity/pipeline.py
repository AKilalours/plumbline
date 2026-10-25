"""Run identity resolution against the warehouse and write the person map back into it.

Tables written (replaced on every run, in one transaction):
  identity_records   one row per matchable record (CRM row, vendor row, store customer profile)
  identity_map       (source, record_id) -> person_key for every current record, including each
                     store order and each email event; unresolved rows say why
  identity_pairs     scored candidate pairs >= 0.5, so any merge can be explained afterwards

Email events carry only an address. An event joins a person when its address appears on records of
exactly one person. An address on two persons (a shared family inbox) is left unresolved on purpose:
guessing would hand one person's engagement to the other.
"""
from __future__ import annotations

import hashlib
import time

import duckdb
import pandas as pd

from .match import MATCH_THRESHOLD, train_and_predict
from .prepare import build_records


def person_key(cluster_id: str) -> str:
    return "PK" + hashlib.sha256(cluster_id.encode()).hexdigest()[:12]


def resolve(con: duckdb.DuckDBPyConnection, threshold: float = MATCH_THRESHOLD, seed: int = 7) -> dict:
    t0 = time.time()
    recs, profile_orders = build_records(con)
    pairs, clusters, _ = train_and_predict(recs, seed=seed, threshold=threshold)
    recs = recs.merge(clusters, on="unique_id")
    recs["person_key"] = recs["cluster_id"].map(person_key)

    base = recs[["source", "record_id", "person_key"]].copy()
    orders = profile_orders.merge(recs.loc[recs.source == "store", ["record_id", "person_key"]], on="record_id")
    orders = orders.assign(source="store_order", record_id=orders["order_id"])[["source", "record_id", "person_key"]]

    by_email = recs.dropna(subset=["email"]).groupby("email")["person_key"].agg(["nunique", "first"])
    events = con.execute("SELECT event_id, email FROM current_email").df()
    events = events.merge(by_email, left_on="email", right_index=True, how="left")
    events["person_key"] = events["first"].where(events["nunique"] == 1)
    events["unresolved_reason"] = None
    events.loc[events["nunique"] > 1, "unresolved_reason"] = "email_on_several_persons"
    events.loc[events["nunique"].isna(), "unresolved_reason"] = "email_not_on_any_record"
    events = events.assign(source="email", record_id=events["event_id"])[["source", "record_id", "person_key", "unresolved_reason"]]

    idmap = pd.concat([base.assign(unresolved_reason=None), orders.assign(unresolved_reason=None), events],
                      ignore_index=True)
    strong = pairs[pairs.match_probability >= 0.5]

    con.execute("BEGIN")
    try:
        for name, df in (("identity_records", recs), ("identity_map", idmap), ("identity_pairs", strong)):
            con.register("_df", df)
            con.execute(f"CREATE OR REPLACE TABLE {name} AS SELECT * FROM _df")
            con.unregister("_df")
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return {
        "records": len(recs), "persons": int(recs.person_key.nunique()),
        "refused_merges": clusters.attrs.get("refused_merges"),
        "email_events": len(events),
        "email_events_resolved": int(events.person_key.notna().sum()),
        "email_events_shared_inbox": int((events.unresolved_reason == "email_on_several_persons").sum()),
        "seconds": round(time.time() - t0, 1),
    }
