"""Where every product comes from: deliveries, through the loader and identity, through every dbt model.

    python -m plumbline.products.lineage            # runs `dbt parse`, writes docs/results/lineage.json

dbt's own manifest gives the model graph and the tests on each model. Above dbt, the tables dbt reads are
written by this repo's Python; WRITERS says which module writes which table from which delivery, and a test
checks every one of those tables is a source dbt declares, so the two halves of the graph meet.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]

# delivery or upstream table -> table, and the module that writes it
WRITERS = [
    ("CRM files", "current_crm", "src/plumbline/ingest/engine.py"),
    ("Vendor files", "current_vendor", "src/plumbline/ingest/engine.py"),
    ("Email event files", "current_email", "src/plumbline/ingest/engine.py"),
    ("Store API", "current_store", "src/plumbline/ingest/connectors/store_api.py"),
    ("CRM files", "delivery_rows", "src/plumbline/ingest/engine.py"),
    ("Vendor files", "delivery_rows", "src/plumbline/ingest/engine.py"),
    ("Email event files", "delivery_rows", "src/plumbline/ingest/engine.py"),
    ("Store API", "delivery_rows", "src/plumbline/ingest/engine.py"),
    ("CRM files", "ingest_ledger", "src/plumbline/ingest/engine.py"),
    ("Vendor files", "ingest_ledger", "src/plumbline/ingest/engine.py"),
    ("Email event files", "ingest_ledger", "src/plumbline/ingest/engine.py"),
    ("Store API", "ingest_ledger", "src/plumbline/ingest/engine.py"),
    ("Privacy requests", "suppression", "src/plumbline/ingest/erasure.py"),
    ("NYC PLUTO (real)", "ref_property", "src/plumbline/ml/reference.py"),
    ("Census ACS (real)", "ref_zip_income", "src/plumbline/ml/reference.py"),
    ("current_crm", "identity_records", "src/plumbline/identity/pipeline.py"),
    ("current_vendor", "identity_records", "src/plumbline/identity/pipeline.py"),
    ("current_store", "identity_records", "src/plumbline/identity/pipeline.py"),
    ("identity_records", "identity_map", "src/plumbline/identity/pipeline.py"),
    ("current_store", "identity_store_profiles", "src/plumbline/identity/pipeline.py"),
]
DELIVERIES = {a for a, _, _ in WRITERS} - {b for _, b, _ in WRITERS}


def manifest(dbt_dir: Path = ROOT / "dbt") -> dict:
    """Parse the dbt project into a scratch folder and read its manifest (no warehouse is touched)."""
    with tempfile.TemporaryDirectory() as tmp:
        env = {**os.environ, "DBT_PROFILES_DIR": str(dbt_dir), "PLUMBLINE_WAREHOUSE": str(Path(tmp) / "none.duckdb"),
               "DBT_SEND_ANONYMOUS_USAGE_STATS": "false", "DO_NOT_TRACK": "1"}
        dbt = Path(sys.executable).with_name("dbt")
        subprocess.run([str(dbt) if dbt.exists() else "dbt", "parse", "--project-dir", str(dbt_dir), "--target-path", tmp,
                        "--log-path", tmp, "--quiet"], check=True, env=env, cwd=str(dbt_dir))
        return json.loads((Path(tmp) / "manifest.json").read_text())


def build(m: dict) -> dict:
    nodes, edges = {}, set()
    add = lambda name, **kw: nodes.setdefault(name, {"id": name, **kw})
    for d in DELIVERIES:
        add(d, kind="delivery", stage="Deliveries", written_by="", real=d.endswith("(real)"))
    for a, b, by in WRITERS:
        stage = "Identity" if b.startswith("identity") else "Reference" if b.startswith("ref_") else "Loader"
        add(b, kind="table", stage=stage, written_by=by)
        edges.add((a, b))
    tests = {}
    for n in m["nodes"].values():
        if n["resource_type"] == "test":
            for d in n.get("depends_on", {}).get("nodes", []):
                tests.setdefault(d.split(".")[-1], []).append(n["name"])
    for n in m["nodes"].values():
        if n["resource_type"] not in ("model", "seed"):
            continue
        name = n["name"]
        stage = ("Seeds" if n["resource_type"] == "seed" else "Staging" if name.startswith("stg_")
                 else "Intermediate" if name.startswith("int_") else "Products")
        add(name, kind=n["resource_type"], stage=stage, written_by=n["original_file_path"].replace("\\", "/"),
            description=(n.get("description") or "").strip(), schema=n["schema"])
        for d in n.get("depends_on", {}).get("nodes", []):          # a seed depends on nothing
            edges.add((d.split(".")[-1], name))
    for name, ts in tests.items():
        if name in nodes:
            nodes[name]["tests"] = sorted(set(ts))
    sources = sorted(s["name"] for s in m["sources"].values())
    return {"nodes": sorted(nodes.values(), key=lambda x: x["id"]), "edges": sorted([list(e) for e in edges]),
            "dbt_sources": sources, "stages": ["Deliveries", "Reference", "Loader", "Identity", "Seeds", "Staging", "Intermediate", "Products"]}


if __name__ == "__main__":
    out = ROOT / "docs" / "results" / "lineage.json"
    g = build(manifest())
    out.write_text(json.dumps(g, indent=1))
    print(f"{len(g['nodes'])} nodes, {len(g['edges'])} edges -> {out}")
