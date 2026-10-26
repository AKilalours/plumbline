"""Score clusters against the hidden answer key. Used only for measurement, never for training."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd


def truth_for(records: pd.DataFrame, profile_orders: pd.DataFrame, run_dir: str | Path) -> pd.Series:
    run = Path(run_dir)
    links = pd.read_parquet(run / "truth" / "record_links.parquet")
    store_links = run / "store" / "record_links.parquet"
    if not store_links.exists():
        if len(profile_orders):
            raise FileNotFoundError(f"the warehouse has store orders but {store_links} is missing; it holds "
                                    "the store's answer key. Run `python -m plumbline.store build` for this run.")
        store_links = None   # the store is optional: no orders, nothing to score
    store = (pd.read_parquet(store_links) if store_links else
             pd.DataFrame(columns=["source", "record_id", "person_id"]).astype(str)).merge(
        profile_orders, left_on="record_id", right_on="order_id", suffixes=("_order", ""))
    store_truth = profile_owners(store, run).rename("person_id").reset_index().assign(source="store")
    t = pd.concat([links[links.source.isin(["crm", "vendor"])], store_truth], ignore_index=True)
    t["unique_id"] = t["source"] + ":" + t["record_id"]
    return records[["unique_id"]].merge(t[["unique_id", "person_id"]], how="left").set_index("unique_id")["person_id"]


def shared_profiles(store: pd.DataFrame, run: Path) -> pd.DataFrame:
    """Store profiles whose orders came from more than one true person.

    A profile is one email, name and ship-to address. Two people can share all of that: a father and
    son with one name, one home and one inbox. Nothing in the store's data tells them apart, so no
    matcher can, and the profile is scored as its most frequent buyer (the system is charged for the
    other). The first version asserted this never happens; it held up to 300,000 people and failed at
    1,000,000, where 10 such pairs exist.

    Anything else that mixes people means the profile key is wrong, and that still raises."""
    n = store.groupby("record_id")["person_id"].nunique()
    shared = store[store.record_id.isin(n[n > 1].index)]
    if shared.empty:
        return shared.assign(household_id=None)
    ppl = pd.read_parquet(run / "truth" / "people.parquet", columns=["person_id", "household_id", "first_name", "last_name"])
    s = shared[["record_id", "person_id"]].drop_duplicates().merge(ppl, on="person_id", how="left")
    s["name"] = s.first_name.str.lower() + " " + s.last_name.str.lower()
    per = s.groupby("record_id").agg(households=("household_id", "nunique"), names=("name", "nunique"))
    wrong = per[(per.households > 1) | (per.names > 1)]
    if len(wrong):
        raise AssertionError(f"{len(wrong)} store profiles mix people who could be told apart "
                             f"(another name or household); the profile key is wrong: {list(wrong.index[:5])}")
    return s


def profile_owners(store: pd.DataFrame, run: Path) -> pd.Series:
    """profile record_id -> the true person who placed most of its orders (ties: the lowest id)."""
    shared_profiles(store, run)                      # raises if the profile key itself is wrong
    c = store.groupby(["record_id", "person_id"]).size().rename("orders").reset_index()
    c = c.sort_values(["record_id", "orders", "person_id"], ascending=[True, False, True], kind="mergesort")
    return c.drop_duplicates("record_id").set_index("record_id")["person_id"]


def components(ids: pd.Index, left: np.ndarray, right: np.ndarray) -> pd.Series:
    """Connected components by union-find; returns cluster id (smallest member index) per id."""
    pos = pd.Series(np.arange(len(ids)), index=ids)
    parent = np.arange(len(ids))

    def find(x):
        root = x
        while parent[root] != root:
            root = parent[root]
        while parent[x] != root:
            parent[x], x = root, parent[x]
        return root

    for a, b in zip(pos[left].to_numpy(), pos[right].to_numpy()):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)
    return pd.Series([find(i) for i in range(len(ids))], index=ids)


def _pairs(n: pd.Series) -> int:
    n = n.astype(np.int64)
    return int((n * (n - 1) // 2).sum())


def pairwise_scores(cluster: pd.Series, person: pd.Series) -> dict:
    df = pd.DataFrame({"c": cluster, "p": person})
    tp = _pairs(df.groupby(["c", "p"]).size())
    pred = _pairs(df.groupby("c").size())
    true = _pairs(df.groupby("p").size())
    prec = tp / pred if pred else 1.0
    rec = tp / true if true else 1.0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    exact = df.groupby("p")["c"].nunique().eq(1) & df.groupby("p")["c"].first().map(df.groupby("c")["p"].nunique()).eq(1)
    return {"precision": round(prec, 4), "recall": round(rec, 4), "f1": round(f1, 4),
            "true_pairs": true, "predicted_pairs": pred, "people": int(df.p.nunique()),
            "clusters": int(df.c.nunique()), "people_resolved_exactly": round(float(exact.mean()), 4)}


def recall_by_damage(cluster: pd.Series, person: pd.Series, damage: pd.DataFrame) -> pd.DataFrame:
    """For records carrying each kind of damage: of their true partner records, how many were joined."""
    df = pd.DataFrame({"c": cluster, "p": person})
    size_p = df.groupby("p").size()
    size_cp = df.groupby(["c", "p"]).size()
    df["partners"] = df["p"].map(size_p) - 1
    df["found"] = [size_cp[(c, p)] - 1 for c, p in zip(df.c, df.p)]
    out = []
    groups = damage.groupby("label")["unique_id"].apply(set)
    groups["(no damage)"] = set(df.index) - set(damage.unique_id)
    for label, uids in groups.items():
        sub = df.loc[df.index.intersection(list(uids))]
        sub = sub[sub.partners > 0]
        if len(sub):
            out.append({"damage": label, "records": len(sub), "recall": round(sub.found.sum() / sub.partners.sum(), 4)})
    return pd.DataFrame(out).sort_values("recall")


def damage_table(run_dir: str | Path, records: pd.DataFrame, profile_orders: pd.DataFrame) -> pd.DataFrame:
    run = Path(run_dir)
    d = pd.read_parquet(run / "truth" / "damage_log.parquet")
    d = d[d.source.isin(["crm", "vendor"])].assign(unique_id=lambda x: x.source + ":" + x.record_id)
    sd = run / "store" / "damage_log.parquet"
    s = (pd.read_parquet(sd) if sd.exists() else pd.DataFrame(columns=["source", "record_id", "field", "kind"]).astype(str)
         ).merge(profile_orders, left_on="record_id", right_on="order_id", suffixes=("_o", ""))
    s = s.assign(unique_id="store:" + s["record_id"], source="store")
    d = pd.concat([d, s], ignore_index=True)
    field = d["field"].replace({"full_name": "name", "first_name": "name", "last_name": "name",
                                "street_address": "address", "address_line": "address"})
    d["label"] = d["kind"] + ":" + field
    return d[d.unique_id.isin(records.unique_id)][["unique_id", "label"]].drop_duplicates()


def household_false_merges(cluster: pd.Series, person: pd.Series, run_dir: str | Path) -> dict:
    hh = pd.read_parquet(Path(run_dir) / "truth" / "people.parquet", columns=["person_id", "household_id"])
    df = pd.DataFrame({"c": cluster, "p": person}).reset_index()
    df = df.merge(hh, left_on="p", right_on="person_id", how="left")
    per = df.groupby("c").agg(people=("p", "nunique"), households=("household_id", "nunique"))
    mixed = per[per.people > 1]
    return {"clusters_mixing_people": int(len(mixed)),
            "of_which_same_household": int((mixed.households == 1).sum())}


def _shared_count(profile_orders: pd.DataFrame, run: Path) -> int:
    links = run / "store" / "record_links.parquet"
    if not links.exists() or profile_orders.empty:
        return 0
    store = pd.read_parquet(links).merge(profile_orders, left_on="record_id", right_on="order_id", suffixes=("_order", ""))
    return int(shared_profiles(store, run).record_id.nunique()) if len(store) else 0


def score_warehouse(con, run_dir: str | Path) -> dict:
    """Score what resolve() wrote: record-level pairwise metrics, households, damage, email events."""
    from .prepare import build_records
    run = Path(run_dir)
    recs, po = build_records(con)
    ir = con.execute("SELECT unique_id, person_key FROM identity_records").df().set_index("unique_id")["person_key"]
    truth = truth_for(recs, po, run)
    cl = ir.reindex(truth.index)
    out = {"pairwise": pairwise_scores(cl, truth), "households": household_false_merges(cl, truth, run),
           "store_profiles_shared_by_people_no_data_tells_apart": _shared_count(po, run)}
    dmg = recall_by_damage(cl, truth, damage_table(run, recs, po))
    out["recall_by_damage"] = dict(zip(dmg.damage, dmg.recall))
    links = pd.read_parquet(run / "truth" / "record_links.parquet").query("source == 'email'")
    ev = con.execute("SELECT record_id, person_key FROM identity_map WHERE source = 'email'").df()
    ev = ev.merge(links[["record_id", "person_id"]], on="record_id")
    # an event is right if its person_key's records truly belong to the event's person
    rec_person = pd.DataFrame({"person_key": cl, "person_id": truth}).dropna()
    owner = rec_person.groupby("person_key")["person_id"].agg(lambda s: s.mode().iat[0])
    got = ev.dropna(subset=["person_key"])
    out["email_events"] = {"resolved_share": round(len(got) / len(ev), 4),
                           "resolved_correct": round(float((got.person_key.map(owner) == got.person_id).mean()), 4)}
    return out
