"""Every question the app asks of a world, as plain functions over the warehouse and the answer key.

The answer key (truth/) is what the generator knows and the pipeline never sees: who each record
really belongs to, who really lives together. The app puts the two side by side, so a viewer can
check any result instead of trusting a summary.
"""
from __future__ import annotations

import csv
import json
import shutil
import tempfile
from pathlib import Path

import duckdb
import pandas as pd

from ..ingest.contracts import email_hash, load_contracts, phone_hash
from ..ingest.engine import ingest
from ..privacy.evaluate import snapshot
from ..privacy.person_deletion import expand_deletions
from .demo import CONTRACTS, REPO, Demo

# record_links uses the contract names; identity_map calls a store order "store_order"
MAP_SOURCE = {"crm": "crm", "vendor": "vendor", "store": "store_order", "email": "email"}
SOURCE_LABEL = {"crm": "CRM", "vendor": "Data vendor", "store": "Store order", "email": "Email event"}
RESULTS = REPO / "docs" / "results" / "scale.json"


def connect(demo: Demo) -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(demo.warehouse), read_only=True)


def links(run: Path) -> pd.DataFrame:
    """Every source record and the person it really belongs to."""
    parts = [pd.read_parquet(run / "truth" / "record_links.parquet")]
    if (run / "store" / "record_links.parquet").exists():
        parts.append(pd.read_parquet(run / "store" / "record_links.parquet"))
    return pd.concat(parts, ignore_index=True)


def people(run: Path) -> pd.DataFrame:
    return pd.read_parquet(run / "truth" / "people.parquet")


def _resolved(con) -> pd.DataFrame:
    m = con.execute("SELECT source, record_id, person_key FROM identity_map").df()
    back = {v: k for k, v in MAP_SOURCE.items()}
    return m.assign(source=m.source.map(back)).dropna(subset=["source"])


def identity_outcomes(con, run: Path) -> pd.DataFrame:
    """One row per real person with records in the warehouse: how many resolved persons their records
    landed in (1 is right) and how many other people's records share those persons (0 is right)."""
    truth = links(run)
    m = _resolved(con).merge(truth, on=["source", "record_id"], how="inner").dropna(subset=["person_key"])
    m = m[m.source != "email"]                       # events describe an inbox, not a person
    per_key = m.groupby("person_key").person_id.nunique()
    keys = m.groupby("person_id").person_key.agg(lambda s: sorted(set(s)))
    out = pd.DataFrame({"person_id": keys.index, "keys": keys.values})
    out["persons_found"] = out["keys"].map(len)
    shared = m[m.person_key.isin(per_key[per_key > 1].index)]
    strangers = shared.merge(shared[["person_key", "person_id"]].drop_duplicates(), on="person_key", suffixes=("", "_other"))
    strangers = strangers[strangers.person_id != strangers.person_id_other].groupby("person_id").person_id_other.nunique()
    out["merged_with"] = out.person_id.map(strangers).fillna(0).astype(int)
    src = m.groupby("person_id").source.agg(lambda s: sorted(set(s)))
    out["sources"] = out.person_id.map(src)
    out["records"] = out.person_id.map(m.groupby("person_id").size())
    dmg = pd.read_parquet(run / "truth" / "damage_log.parquet")
    if (run / "store" / "damage_log.parquet").exists():
        dmg = pd.concat([dmg, pd.read_parquet(run / "store" / "damage_log.parquet")])
    dmg = dmg.merge(truth, on=["source", "record_id"])
    out["damaged_fields"] = out.person_id.map(dmg.groupby("person_id").size()).fillna(0).astype(int)
    p = people(run).set_index("person_id")
    out["name"] = out.person_id.map(p.first_name + " " + p.last_name)
    out["verdict"] = ["split" if f > 1 else ("merged" if w else "correct")
                      for f, w in zip(out.persons_found, out.merged_with)]
    return out.sort_values("person_id").reset_index(drop=True)


def showcase(outcomes: pd.DataFrame, k: int = 6) -> dict[str, list[str]]:
    """People worth opening: found despite typos across many sources, and the honest misses."""
    rich = outcomes[(outcomes.verdict == "correct") & (outcomes.sources.map(len) >= 3)]
    rich = rich.sort_values(["damaged_fields", "records", "person_id"], ascending=[False, False, True])
    return {"correct": rich.person_id.head(k).tolist(),
            "split": outcomes[outcomes.verdict == "split"].person_id.head(k).tolist(),
            "merged": outcomes[outcomes.verdict == "merged"].person_id.head(k).tolist()}


def person_records(con, run: Path, person_id: str) -> pd.DataFrame:
    """Every record of one real person that is in the warehouse, as the source sent it, and the
    resolved person the pipeline put it in."""
    mine = links(run)
    mine = mine[mine.person_id == person_id]
    rows = []
    q = {"crm": ("current_crm", "crm_id", "first_name || ' ' || last_name", "email", "phone", "street_address || ', ' || zip"),
         "vendor": ("current_vendor", "vendor_record_id", "full_name", "email", "phone", "address_line || ', ' || zip"),
         "store": ("current_store", "order_id", "first_name || ' ' || last_name", "customer_email", "NULL", "ship_street || ', ' || ship_zip")}
    for src, (table, key, name, email, phone, addr) in q.items():
        ids = mine.loc[mine.source == src, "record_id"].tolist()
        if ids:
            d = con.execute(f"SELECT '{src}' AS source, {key} AS record_id, {name} AS name, {email} AS email, "
                            f"{phone} AS phone, {addr} AS address FROM {table} WHERE list_contains(?, {key})", [ids]).df()
            rows.append(d)
    recs = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame(
        columns=["source", "record_id", "name", "email", "phone", "address"])
    recs = recs.merge(_resolved(con), on=["source", "record_id"], how="left")
    recs.insert(0, "kind", recs.source.map(SOURCE_LABEL))
    return recs.drop(columns="source").sort_values(["kind", "record_id"]).reset_index(drop=True)


def person_truth(run: Path, person_id: str) -> dict:
    p = people(run).set_index("person_id").loc[person_id]
    return {"name": f"{p.first_name} {p.last_name}", "personal_email": p.personal_email, "phone": p.phone,
            "household_id": p.household_id, "shares_household_email": bool(p.shares_household_email)}


def strangers_in(con, run: Path, person_keys: list[str], person_id: str) -> pd.DataFrame:
    """Records of other real people that the pipeline put in the same resolved person."""
    m = _resolved(con)
    m = m[m.person_key.isin(person_keys) & (m.source != "email")].merge(links(run), on=["source", "record_id"])
    m = m[m.person_id != person_id]
    p = people(run).set_index("person_id")
    return m.assign(really=m.person_id.map(p.first_name + " " + p.last_name))[["source", "record_id", "person_key", "really"]]


def household_outcomes(con, run: Path) -> pd.DataFrame:
    """One row per household the pipeline built: how many real households its members come from (1 is
    right) and whether it holds the whole real household."""
    hh = con.execute("SELECT household_key, address_line, zip, members, person_keys FROM products.dim_household").df()
    owner = _owners(con, run)
    pp = people(run).set_index("person_id")
    rows = []
    for r in hh.itertuples():
        ids = [owner.get(k) for k in r.person_keys]
        real = {pp.household_id.get(i) for i in ids if i is not None}
        rows.append({"household_key": r.household_key, "address": f"{r.address_line}, {r.zip}", "members": r.members,
                     "real_households": len(real), "real_household": next(iter(real)) if len(real) == 1 else None})
    out = pd.DataFrame(rows)
    size = pp.groupby("household_id").size()
    found = out[out.real_households == 1].groupby("real_household").members.sum()
    out["real_size"] = out.real_household.map(size)
    out["complete"] = out.real_household.map(found).eq(out.real_size) & out.real_household.notna()
    return out


