"""One scheduled sync of a live NYC Open Data dataset, recorded whether it succeeds or fails.

    python -m plumbline.ingest.open_data_daily            # what the daily GitHub job runs

Each run: read the saved cursor (docs/results/open_data/state/), pull only rows changed since then
(retries, privacy filter, keyset paging), load them under the reviewed contract
(sources/open_data/<name>.yaml) into a scratch warehouse, and append one line to
docs/results/open_data/runs.jsonl: rows, pages, retries by cause, seconds, how stale the newest row is,
rows loaded and quarantined, columns that appeared, and a bulk rewrite if the publisher reloaded the table.
A failed run is recorded too, with the API's reason, so the history cannot look better than it was.

The landed rows themselves are not kept: this proves the pipeline runs on data nobody here controls; the
records stay with the city.
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "docs" / "results" / "open_data"
DATASET, NAME = "rbx6-tga4", "dob_now_permits"     # DOB NOW: Build, Approved Permits (updated daily)
TITLE = "DOB NOW: Build, Approved Permits"


def run(*, host: str | None = None, dataset: str = DATASET, name: str = NAME, out: Path = OUT,
        contracts: Path = ROOT / "sources" / "open_data", since: str = "2026-11-01", max_pages: int = 60, page_size: int = 1000,
        client_kw: dict | None = None, now=lambda: datetime.now(timezone.utc)) -> dict:
    from .connectors.socrata import DEFAULT_HOST, SocrataClient, sync_dataset
    from .contracts import load_contracts
    from .engine import ingest
    import duckdb
    import os

    started = now()
    rec = {"run_at": started.strftime("%Y-%m-%dT%H:%M:%SZ"), "dataset": dataset, "title": TITLE, "name": name,
           "host": host or DEFAULT_HOST, "ok": False}
    state_dir = out / "state" / name
    state_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    with tempfile.TemporaryDirectory() as tmp:
        landing = Path(tmp) / "run" / "sources" / name
        landing.mkdir(parents=True)
        if (state_dir / "_sync_state.json").exists():          # the cursor lives in the repo between runs
            shutil.copy(state_dir / "_sync_state.json", landing / "_sync_state.json")
        try:
            client = SocrataClient(host or DEFAULT_HOST, os.environ.get("SOCRATA_APP_TOKEN"), **(client_kw or {}))
            st = sync_dataset(client, dataset, landing, name=name, since=since, max_pages=max_pages, page_size=page_size)
            state = json.loads((landing / "_sync_state.json").read_text())
            rec.update(rows=st.orders, pages=st.pages, requests=st.requests, retries=st.retries,
                       retries_by_cause=dict(st.by_error), resumed=st.resumed, watermark=state["watermark"],
                       complete=state.get("in_progress") is None, dropped_personal=state["dropped_personal"],
                       drift=state["drift"], bulk_rewrite=state.get("bulk_rewrite"))
            if state["watermark"]:
                newest = datetime.fromisoformat(state["watermark"]).replace(tzinfo=timezone.utc)
                rec["hours_behind_source"] = round((started - newest).total_seconds() / 3600, 1)
            contract = contracts / f"{name}.yaml"
            if contract.exists() and st.orders:
                cdir = Path(tmp) / "contracts"
                cdir.mkdir()
                shutil.copy(contract, cdir / contract.name)
                wh = Path(tmp) / "check.duckdb"
                ingest(Path(tmp) / "run", wh, load_contracts(cdir))
                with duckdb.connect(str(wh), read_only=True) as con:
                    rec["rows_loaded"] = con.execute(f"SELECT count(*) FROM current_{name}").fetchone()[0]
                    rec["rows_quarantined"] = con.execute("SELECT count(*) FROM quarantine WHERE source = ?", [name]).fetchone()[0]
            elif not contract.exists():
                rec["note"] = f"no reviewed contract at sources/open_data/{name}.yaml yet: synced, not loaded"
            shutil.copy(landing / "_sync_state.json", state_dir / "_sync_state.json")
            rec["ok"] = True
        except Exception as e:                       # recorded, then re-raised so the job shows red
            rec["error"] = f"{type(e).__name__}: {e}"[:400]
            rec["seconds"] = round(time.perf_counter() - t0, 2)
            _append(out, rec)
            raise
    rec["seconds"] = round(time.perf_counter() - t0, 2)
    _append(out, rec)
    return rec


def _append(out: Path, rec: dict) -> None:
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "runs.jsonl", "a") as f:
        f.write(json.dumps(rec, sort_keys=True) + "\n")


HISTORY_URL = "https://raw.githubusercontent.com/AKilalours/plumbline/open-data-history/runs.jsonl"


def _parse(text: str) -> list[dict]:
    out = []
    for line in text.splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if isinstance(r, dict) and "run_at" in r and "ok" in r:
            out.append(r)
    return out


def history(out: Path = OUT, url: str | None = None, timeout: float = 4.0) -> list[dict]:
    """The run history: from the open-data-history branch the daily job writes, when url is given and
    reachable, else the local copy. A line that does not parse is skipped, never a crash on the page."""
    if url:
        import urllib.request
        try:
            with urllib.request.urlopen(url, timeout=timeout) as r:
                return _parse(r.read().decode("utf-8", "replace"))
        except Exception:
            pass
    p = out / "runs.jsonl"
    return _parse(p.read_text()) if p.exists() else []


def summary(runs: list[dict]) -> dict:
    ok = [r for r in runs if r.get("ok")]
    return {"runs": len(runs), "succeeded": len(ok), "rows": sum(r.get("rows", 0) for r in ok),
            "retries": sum(r.get("retries", 0) for r in ok), "first": runs[0]["run_at"] if runs else None,
            "last": runs[-1] if runs else None, "quarantined": sum(r.get("rows_quarantined", 0) or 0 for r in ok)}


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--max-pages", type=int, default=60, help="a cap per run; a larger change continues next run")
    a = ap.parse_args(argv)
    try:
        r = run(max_pages=a.max_pages)
    except Exception:
        traceback.print_exc()
        return 1
    print(json.dumps(r, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
