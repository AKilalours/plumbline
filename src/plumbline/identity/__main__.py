"""python -m plumbline.identity [--warehouse data/warehouse.duckdb] [--score data/synth/seed7_n100000]"""
from __future__ import annotations

import argparse
import json

import duckdb

from .pipeline import resolve


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="python -m plumbline.identity")
    ap.add_argument("--warehouse", default="data/warehouse.duckdb")
    ap.add_argument("--score", metavar="RUN_DIR", help="score the result against this synthetic run's answer key")
    a = ap.parse_args(argv)
    with duckdb.connect(a.warehouse) as con:
        out = resolve(con)
        if a.score:
            from .evaluate import score_warehouse
            out["score"] = score_warehouse(con, a.score)
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