def _owners(con, run: Path) -> dict:
    """Resolved person -> the real person most of its records belong to."""
    m = _resolved(con)
    m = m[m.source != "email"].merge(links(run), on=["source", "record_id"])
    return m.groupby("person_key").person_id.agg(lambda s: s.value_counts().index[0]).to_dict()


def household_members(con, run: Path, household_key: str) -> pd.DataFrame:
    keys = con.execute("SELECT unnest(person_keys) FROM products.dim_household WHERE household_key = ?",
                       [household_key]).df().iloc[:, 0].tolist()
    d = con.execute("""SELECT person_key, first_name || ' ' || last_name AS name, personal_email, address_line
                       FROM products.dim_person WHERE list_contains(?, person_key)""", [keys]).df()
    owner = _owners(con, run)
    pp = people(run).set_index("person_id")
    d["person_id"] = d.person_key.map(lambda k: owner.get(k))
    d["real_household"] = d.person_id.map(pp.household_id)
    d["really"] = d.person_id.map(pp.first_name + " " + pp.last_name)
    d["counted_twice"] = d.person_id.duplicated(keep=False) & d.person_id.notna()     # one person split in two
    return d.sort_values("name").reset_index(drop=True)


def deletion_candidates(con, run: Path, outcomes: pd.DataFrame, k: int = 40) -> list[str]:
    """People it is interesting to delete: records in several sources, some of them damaged, and
    others in their home with the same last name (the case deletion can get wrong)."""
    pp = people(run)
    same_name = pp.groupby(["household_id", "last_name"]).person_id.transform("size")
    crowded = set(pp.loc[same_name > 1, "person_id"])
    gone = set(pd.read_parquet(run / "truth" / "deletion_requests.parquet").person_id)
    c = outcomes[(outcomes.verdict == "correct") & outcomes.person_id.isin(crowded) & ~outcomes.person_id.isin(gone)
                 & (outcomes.sources.map(len) >= 2)].copy()
    c["hidden"] = c.person_id.map(hidden_records(con, run)).fillna(0)
    return c.sort_values(["hidden", "damaged_fields", "records", "person_id"],
                         ascending=[False, False, False, True]).person_id.head(k).tolist()


def hidden_records(con, run: Path) -> pd.Series:
    """Per person: CRM and vendor records carrying neither their email nor their phone, which a
    deletion request cannot reach at load. Only the identity graph can find them."""
    pp = people(run).set_index("person_id")
    l = links(run)
    rows = []
    for src, table, key in (("crm", "current_crm", "crm_id"), ("vendor", "current_vendor", "vendor_record_id")):
        d = con.execute(f"SELECT {key} AS record_id, lower(trim(email)) AS email, regexp_replace(phone, '[^0-9]', '', 'g') AS phone "
                        f"FROM {table}").df().merge(l[l.source == src], on="record_id")
        d["reachable"] = (d.email == d.person_id.map(pp.personal_email).str.lower()) | (d.phone == d.person_id.map(pp.phone).astype(str))
        rows.append(d[~d.reachable])
    h = pd.concat(rows)
    return h.groupby("person_id").size()


