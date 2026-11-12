"""One scheduled sync of a live NYC Open Data dataset, recorded whether it succeeds or fails.

    python -m plumbline.ingest.open_data_daily            # what the daily GitHub job runs

Each run: read the saved cursor (docs/results/open_data/state/), pull only rows changed since then
(retries, privacy filter, keyset paging), load them under the reviewed contract
(sources/open_data/<name>.yaml) into a scratch warehouse, and append one line to
docs/results/open_data/runs.jsonl: rows, pages, retries by cause, seconds, rows loaded and quarantined, columns
that appeared, a bulk rewrite or a skipped reload if the publisher reloaded the table, whether the run finished or
stopped at its page budget (the next run continues from there), and how far the landed data is behind the
publisher: hours from the newest update landed here to the newest update the publisher has (asked each run).
That is not "hours since the newest row": a quiet weekend at the city is not lag, and a stuck sync is.
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
        contracts: Path = ROOT / "sources" / "open_data", since: str = "2026-11-01", max_pages: int = 60, page_size: int = 1000, reload_rows: int = 100_000,
        client_kw: dict | None = None, now=lambda: datetime.now(timezone.utc), publish_to: str | None = None,
        reconcile_budget: int = 2000, held_rows=None, reconcile_by: str = "work_permit", lake_dir: str | None = None) -> dict:
    from .connectors.socrata import source_newest, DEFAULT_HOST, SocrataClient, sync_dataset
    from .contracts import load_contracts
    from .engine import ingest
    import duckdb
    import os

    started = now()
    rec = {"run_at": started.strftime("%Y-%m-%dT%H:%M:%SZ"), "dataset": dataset, "title": TITLE, "name": name,
           "host": host or DEFAULT_HOST, "ok": False, "trigger": _trigger()}
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
            contract = contracts / f"{name}.yaml"
            accepted = set(load_contracts(contracts)[name].columns) if contract.exists() else set()
            st = sync_dataset(client, dataset, landing, name=name, since=since, max_pages=max_pages, page_size=page_size,
                              reload_rows=reload_rows, accepted=accepted)
            state = json.loads((landing / "_sync_state.json").read_text())
            rec.update(rows=st.orders, reread=getattr(st, "reread", 0), pages=st.pages, requests=st.requests, retries=st.retries,
                       retries_by_cause=dict(st.by_error), resumed=st.resumed, watermark=state["watermark"],
                       complete=state.get("in_progress") is None, dropped_personal=state["dropped_personal"],
                       drift=state["drift"], bulk_rewrite=state.get("bulk_rewrite"), capped=st.capped,
                       reload_skipped=st.reload_skipped)
            have = state["watermark"] if rec["complete"] else (state.get("in_progress") or {}).get("max_seen") or state["watermark"]
            rec["source_newest"] = source_newest(client, dataset, st)
            if have and rec["source_newest"]:
                gap = datetime.fromisoformat(rec["source_newest"]) - datetime.fromisoformat(have)
                rec["hours_behind_source"] = round(max(0.0, gap.total_seconds() / 3600), 1)
            if contract.exists() and reconcile_budget:
                _reconcile(rec, client, dataset, load_contracts(contracts)[name], landing, state, state_dir, st,
                           held_rows, publish_to, reconcile_budget, name, reconcile_by)
            if contract.exists() and (st.orders or rec.get("reconcile_rows")):
                cdir = Path(tmp) / "contracts"
                cdir.mkdir()
                shutil.copy(contract, cdir / contract.name)
                wh = Path(tmp) / "check.duckdb"
                ingest(Path(tmp) / "run", wh, load_contracts(cdir))
                with duckdb.connect(str(wh), read_only=True) as con:
                    rec["rows_loaded"] = con.execute(f"SELECT count(*) FROM current_{name}").fetchone()[0]
                    rec["rows_quarantined"] = con.execute("SELECT count(*) FROM quarantine WHERE source = ?", [name]).fetchone()[0]
                # A cloud publish that fails must not cost the source its cursor: the sync is recorded as done
                # and the failure as publish_error, which alerts and turns the job red (main returns 1).
                try:
                    rec["published"] = _publish(wh, name, publish_to, rec["run_at"])
                except Exception as e:
                    rec["published"], rec["publish_error"] = None, f"{type(e).__name__}: {e}"[:400]
                # The Iceberg copy: one snapshot per run, for time travel. Same rule as the cloud publish.
                try:
                    rec["lake"] = _lake(wh, name, lake_dir, rec["run_at"], lambda: _held_in_cloud(name, publish_to))
                except Exception as e:
                    rec["lake"], rec["lake_error"] = None, f"{type(e).__name__}: {e}"[:400]
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


def _reconcile(rec, client, dataset, contract, landing, state, state_dir, st, held_rows, target, budget, name, by) -> None:
    """Check the next slice of the rows the cloud warehouse holds against the source; land what changed.
    Without held rows (no cloud warehouse configured) nothing is claimed. A failure here is recorded, never
    fatal: the sync already succeeded and its cursor is saved."""
    from .reconcile import reconcile
    try:
        held = held_rows() if held_rows else _held_in_cloud(name, target)
        if not held:
            rec["reconcile"] = None
            return
        path = state_dir / "_reconcile.json"
        offset = json.loads(path.read_text())["next_offset"] if path.exists() else 0
        r = reconcile(client, dataset, contract, held, offset, budget, st, by)
        rows = r.pop("rows")
        if rows:
            cols = list(dict.fromkeys(list(state["columns"] or []) + [c for c in contract.columns if any(c in x for x in rows)]))
            from .connectors.socrata import _to_csv
            from .connectors.store_api import _atomic_write
            _atomic_write(landing / f"{name}_reconcile_{rec['run_at'].replace(':', '').replace('-', '')}.csv", _to_csv(rows, cols))
        rec["reconcile"], rec["reconcile_rows"] = r, len(rows)
        path.write_text(json.dumps({"next_offset": r["next_offset"], "held": r["held"]}))
    except Exception as e:
        rec["reconcile"] = {"error": f"{type(e).__name__}: {e}"[:300]}


def _held_in_cloud(name: str, target: str | None) -> list[dict] | None:
    import os
    target = target or ("md:plumbline" if os.environ.get("MOTHERDUCK_TOKEN") else None)
    if not target or target == "off":
        return None
    from ..products.publish import connect
    con, cat = connect(target)
    try:
        if not con.execute("SELECT count(*) FROM information_schema.tables WHERE table_catalog = ? AND table_name = ?",
                           [cat, f"current_{name}"]).fetchone()[0]:
            return None
        cur = con.execute(f'SELECT * FROM {cat}.main."current_{name}"')
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
    finally:
        con.close()


def _lake(warehouse: Path, name: str, lake_dir: str | None, run_at: str, seed=None) -> dict | None:
    """Append this run's rows to the feed's Iceberg table (lake/iceberg.py) when a lake folder is configured.
    The first time, the table is seeded from the cloud copy so it starts with every row held, not one run's."""
    import os
    lake_dir = lake_dir or os.environ.get("PLUMBLINE_FEED_LAKE")
    if not lake_dir:
        return None
    from ..lake.iceberg import Lake, feed_snapshot
    return feed_snapshot(warehouse, Lake(str(Path(lake_dir) / "warehouse"), Path(lake_dir) / "catalog.db"), name, run_at,
                         seed=seed)


def _publish(warehouse: Path, name: str, target: str | None, run_at: str) -> dict | None:
    """Upsert this run's rows into the cloud warehouse when a token is configured; otherwise None."""
    import os
    from ..products.publish import Table, publish
    if target == "off":                 # as in stream_daily: "off" turns publishing off (it once made a file named off)
        return None
    target = target or ("md:plumbline" if os.environ.get("MOTHERDUCK_TOKEN") else None)
    if not target:
        return None
    r = publish(warehouse, [Table(f"current_{name}", "source_row_id", "source_updated_at")], target, run=run_at)
    return {"target": r["target"], **{k: v for k, v in r["tables"][0].items() if k != "table"}}


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
    return 1 if r.get("publish_error") else 0


def _trigger() -> str:
    """What started this run: "schedule", "push" (code changed), "workflow_dispatch" (a person), or "local"."""
    import os
    return os.environ.get("GITHUB_EVENT_NAME", "local")


if __name__ == "__main__":
    sys.exit(main())

