"""python -m plumbline.ml [--warehouse data/warehouse.duckdb] [--run-dir data/synth/seed7_n100000]

Builds point-in-time feature snapshots, trains and evaluates the home-buyer model, writes its card,
and publishes scores only if the guardrails pass. Run after `python -m plumbline` (needs the products).
"""
from __future__ import annotations

import argparse
import json

from .pipeline import train_and_score


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="python -m plumbline.ml")
    ap.add_argument("--warehouse", default="data/warehouse.duckdb")
    ap.add_argument("--run-dir", default="data/synth/seed7_n100000")
    ap.add_argument("--models", default="data/models/home_buyer")
    a = ap.parse_args(argv)
    print(json.dumps(train_and_score(a.warehouse, a.run_dir, a.models), indent=2, default=str))


if __name__ == "__main__":
    main()