def deletion_sandbox(demo: Demo, person_id: str, work: str | Path | None = None, keep: bool = False) -> dict:
    """File a deletion request for one person on a copy of the world, run it the way the nightly
    pipeline does (purge at load, then person-level deletion), and check every removed record
    against the answer key. The demo world itself is never changed."""
    work = Path(work or tempfile.mkdtemp(prefix="plumbline-sandbox-"))
    work.mkdir(parents=True, exist_ok=True)
    try:
        run, wh = work / "run", work / "w.duckdb"
        shutil.copytree(demo.run, run)
        shutil.copy(demo.warehouse, wh)
        p = people(run).set_index("person_id").loc[person_id]
        req = run / "sources" / "privacy" / "deletion_requests_2026-01-20.csv"
        with open(req, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["request_id", "email_sha256", "phone_sha256", "received_date"])
            w.writerow(["R9000001", email_hash(p.personal_email), phone_hash(str(p.phone)), "2026-01-20"])
        contracts = load_contracts(CONTRACTS)
        with duckdb.connect(str(wh)) as con:
            before = snapshot(con)
        ingest(run, wh, contracts)
        with duckdb.connect(str(wh)) as con:
            loaded = snapshot(con)
            expand_deletions(con, contracts)
            after = snapshot(con)
            review = con.execute("""SELECT DISTINCT r.person_key, r.reason FROM deletion_review r
                                    JOIN deletion_tombstones t USING (tombstone_id) WHERE t.request_id = 'R9000001'""").df()
        truth = links(run)
        rows = []
        for src in before:
            t = truth[truth.source == src]
            owner = dict(zip(t.record_id, t.person_id))
            for rid in before[src] - after[src]:
                rows.append({"kind": SOURCE_LABEL[src], "record_id": rid,
                             "removed_by": "purge at load" if rid not in loaded[src]
                             else "identity graph",
                             "belongs_to": "the requester" if owner.get(rid) == person_id else "SOMEONE ELSE"})
            for rid in after[src]:
                if owner.get(rid) == person_id:
                    rows.append({"kind": SOURCE_LABEL[src], "record_id": rid, "removed_by": "NOT REMOVED",
                                 "belongs_to": "the requester"})
        out = pd.DataFrame(rows, columns=["kind", "record_id", "removed_by", "belongs_to"])
        out = out.sort_values(["removed_by", "kind", "record_id"]).reset_index(drop=True)
        return {"records": out,
                "removed": int((out.removed_by != "NOT REMOVED").sum()),
                "left_behind": int((out.removed_by == "NOT REMOVED").sum()),
                "innocent": int((out.belongs_to == "SOMEONE ELSE").sum()),
                "review": review, **({"run": run, "warehouse": wh} if keep else {})}
    finally:
        if not keep:
            shutil.rmtree(work, ignore_errors=True)


def saved_results(path: Path = RESULTS) -> dict:
    return json.loads(Path(path).read_text())


def agent_context(demo: Demo, work: str | Path, use_model: bool = False):
    """An agent for one viewer: its own copy of the warehouse (an approved deletion must never change
    what other viewers see), its own approvals and audit database, and the example roles. The
    language model is off unless the app is started with it on: a public page holding an API key
    is a page anyone can spend that key from."""
    import yaml
    from ..agents.agent import Context
    from ..agents.llm import ChatModel
    work = Path(work)
    work.mkdir(parents=True, exist_ok=True)
    wh = work / "warehouse.duckdb"
    if not wh.exists():
        shutil.copy(demo.warehouse, wh)
    roles = yaml.safe_load((REPO / "ops" / "roles.example.yaml").read_text())["users"]
    return Context(wh, REPO, CONTRACTS, work / "agent.duckdb", {u: set(r) for u, r in roles.items()}, demo.run,
                   ChatModel.from_env() if use_model else None)


def example_roles() -> dict[str, list[str]]:
    import yaml
    return yaml.safe_load((REPO / "ops" / "roles.example.yaml").read_text())["users"]


SOURCE_COLOR = {"CRM": "#DCE8F5", "Data vendor": "#F3E3CF", "Store order": "#DDEFE6", "Email event": "#ECE3F3"}


def _dot(s) -> str:
    return str(s).replace("\\", "\\\\").replace('"', '\\"')


