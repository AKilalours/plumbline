"""Measure deletion against the answer key: who leaked, and who was deleted who should not be."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

VIEWS = {"crm": ("current_crm", "crm_id"), "vendor": ("current_vendor", "vendor_record_id"),
         "store": ("current_store", "order_id"), "email": ("current_email", "event_id")}


def _links(run: Path) -> pd.DataFrame:
    a = pd.read_parquet(run / "truth" / "record_links.parquet")
    b = pd.read_parquet(run / "store" / "record_links.parquet") if (run / "store" / "record_links.parquet").exists() else a.iloc[:0]
    return pd.concat([a, b], ignore_index=True)


def deletion_report(con, run_dir: str | Path, before: dict[str, set] | None = None) -> dict:
    """`before`: current record ids per source before person-level deletion, to count collateral."""
    run = Path(run_dir)
    links = _links(run)
    deleted_people = set(pd.read_parquet(run / "truth" / "deletion_requests.parquet").person_id)
    leaks, by_source, collateral = set(), {}, {}
    for src, (view, key) in VIEWS.items():
        ids = set(con.execute(f"SELECT {key} FROM {view}").df()[key])
        l = links[(links.source == src) & links.record_id.isin(ids)]
        hit = l[l.person_id.isin(deleted_people)]
        by_source[src] = int(hit.person_id.nunique())
        leaks |= set(hit.person_id)
        if before is not None:
            gone = before[src] - ids
            g = links[(links.source == src) & links.record_id.isin(gone)]
            collateral[src] = int((~g.person_id.isin(deleted_people)).sum())
    out = {"deleted_people": len(deleted_people), "people_with_any_record_left": len(leaks),
           "by_source": by_source}
    if before is not None:
        out["innocent_records_removed"] = collateral
    return out


def snapshot(con) -> dict[str, set]:
    return {s: set(con.execute(f"SELECT {k} FROM {v}").df()[k]) for s, (v, k) in VIEWS.items()}


def review_queue_report(con, run_dir: str | Path) -> dict:
    """How much of the review queue is a real leftover of a deleted person."""
    run = Path(run_dir)
    deleted_people = set(pd.read_parquet(run / "truth" / "deletion_requests.parquet").person_id)
    links = _links(run).assign(unique_id=lambda d: d.source + ":" + d.record_id)
    q = con.execute("""SELECT DISTINCT r.person_key, i.unique_id FROM deletion_review r
                       JOIN identity_records i USING (person_key) WHERE r.reason = 'one_key_matches'""").df()
    q = q.merge(links[["unique_id", "person_id"]], on="unique_id", how="left")
    per = q.groupby("person_key")["person_id"].agg(lambda s: bool(s.isin(deleted_people).any()))
    return {"persons_in_queue": int(len(per)), "are_deleted_people": int(per.sum())}
