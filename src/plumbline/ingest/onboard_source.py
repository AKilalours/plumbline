"""Onboard a source nobody has described, end to end, and time every step.

    python -m plumbline.ingest.onboard_source rbx6-tga4 --name dob_now_permits --since 2026-11-01

1. sync      the first delivery through the Socrata connector (retries, cursor, privacy filter)
2. draft     a contract from what arrived: types, privacy, key (the model when one is configured,
             else the rules); the key and version columns are the connector's own row id and time
3. validate  the draft with the same loader every contract goes through
4. load      the first delivery under the draft into a scratch warehouse: rows kept, rows quarantined

"Time to onboard a new source" is the number the ingestion role owns; this measures it. The draft is
written to sources/drafts/ for a person to read; it is never applied to the real warehouse on its own.
The result goes to docs/results/onboarding_time.json.
"""
from __future__ import annotations

import json
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import yaml

ROOT = Path(__file__).resolve().parents[3]


def onboard(dataset: str, name: str, *, host: str, since: str | None, workdir: Path, drafts: Path,
            app_token: str | None = None, page_size: int = 1000, max_pages: int | None = 5,
            client_kw: dict | None = None) -> dict:
    from ..ai.onboard import Draft, heuristic_draft, model_draft, profile, to_contract, validate
    from ..ai.runtime import Runtime
    from .connectors.socrata import SocrataClient, sync_dataset
    from .contracts import load_contracts
    from .engine import ingest

    t, steps = time.perf_counter(), {}
    run = workdir / "run"
    landing = run / "sources" / name
    st = sync_dataset(SocrataClient(host, app_token, **(client_kw or {})), dataset, landing, name=name,
                      page_size=page_size, since=since, max_pages=max_pages)
    state = json.loads((landing / "_sync_state.json").read_text())
    steps["sync"] = time.perf_counter() - t

    t = time.perf_counter()
    first = sorted(landing.glob("*.csv"))[0]
    prof = profile(first)
    rt = Runtime.from_env()
    d, _ = model_draft(rt, prof) if rt.mode == "live" else (None, None)
    drafted_by = "model" if d else "rules"
    d = d or heuristic_draft(prof)
    cols = {c.name: c for c in d.columns}           # the connector knows the key and the version column
    cols["source_row_id"] = cols["source_row_id"].model_copy(update={"type": "string", "required": True})
    cols["source_updated_at"] = cols["source_updated_at"].model_copy(update={"type": "timestamp", "required": True})
    d = Draft(key=["source_row_id"], latest_by=["source_updated_at"], columns=list(cols.values()))
    steps["draft"] = time.perf_counter() - t

    t = time.perf_counter()
    why = validate(d, name, Path(tempfile.mkdtemp()))
    steps["validate"] = time.perf_counter() - t
    if why:
        raise ValueError(f"draft rejected by the contract loader: {why}")
    drafts.mkdir(parents=True, exist_ok=True)
    contract = to_contract(d, name, f"sources/{name}/*.csv")
    contract["kind"] = "api"
    (drafts / f"{name}.yaml").write_text(f"# Drafted by {drafted_by} from {dataset}; read before moving to sources/.\n"
                                         + yaml.safe_dump(contract, sort_keys=False))

    t = time.perf_counter()
    cdir = workdir / "contracts"
    cdir.mkdir(exist_ok=True)
    (cdir / f"{name}.yaml").write_text(yaml.safe_dump(contract, sort_keys=False))
    wh = workdir / "first_delivery.duckdb"
    ingest(run, wh, load_contracts(cdir))
    with duckdb.connect(str(wh), read_only=True) as con:
        kept = con.execute(f"SELECT count(*) FROM current_{name}").fetchone()[0]
        q = con.execute("SELECT reason, count(*) FROM quarantine WHERE source = ? GROUP BY 1 ORDER BY 2 DESC", [name]).fetchall()
    steps["load"] = time.perf_counter() - t

    return {"dataset": dataset, "name": name, "host": host, "measured_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "seconds": {k: round(v, 2) for k, v in steps.items()}, "total_seconds": round(sum(steps.values()), 2),
            "rows_landed": st.orders, "requests": st.requests, "retries": st.retries, "rows_loaded": int(kept),
            "rows_quarantined": int(sum(n for _, n in q)), "quarantine_reasons": {r: int(n) for r, n in q[:5]},
            "columns": len(contract["columns"]), "drafted_by": drafted_by,
            "dropped_personal": state["dropped_personal"], "drift": state["drift"],
            "unsure": [f"{c.name}: {c.unsure}" for c in d.columns if c.unsure]}


def main(argv: list[str] | None = None) -> int:
    import argparse
    import os
    from .connectors.socrata import DEFAULT_HOST
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("dataset")
    ap.add_argument("--name", required=True)
    ap.add_argument("--since", default=None, help="rows updated on or after this date (first sync)")
    ap.add_argument("--host", default=DEFAULT_HOST)
    ap.add_argument("--max-pages", type=int, default=5, help="the first delivery is a sample; the full backfill runs later")
    ap.add_argument("--out", default=str(ROOT / "docs" / "results" / "onboarding_time.json"))
    a = ap.parse_args(argv)
    with tempfile.TemporaryDirectory() as tmp:
        r = onboard(a.dataset, a.name, host=a.host, since=a.since, workdir=Path(tmp), drafts=ROOT / "sources" / "drafts",
                    app_token=os.environ.get("SOCRATA_APP_TOKEN"), max_pages=a.max_pages)
    Path(a.out).write_text(json.dumps(r, indent=1))
    print(json.dumps(r, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