def person_dot(recs: pd.DataFrame, strangers: pd.DataFrame, truth_name: str) -> str:
    """A Graphviz picture of one person: each record as the source spelled it, boxed by the resolved
    person the pipeline put it in, joined to the answer key's person when it really is theirs.
    Store orders are folded into one node per resolved person; someone else's records are red."""
    out = ['digraph G {', 'rankdir=LR; bgcolor="transparent"; nodesep=0.25; ranksep=1.1; size="9,5";',
           'node [shape=box style="rounded,filled" fontname="Helvetica" fontsize=11 color="#9AA3B0" penwidth=1];',
           'edge [color="#A8741A" style=dashed arrowhead=none penwidth=1.4];',
           f'truth [label="{_dot(truth_name)}\\nanswer key" fillcolor="#F6E7C8" color="#A8741A" penwidth=2 fontsize=13];']
    rows = [(r.kind, r.record_id, r.name, r.person_key, True) for r in recs.itertuples()]
    label_of = {"crm": "CRM", "vendor": "Data vendor", "store": "Store order"}
    rows += [(label_of.get(r.source, r.source), r.record_id, r.really, r.person_key, False) for r in strangers.itertuples()]
    keys = list(dict.fromkeys(k for *_, k, _ in rows if isinstance(k, str)))
    want = " ".join(truth_name.lower().split())
    edges = []          # outside the clusters: an edge written inside one pulls its far end into it
    for i, k in enumerate(keys, 1):
        out.append(f'subgraph cluster_{i} {{ label="Pipeline person {i}"; fontname="Helvetica"; fontsize=10; '
                   'style="rounded,dashed"; color="#5E6878";')
        mine = [r for r in rows if r[3] == k]
        orders = [r for r in mine if r[0] == "Store order" and r[4]]
        for j, (kind, rid, name, _, own) in enumerate(r for r in mine if not (r[0] == "Store order" and r[4])):
            node = f"n{i}_{j}"
            sent = " ".join(str(name or "").lower().replace(",", " ").split())
            typo = own and sorted(sent.split()) != sorted(want.split())
            fill = SOURCE_COLOR.get(kind, "white") if own else "#F8E1DC"
            extra = ' color="#B3432F" penwidth=2' if not own else (' fontcolor="#B3432F"' if typo else "")
            tag = "\\n(spelled differently)" if typo else ("" if own else "\\nsomeone else")
            out.append(f'{node} [label="{_dot(kind)}\\n{_dot(name)}{tag}" fillcolor="{fill}"{extra}];')
            if own:
                edges.append(f"{node} -> truth;")
        if orders:
            names = sorted({str(o[2]) for o in orders})
            node = f"n{i}_orders"
            out.append(f'{node} [label="Store orders x{len(orders)}\\n{_dot(" / ".join(names[:2]))}" '
                       f'fillcolor="{SOURCE_COLOR["Store order"]}"];')
            edges.append(f"{node} -> truth;")
        out.append("}")
    out += edges + ["}"]
    return "\n".join(out)


HOME_COLORS = ["#2E7A68", "#B3432F", "#5E6878", "#A8741A"]


def household_dot(members: pd.DataFrame, address: str) -> str:
    """The home the pipeline built, and each member as the answer key knows them: one colour per real
    household, so two homes joined show two colours, and one person counted twice shows twice."""
    homes = list(dict.fromkeys(members.real_household.fillna("unknown")))
    out = ['digraph H {', 'rankdir=LR; bgcolor="transparent"; nodesep=0.2; ranksep=0.8;',
           'node [shape=box style="rounded,filled" fontname="Helvetica" fontsize=13 penwidth=1.5];',
           'edge [color="#9AA3B0" arrowhead=none];',
           f'home [label="{_dot(address)}" shape=house fillcolor="#F6E7C8" color="#A8741A" fontsize=12];']
    for i, r in enumerate(members.itertuples()):
        c = HOME_COLORS[homes.index(r.real_household if isinstance(r.real_household, str) else "unknown") % len(HOME_COLORS)]
        note = "\\ncounted twice" if r.counted_twice else ""
        out.append(f'm{i} [label="{_dot(str(r.name).title())}\\nreally {_dot(r.really)}{note}" fillcolor="white" color="{c}" fontcolor="{c}"];')
        out.append(f"home -> m{i};")
    out.append("}")
    return "\n".join(out)
