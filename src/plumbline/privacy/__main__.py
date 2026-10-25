"""python -m plumbline.privacy [--warehouse data/warehouse.duckdb] [--score RUN_DIR]

Applies deletion requests to whole persons. Run after `python -m plumbline.identity`.
"""
from __future__ import annotations

import argparse
import json
import os

import duckdb

from ..ingest.contracts import load_contracts
from .person_deletion import expand_deletions
from .tombstones import DEV_KEY


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="python -m plumbline.privacy")
    ap.add_argument("--warehouse", default="data/warehouse.duckdb")
    ap.add_argument("--contracts", default="sources")
    ap.add_argument("--score", metavar="RUN_DIR", help="measure leaks and wrong deletions against this run's answer key")
    a = ap.parse_args(argv)
    if os.environ.get("PLUMBLINE_TOMBSTONE_KEY", DEV_KEY) == DEV_KEY:
        print("note: using the public development tombstone key; set PLUMBLINE_TOMBSTONE_KEY outside development")
    with duckdb.connect(a.warehouse) as con:
        if a.score:
            from .evaluate import deletion_report, review_queue_report, snapshot
            before, snap = deletion_report(con, a.score), snapshot(con)
        out = expand_deletions(con, load_contracts(a.contracts))
        if a.score:
            out["before"] = before
            out["after"] = deletion_report(con, a.score, snap)
            out["review_queue"] = review_queue_report(con, a.score)
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
