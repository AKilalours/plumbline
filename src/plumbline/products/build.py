"""Run the dbt project (models + tests) against a warehouse.

dbt runs in its own process. In-process, dbt-duckdb keeps its connection to the warehouse open after
the build, so the next stage could not open the file; a separate process also means no dbt state
leaks between builds made for different as_of dates.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

DBT_DIR = Path(__file__).resolve().parents[3] / "dbt"


class DbtFailed(RuntimeError):
    pass


def build(warehouse: str | Path, as_of: str | None = None, select: str | None = None) -> dict:
    target = Path(tempfile.mkdtemp(prefix="plumbline-dbt-"))
    args = ["build", "--project-dir", str(DBT_DIR), "--profiles-dir", str(DBT_DIR),
            "--target-path", str(target), "--log-path", str(target), "--quiet"]
    if as_of:
        args += ["--vars", json.dumps({"as_of": as_of})]
    if select:
        args += ["--select", select]
    env = {**os.environ, "PLUMBLINE_WAREHOUSE": str(Path(warehouse).resolve()), "DBT_SEND_ANONYMOUS_USAGE_STATS": "false"}
    proc = subprocess.run([sys.executable, "-c", "from dbt.cli.main import cli; cli()", *args],
                          env=env, capture_output=True, text=True)
    rr = target / "run_results.json"
    results = json.loads(rr.read_text())["results"] if rr.exists() else []
    failed = [r["unique_id"].split(".")[-1] if not r["unique_id"].startswith("test.") else r["unique_id"].split(".")[2]
              for r in results if r["status"] in ("error", "fail")]
    tests = [r for r in results if r["unique_id"].startswith("test.")]
    if proc.returncode != 0 or failed:
        raise DbtFailed(f"dbt build failed: {failed or (proc.stdout + proc.stderr)[-2000:]}")
    return {"models": sum(r["unique_id"].startswith("model.") for r in results),
            "tests": len(tests), "tests_passed": sum(r["status"] == "pass" for r in tests)}
