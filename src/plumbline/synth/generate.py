"""Run the whole generator and write one self-describing run directory.

    <run>/sources/...      what a pipeline is allowed to read
    <run>/truth/...        the answer key; only evaluation code may read it
    <run>/deliveries.json  every file with its logical date, arrival time, version, hash
    <run>/manifest.json    config, versions, counts and a content fingerprint

The fingerprint is computed from canonical CSV bytes of every table, never from parquet
bytes, because parquet files embed the writer's library version. Same seed and same
reference data therefore give the same fingerprint on any machine with the same
library versions; tests hold the generator to that.
"""
from __future__ import annotations

import hashlib
import json
import platform
from datetime import datetime, timezone
from pathlib import Path

import faker
import numpy as np
import pandas as pd

from .config import SynthConfig
from .emit import Emitted, emit_crm, emit_deletions, emit_email, emit_vendor
from .reference import Reference
from .rng import component_rngs
from .world import build_world


def canonical_bytes(df: pd.DataFrame) -> bytes:
    return df.to_csv(index=False, lineterminator="\n").encode()


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _for_csv(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for c in out.columns:
        if pd.api.types.is_datetime64_any_dtype(out[c]):
            out[c] = out[c].dt.strftime("%Y-%m-%d")
    return out


def generate(ref: Reference, cfg: SynthConfig, run_dir: str | Path) -> dict:
    run_dir = Path(run_dir)
    rngs = component_rngs(cfg.seed)
    world = build_world(ref, cfg, rngs)

    out = Emitted()
    customers = emit_crm(world, cfg, rngs["crm"], out)
    vendor_people = emit_vendor(world, cfg, rngs["vendor"], out)
    emit_email(world, cfg, rngs["email"], out, customers)
    observed = np.union1d(customers, vendor_people)
    deletion_truth = emit_deletions(world, cfg, rngs["privacy"], out, observed)

    hashes: dict[str, str] = {}
    deliveries = []
    for d in out.deliveries:
        path = run_dir / d.path
        path.parent.mkdir(parents=True, exist_ok=True)
        b = canonical_bytes(d.frame)
        path.write_bytes(b)
        h = _sha(b)
        hashes[d.path] = h
        deliveries.append({"source": d.source, "path": d.path, "logical_date": d.logical_date,
                           "delivered_at": d.delivered_at, "version": d.version, "rows": len(d.frame),
                           "sha256": h})

    damage = pd.DataFrame([r for log in out.damage for r in log.rows],
                          columns=["source", "record_id", "field", "kind"])
    truth = {
        "people": world.people, "households": world.households,
        "address_history": world.address_history, "job_history": world.job_history,
        "events": world.events, "record_links": pd.concat(out.links, ignore_index=True),
        "damage_log": damage, "deletion_requests": deletion_truth,
    }
    tdir = run_dir / "truth"
    tdir.mkdir(parents=True, exist_ok=True)
    for name, df in truth.items():
        flat = _for_csv(df)
        flat.to_parquet(tdir / f"{name}.parquet", index=False)
        hashes[f"truth/{name}"] = _sha(canonical_bytes(flat))

    fingerprint = _sha("\n".join(f"{k}:{hashes[k]}" for k in sorted(hashes)).encode())[:16]
    events = world.events["event_type"].value_counts().sort_index().to_dict()
    manifest = {
        "fingerprint": fingerprint,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "reference_source": ref.source,
        "config": cfg.to_dict(),
        "versions": {"python": platform.python_version(), "numpy": np.__version__,
                     "pandas": pd.__version__, "faker": faker.VERSION},
        "counts": {
            "people": len(world.people), "households": len(world.households),
            "tax_lots_available": len(ref.lots), "events": {k: int(v) for k, v in events.items()},
            "crm_customers": int(len(customers)), "vendor_people": int(len(vendor_people)),
            "deletion_requests": int(len(deletion_truth)), "damage_entries": int(len(damage)),
            "deliveries": len(deliveries),
        },
        "hashes": hashes,
    }
    (run_dir / "deliveries.json").write_text(json.dumps(deliveries, indent=2) + "\n")
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest
