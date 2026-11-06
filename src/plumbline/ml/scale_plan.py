"""How the pipeline grows, stage by stage, and what moves off one machine first.

Only runs on the same machine are compared: 20,000, 100,000 and 300,000 people on a 2-core, 7 GB
cloud machine (the 1,000,000 run was on a 10-core laptop, so its times are not on the same clock).
Each stage is a fixed start-up cost plus a cost per person. A stage whose cost per person rises from
one step to the next grows faster than the data and is the first to need more than one machine. The
projection is an extrapolation on one small machine, not a measurement: it ranks which stage breaks
first; it does not say how long a cluster would take.

    python -m plumbline.ml.scale_plan        # writes docs/results/scale_plan.json
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
SAME_MACHINE = "2-core cloud, 7 GB"
# Warehouse file size of the full pipeline, measured on the scale runs (bytes).
WAREHOUSE_BYTES = {100_000: 192_425_984, 300_000: 518_533_120}
STAGES = {"store_build": "build the store (test harness)", "store_sync": "store API sync", "ingest": "file ingestion",
          "identity": "identity resolution", "person_deletion": "deletion", "products": "dbt data products",
          "model": "home-buyer model", "more_models": "income and seller models", "experiment": "experiment readout"}
# What each stage would move to past one machine, and why that tool (the stack Minerva names).
NEXT = {"identity": "Splink on Spark: blocking keeps comparisons near linear; the clustering is a graph job",
        "products": "dbt on Spark or Trino over Iceberg tables: the SQL stays, the engine changes",
        "ingest": "parallel loaders writing Iceberg; the file ledger and row hashes keep reruns safe",
        "store_sync": "one sync per shard of the keyspace; cursors already make each resumable",
        "model": "features computed in Spark, training on a sample; scoring is embarrassingly parallel",
        "more_models": "same as the home-buyer model",
        "person_deletion": "a join of requests against the identity map; partition by person key",
        "experiment": "an aggregate; trivially parallel", "store_build": "not part of production"}


def growth(runs: list[dict]) -> dict:
    """Per stage: a fixed cost plus a cost per person (least squares on the same-machine runs), and the
    marginal cost per 100,000 people on each step up. On small runs, start-up work (loading Splink,
    starting dbt, fitting a model) is most of the time, so a power-law exponent fitted there comes out
    near 0.5 and would promise that identity gets cheaper per person forever. It does not: what decides
    large scale is the marginal cost, and whether it rises from one step to the next."""
    same = sorted((r for r in runs if r.get("machine") == SAME_MACHINE and "timing" in r), key=lambda r: r["people"])
    n = np.array([r["people"] for r in same], dtype=float)
    out = {}
    for st in STAGES:
        t = np.array([r["timing"]["seconds"][st] for r in same], dtype=float)
        k, c = np.polyfit(n, t, 1)
        marg = [(t[i + 1] - t[i]) / (n[i + 1] - n[i]) * 1e5 for i in range(len(n) - 1)]
        out[st] = {"seconds": dict(zip([int(x) for x in n], [int(x) for x in t])),
                   "fixed_seconds": round(float(max(c, 0.0)), 1), "seconds_per_100k": round(float(k * 1e5), 2),
                   "marginal_per_100k": [round(m, 1) for m in marg],
                   "marginal_rising": bool(len(marg) > 1 and marg[-1] > 1.25 * marg[0])}
    return {"people": [int(x) for x in n], "stages": out}


def project(g: dict, people: int) -> dict:
    """Seconds per stage at `people`: fixed cost plus the latest marginal cost per person (an extrapolation)."""
    out = {}
    for st, v in g["stages"].items():
        top = max(v["seconds"])
        out[st] = v["seconds"][top] + max(v["marginal_per_100k"][-1], 0.0) / 1e5 * max(people - top, 0)
    return out


def plan() -> dict:
    runs = json.loads((ROOT / "docs" / "results" / "scale.json").read_text())["runs"]
    g = growth(runs)
    per_person = {n: b / n for n, b in WAREHOUSE_BYTES.items()}
    bpp = float(np.mean(list(per_person.values())))
    tb_people = int(1e12 / bpp)
    targets = {"1M": 1_000_000, "10M": 10_000_000, "100M": 100_000_000, "1 TB": tb_people}
    proj = {k: {st: round(s / 60, 1) for st, s in project(g, n).items() if st != "store_build"} for k, n in targets.items()}
    at_tb = proj["1 TB"]
    first = sorted((st for st in g["stages"] if st != "store_build"), key=lambda st: -at_tb[st])
    return {"machine": SAME_MACHINE, "growth": g, "bytes_per_person": round(bpp), "people_per_tb": tb_people,
            "targets": targets, "projected_minutes": proj,
            "order_to_move": [{"stage": st, "label": STAGES[st], "minutes_at_1tb_one_machine": at_tb[st],
                               "marginal_rising": g["stages"][st]["marginal_rising"], "next": NEXT[st]} for st in first],
            "caveat": "Fitted on three runs on one 2-core machine; beyond 300,000 people it is extrapolation, not measurement."}


if __name__ == "__main__":
    p = plan()
    (ROOT / "docs" / "results" / "scale_plan.json").write_text(json.dumps(p, indent=1))
    print(json.dumps({k: p[k] for k in ("bytes_per_person", "people_per_tb", "order_to_move")}, indent=1))
    print({k: round(sum(v.values())) for k, v in p["projected_minutes"].items()}, "total minutes")
