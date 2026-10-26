"""Design, send (simulated) and read out the campaign experiment in one call, for the pipeline."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import duckdb

from ..synth.campaign import simulate_response
from .design import design, send_list
from .readout import readout


def run_experiment(warehouse: str | Path, run_dir: str | Path) -> dict:
    run_dir = Path(run_dir)
    with duckdb.connect(str(warehouse)) as con:
        plan = design(con)
        sends = send_list(con)
        sha = hashlib.sha256(sends.to_csv(index=False, lineterminator="\n").encode()).hexdigest()[:16]
        man = run_dir / "campaign" / "campaign_manifest.json"
        old = json.loads(man.read_text()).get("send_list_sha") if man.exists() else None
        if old != sha:                       # the audience changed: the old responses answer another list
            info = simulate_response(run_dir, sends)
            man.write_text(json.dumps({**info, "send_list_sha": sha}, indent=2) + "\n")
        r = readout(con, run_dir)
    return {"plan": plan, **r}


def summary(r: dict) -> dict:
    """The lines the pipeline prints."""
    up = r["uplift"]["incremental_buyers_per_1000_emails"]
    return {"inboxes": r["inboxes"], "sample_ratio_p": r["sample_ratio"]["p"],
            "attributed_share": r["attribution"]["attributed_share"],
            "effect": r["bought_within_30_days"]["effect"], "ci95": r["bought_within_30_days"]["ci95"],
            "corrected_effect": r["corrected_for_unattributed_orders"]["effect"],
            "true_effect": r["answer_key"]["true_effect_of_sending_to_this_list"],
            "truth_inside_corrected_ci": r["answer_key"]["inside_corrected_ci"],
            "uplift_model": r["uplift"]["chosen"],
            "buyers_per_1000_emails": {k: up[k] for k in ("everyone", "likeliest_buyers_30pct", "uplift_model_30pct",
                                                           "best_possible_30pct")},
            "trustworthy": r["trustworthy"]}
