"""python -m plumbline.lake publish: sync bronze to the Iceberg lake (see iceberg.py).

    export PLUMBLINE_LAKE_URI=s3://my-bucket/plumbline      # or a local directory (the default)
    export PLUMBLINE_S3_ENDPOINT=http://localhost:9000      # only for S3-compatible stores such as MinIO
    export AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=...  # never in a file in the repo
    python -m plumbline.lake publish --warehouse data/warehouse.duckdb
"""
from __future__ import annotations

import argparse
import json
import sys

from .iceberg import Lake, publish


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m plumbline.lake")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("publish")
    p.add_argument("--warehouse", default="data/warehouse.duckdb")
    p.add_argument("--catalog", default="data/lake/catalog.db")
    a = ap.parse_args(argv)
    print(json.dumps(publish(a.warehouse, Lake.from_env(a.catalog)), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
