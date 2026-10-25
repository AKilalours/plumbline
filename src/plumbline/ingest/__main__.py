"""python -m plumbline.ingest --run-dir data/synth/seed7_n100000 --warehouse data/warehouse.duckdb"""
from __future__ import annotations

import argparse
import json
import sys

import duckdb

from .checks import check_invariants
from .contracts import load_contracts
from .engine import ingest


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Load delivered files into the DuckDB warehouse.")
    ap.add_argument("--run-dir", default="data/synth/seed7_n100000")
    ap.add_argument("--warehouse", default="data/warehouse.duckdb")
    ap.add_argument("--contracts", default="sources")
    a = ap.parse_args(argv)
    contracts = load_contracts(a.contracts)
    report = ingest(a.run_dir, a.warehouse, contracts)
    con = duckdb.connect(a.warehouse, read_only=True)
    violations = {k: v for k, v in check_invariants(con, contracts).items() if v}
    print(json.dumps({"totals": report.totals(), "violations": violations}, indent=2))
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())
