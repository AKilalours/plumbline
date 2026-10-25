"""python -m plumbline: the whole pipeline in order, so no step can be skipped by accident.

    python -m plumbline                       # store, ingest, identity, deletion
    python -m plumbline --score               # also score each step against the answer key
    python -m plumbline --fresh               # start from an empty warehouse

Steps: build the store (once per run), sync it through the faulty mock API, ingest every source,
resolve persons, apply deletions to whole persons, build the data products (dbt models and tests). Each step's own command still exists for
working on one stage at a time.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import duckdb


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m plumbline")
    ap.add_argument("--run-dir", default="data/synth/seed7_n100000")
    ap.add_argument("--warehouse", default="data/warehouse.duckdb")
    ap.add_argument("--contracts", default="sources")
    ap.add_argument("--score", action="store_true", help="score identity and deletion against the answer key")
    ap.add_argument("--fresh", action="store_true", help="delete the warehouse first")
    ap.add_argument("--no-store", action="store_true", help="skip the store API step")
    a = ap.parse_args(argv)
    run, wh = Path(a.run_dir), Path(a.warehouse)
    if not (run / "deliveries.json").exists():
        print(f"no synthetic run at {run}. Generate one first:\n"
              f"  python -m plumbline.synth.cli --ref data/reference --out {run.parent} --n-people 100000 --seed 7")
        return 2
    if a.fresh and wh.exists():
        wh.unlink()
    wh.parent.mkdir(parents=True, exist_ok=True)
    summary: dict = {}

    def step(name, fn):
        t = time.time()
        print(f"[{name}] ...", flush=True)
        out = fn()
        summary[name] = {"seconds": round(time.time() - t, 1), **(out or {})}
        print(f"[{name}] done in {summary[name]['seconds']} s", flush=True)

    if not a.no_store:
        from .ingest.connectors.store_api import StoreClient, sync_orders
        from .store.db import build_store
        from .store.server import Faults, MockStore
        if not (run / "store" / "orders.parquet").exists():
            step("store_build", lambda: {k: v for k, v in build_store(run).items() if k in ("orders", "fingerprint")})

        def sync():
            f = Faults(p_429=0.10, p_503=0.05, p_drop=0.05, retry_after=1, refund_every=20, seed=1)
            with MockStore.from_run(run, "local-mock-token", f) as s:
                st = sync_orders(StoreClient(s.url, "local-mock-token", base_delay=0.05), run / "sources" / "store")
            return {"orders_fetched": st.orders, "retries": st.retries, "resumed": st.resumed}
        step("store_sync", sync)

    from .ingest.checks import check_invariants
    from .ingest.contracts import load_contracts
    from .ingest.engine import ingest
    contracts = load_contracts(a.contracts)

    def do_ingest():
        rep = ingest(run, wh, contracts)
        with duckdb.connect(str(wh), read_only=True) as con:
            v = {k: n for k, n in check_invariants(con, contracts).items() if n}
        return {**{k: rep.totals()[k] for k in ("rows_in", "rows_loaded", "rows_quarantined", "deliveries_loaded")},
                "violations": v}
    step("ingest", do_ingest)
    if summary["ingest"]["violations"]:
        print(json.dumps(summary, indent=2))
        return 1

    from .identity.pipeline import resolve
    from .privacy.person_deletion import expand_deletions

    def do_identity():
        with duckdb.connect(str(wh)) as con:
            out = resolve(con)
            if a.score:
                from .identity.evaluate import score_warehouse
                sc = score_warehouse(con, run)
                out["accuracy"] = {k: sc["pairwise"][k] for k in ("precision", "recall", "f1")}
                out["email_events"] = sc["email_events"]
        out.pop("seconds", None)
        return out
    step("identity", do_identity)

    def do_privacy():
        with duckdb.connect(str(wh)) as con:
            if a.score:
                from .privacy.evaluate import deletion_report, snapshot
                before, snap = deletion_report(con, run), snapshot(con)
            out = expand_deletions(con, contracts)
            if a.score:
                after = deletion_report(con, run, snap)
                out["deleted_people_with_data_left"] = {"before": before["people_with_any_record_left"],
                                                        "after": after["people_with_any_record_left"]}
                out["innocent_records_removed"] = sum(after["innocent_records_removed"].values())
        return out
    step("person_deletion", do_privacy)

    def do_products():
        from .products.build import build
        out = build(wh)
        if a.score:
            from .products.evaluate import score_products
            with duckdb.connect(str(wh), read_only=True) as con:
                sc = score_products(con, run)
            out["current_address_correct"] = sc["attributes"]["current_address"]["correct_when_filled"]
            out["current_address_ceiling"] = sc["address_ceiling"]
            out["moves_for_segments"] = {k: sc["moves"]["used_for_segments"][k]
                                         for k in ("precision", "recall_of_detectable", "median_days_late")}
            out["work_personal_link_correct"] = sc["b2b2c"]["linked_and_both_correct_of_linkable"]
            out["household_f1"] = sc["households"]["f1"]
        return out
    step("products", do_products)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
