"""plumbline-synth: build one synthetic run.

    python -m plumbline.synth.cli --ref data/reference --out data/synth --n-people 100000 --seed 7
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import SynthConfig
from .generate import generate
from .reference import load_reference


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="plumbline-synth", description=__doc__.splitlines()[0])
    ap.add_argument("--ref", default="data/reference", help="directory written by scripts/fetch_reference.py")
    ap.add_argument("--out", default="data/synth")
    ap.add_argument("--n-people", type=int, default=100_000)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--allow-fixture", action="store_true",
                    help="permit the invented test reference data; never use for published numbers")
    ap.add_argument("--force", action="store_true", help="replace an existing run directory, everything in it")
    a = ap.parse_args(argv)

    ref = load_reference(a.ref)
    if ref.source != "real" and not a.allow_fixture:
        print(f"refusing: reference data in {a.ref} is '{ref.source}', not 'real'. "
              "Run scripts/fetch_reference.py, or pass --allow-fixture for a test run.", file=sys.stderr)
        return 2
    prefix = "" if ref.source == "real" else "FIXTURE_"
    run_dir = Path(a.out) / f"{prefix}seed{a.seed}_n{a.n_people}"
    if run_dir.exists() and not a.force:
        print(f"refusing: {run_dir} exists; pass --force to replace it", file=sys.stderr)
        return 2
    if run_dir.exists():
        # Replace, never write into: the store, its synced orders and the campaign are built from the
        # world, and an old one left next to a new world would be joined to it without complaint.
        import shutil
        shutil.rmtree(run_dir)
    m = generate(ref, SynthConfig(seed=a.seed, n_people=a.n_people), run_dir)
    print(json.dumps({"run_dir": str(run_dir), "fingerprint": m["fingerprint"], "counts": m["counts"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
