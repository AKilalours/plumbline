"""Data for the operations visuals (incidents, cost, snapshots, kit), built from the real run history.

Each function takes what the app already reads (run lines, the recoveries ledger, job cost lines) and returns
the dict its visual draws. No number is made here; the visuals only arrange them.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
REPO = "https://github.com/AKilalours/plumbline/blob/main/"


def _title(postmortem: str | None) -> str:
    if not postmortem or not (ROOT / postmortem).exists():
        return "A person stepped in"
    first = (ROOT / postmortem).read_text().splitlines()[0]
    return re.sub(r"^#\s*(Postmortem:\s*)?", "", first).strip()


def incidents(feeds: dict[str, tuple[str, list[dict]]], entries: list[dict]) -> dict:
    """feeds: label -> (feed name in the ledger, its run lines)."""
    out = []
    for label, (feed, runs) in feeds.items():
        if not runs:
            continue
        mine = [e for e in entries if e["feed"] == feed]
        out.append({"name": label,
                    "runs": [{"t": r["run_at"], "ok": bool(r.get("ok")), "trigger": r.get("trigger"),
                              "hand": r.get("trigger") == "workflow_dispatch"} for r in runs],
                    "retries": sum(int(r.get("retries") or 0) for r in runs),
                    "recoveries": [{"start": str(e["started"]).replace("+00:00", "Z"), "end": str(e["recovered"]).replace("+00:00", "Z"),
                                    "did": e.get("did", ""), "title": _title(e.get("postmortem")),
                                    "url": REPO + e["postmortem"] if e.get("postmortem") else None} for e in mine]})
    return {"feeds": out}


def cost(jobs: list[dict], feed_runs: dict[str, list[dict]], *, runs_per_day: int = 8) -> dict:
    from ..ingest.cost import USD_PER_MINUTE, summary
    s = summary(jobs, feed_runs, runs_per_day=runs_per_day)
    if s.get("measured"):
        avg = s["measured"]["billed_minutes_avg"]
    elif s.get("steps_only"):
        avg = -(-s["steps_only"]["seconds_per_run"] // 60)          # whole minutes, rounded up: a lower bound
    else:
        avg = 0
    return {"jobs": [{k: j[k] for k in ("run_at", "job_seconds", "billed_minutes", "list_usd")} for j in jobs[-40:]],
            "avg_minutes": float(avg), "usd_per_minute": USD_PER_MINUTE, "runs_per_day": runs_per_day,
            "steps_seconds": (s.get("steps_only") or {}).get("seconds_per_run")}


def snapshots(runs: list[dict]) -> dict:
    """One entry per run that committed an Iceberg snapshot (lake/iceberg.py feed_snapshot), oldest first."""
    out, last = [], None
    for r in sorted(runs, key=lambda r: r["run_at"]):
        lk = r.get("lake")
        if not isinstance(lk, dict) or lk.get("snapshot_id") in (None, last):
            continue
        last = lk["snapshot_id"]
        out.append({"run_at": r["run_at"], "rows": lk.get("rows", 0), "added": lk.get("appended", 0),
                    "seeded": lk.get("seeded", 0), "snapshots": lk.get("snapshots", 0)})
    return {"snaps": out}


def kit_code() -> tuple[str, int]:
    """The pages method of the worked example, as an engineer writes it."""
    text = (ROOT / "examples" / "sources" / "usgs_quakes.py").read_text()
    m = re.search(r"(class Quakes\(Source\):.*?)\n\n\ndef ", text, re.S)
    body = m.group(1) if m else text
    return body, len(body.splitlines())


def incident(run, title: str, risk: str, fmt) -> dict:
    """An agent-lab incident (app/scenarios.py Run) as phases to step through. `fmt` formats values as the text
    report does, so the visual and the report say the same thing."""
    kv = lambda d: [{"k": str(k).replace("_", " "), "v": fmt(v)} for k, v in (d or {}).items() if k != "check"]
    v, p, a = run.verification or {}, run.proposal or {}, run.approval or {}
    return {"id": run.id, "title": title, "status": "verified" if v.get("passed") else ("verification failed" if v else run.status),
            "opened": run.created_at, "check": (run.findings or {}).get("check"),
            "phases": [{"phase": e["phase"], "label": e["label"], "detail": e.get("detail") or "", "seconds": e["seconds"],
                        "at": e["at"]} for e in run.events],
            "found": kv(run.findings), "evidence": kv(run.evidence),
            "proposal": {"summary": p.get("summary"), "by": p.get("proposed_by"), "role": p.get("role")} if p else None,
            "approval": {"by": a.get("by"), "at": a.get("at"), "key": a.get("idempotency_key")} if a else None,
            "checks": kv(v.get("checks")),
            "before_after": [{"k": str(k).replace("_", " "), "before": b, "after": c} for k, (b, c) in (v.get("before_after") or {}).items()],
            "risk": risk}


def _frac(x):
    m = re.fullmatch(r"\s*(\d+)\s*/\s*(\d+)\s*", str(x or ""))
    return [int(m.group(1)), int(m.group(2))] if m else None


def scorecard(rows: list[tuple], model: str | None) -> dict:
    """rows: (feature, measured on, without a model, with the model), as the scorecard table had them."""
    return {"model": model or "not recorded yet",
            "rows": [{"feature": f, "measure": m, "without": str(b) if b is not None else "not run", "with": str(w) if w is not None else "not recorded",
                      "b": _frac(b), "w": _frac(w)} for f, m, b, w in rows]}
