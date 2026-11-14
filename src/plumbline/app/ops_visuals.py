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


def row_flow(runs: list[dict]) -> dict:
    """Where every NYC row went, over the runs that recorded each count (rows_loaded appears on every run since
    the first). Sums checked: written + re-read + quarantined = what NYC sent; same + changed + gone = checked."""
    w = [r for r in runs if "rows_loaded" in r]
    S = lambda k: sum(int(r.get(k) or 0) for r in w)
    rc = [r["reconcile"] for r in w if isinstance(r.get("reconcile"), dict)]
    R = lambda k: sum(int(x.get(k) or 0) for x in rc)
    skipped = [r["reload_skipped"] for r in runs if isinstance(r.get("reload_skipped"), dict)]
    q = S("rows_quarantined")
    return {"runs": len(w), "since": w[0]["run_at"] if w else None,
            "sources": [
                {"key": "sent", "title": "rows NYC sent", "streams": [
                    {"key": "written", "label": "Written to the warehouse", "n": S("rows") - q,
                     "tell": "New or changed permits, landed, checked against the contract and loaded."},
                    {"key": "reread", "label": "Re-read at the cursor, already held", "n": S("reread"),
                     "tell": "Every run re-reads a few minutes back so nothing on the edge is missed; rows it already has add nothing."},
                    {"key": "quarantined", "label": "Quarantined, with the reason", "n": q,
                     "tell": "Rows that broke the contract: kept aside with why, never loaded."}]},
                {"key": "checked", "title": "held rows checked against NYC", "streams": [
                    {"key": "same", "label": "Still the same", "n": R("same"), "tell": "A rotating slice of what we hold, asked for again by id."},
                    {"key": "changed", "label": "Changed: landed again", "n": R("changed"),
                     "tell": "NYC changed a row without moving its update time; the reconcile found it and landed NYC's version."},
                    {"key": "gone", "label": "Gone from NYC: reported", "n": R("gone"), "tell": "Removed at the source; reported, and an alert above 1%."}]}],
            "skipped": skipped[0] if skipped else None}


def proof_cards(nyc_runs: list[dict], cdc: dict | None, onboarding: dict | None, now) -> list[dict]:
    """Claims a reviewer will doubt, each computed here from its measured source; misses shown as misses."""
    from ..ingest.slo import measure
    out = []
    gh = "https://github.com/AKilalours/plumbline/blob/main/"
    if cdc:
        db = sum(len(v) for v in cdc["database"].values())
        wh = sum(len(v) for v in cdc["current"].values())
        out.append({"key": "cdc", "title": "A crash mid-replication loses nothing",
                    "visual": {"type": "versus", "left": db, "left_label": "rows in Postgres", "right": wh, "right_label": "rows in the warehouse",
                               "same": bool(cdc.get("matches_database")), "headline": "row for row" if cdc.get("matches_database") else "they differ"},
                    "method": f"A real Postgres {cdc['postgres'].split()[0]}: copy, then {cdc['crash']['transactions']} transactions, a crash before the slot "
                              f"moved, a restart that read {cdc['crash']['read_again']} changes again. The warehouse's current rows are compared with "
                              f"the database's, value by value.", "href": gh + "docs/results/postgres_cdc.json"})
    if nyc_runs:
        m = measure(nyc_runs, now)
        mt = m["matches"]
        if mt["total"]:
            out.append({"key": "reconcile", "title": "Rows checked against NYC again",
                        "visual": {"type": "ring", "k": mt["good"], "n": mt["total"], "target": mt["target"],
                                   "note": f"target {mt['target']:.1%}: {'met' if mt['status'] == 'met' else 'missed'}", "miss": mt["status"] != "met"},
                        "method": "Every run asks NYC again for a slice of the rows held and compares them field by field. The miss "
                                  "is real: most differences were apartment numbers an old bug dropped, now landed again.", "href": gh + "docs/postmortems/2026-11-07-apartment-number-dropped.md"})
        fr = m["fresh"]
        out.append({"key": "fresh", "title": "Data within 26 hours of NYC",
                    "visual": {"type": "ring", "k": fr["good"], "n": fr["total"], "target": fr["target"],
                               "note": f"target {fr['target']:.0%}: {'met' if fr['status'] == 'met' else 'missed'}", "miss": fr["status"] != "met"},
                    "method": "Each hour since the feed began counts as fresh when the newest landed row is within 26 hours of NYC's newest "
                              "update, asked of NYC every run.", "href": gh + "docs/reliability/slo.md"})
        ru = m["runs"]
        out.append({"key": "runs", "title": "Runs that succeeded",
                    "visual": {"type": "ring", "k": ru["good"], "n": ru["total"], "target": ru["target"],
                               "note": f"target {ru['target']:.0%}: {'met' if ru['status'] == 'met' else 'missed, budget spent'}", "miss": ru["status"] != "met"},
                    "method": "Two failed runs on the first day (an unpinned DuckDB) spent the whole 28-day budget. Every run since succeeded.",
                    "href": gh + "docs/postmortems/2026-11-07-motherduck-duckdb.md"})
        first_skip = next((r for r in nyc_runs if isinstance(r.get("reload_skipped"), dict)), None)
        if first_skip:
            stuck = [r for r in nyc_runs if r["run_at"] < first_skip["run_at"] and int(r.get("pages") or 0) >= 60][-4:]
            if stuck:
                avg = round(sum(float(r["seconds"]) for r in stuck) / len(stuck))
                out.append({"key": "reload", "title": "A publisher reload, skipped not re-read",
                            "visual": {"type": "versus", "left": avg, "left_label": f"seconds a run took, stuck ({len(stuck)} runs)",
                                       "right": round(float(first_skip["seconds"])), "right_label": "seconds after the fix", "lower_better": True},
                            "method": f"NYC re-stamped {first_skip['reload_skipped']['rows']:,} rows with one time. The old connector re-read the same 60 "
                                      f"pages every run. The fix counts the block, skips it, and landed the {first_skip['rows']:,} real changes after it.",
                            "href": gh + "docs/postmortems/2026-11-10-nyc-reload-stall.md"})
    if onboarding:
        es = onboarding["engineers"]
        out.append({"key": "onboard", "title": "Engineers who did not build it add a source",
                    "visual": {"type": "dots", "groups": [{"label": f"{e['source']}, {e['minutes']} min", "ok": sum(e["verify"]), "n": len(e["verify"])} for e in es]},
                    "method": "Each followed the guide on a real API and ran verify: lands, re-read adds nothing, a crash loses nothing, "
                              "a cursor is kept. Both used an AI assistant; each found bugs, all fixed with tests.",
                    "href": gh + "docs/results/onboarding_second_engineer.md"})
    return out


# ------------------------------------------------------------------ "Try to break it": each attack, as it really went
FIELD = {"apt_condo_no_s": "apartment number"}       # NYC column names, as a reader would say them
CHECKS = ["lands", "re-read adds nothing", "a crash loses nothing", "a cursor is kept"]


def break_it(nyc_runs: list[dict], cdc: dict | None, onboarding: dict | None, now) -> list[dict]:
    """The attacks a reviewer would try, each answered by what was recorded: the Postgres crash test, the NYC run
    history (reload, reconcile, freshness, failed runs) and the two engineers' verify results. `held` says honestly
    whether the attack failed (True), worked before a fix (\"fixed\"), or worked (False)."""
    from ..ingest.slo import fresh_hours, measure
    gh = "https://github.com/AKilalours/plumbline/blob/main/"
    out = []
    if cdc:
        ch = [{"op": c["op"], "lsn": c["lsn_text"], "xid": c["xid"], "table": c["source"].replace("pg_", ""), "id": c["id"],
               "v": c.get("email") or c.get("plan") if c["source"] == "pg_customers" else c.get("status")} for c in cdc["changes"]]
        out.append({"key": "cdc", "attack": "Crash the replication", "icon": "💥", "held": bool(cdc.get("matches_database")),
                    "question": "Can a crash lose a database change?",
                    "postgres": cdc["postgres"].split()[0], "slot": cdc["slot_point"], "copied": sum(cdc["copied"].values()),
                    "changes": ch, "read_again": cdc["crash"]["read_again"], "transactions": cdc["crash"]["transactions"],
                    "database": cdc["database"], "warehouse": cdc["current"],
                    "verdict": "row for row, value for value" if cdc.get("matches_database") else "they differ",
                    "href": gh + "docs/results/postgres_cdc.json"})
    if nyc_runs:
        runs = sorted(nyc_runs, key=lambda r: r["run_at"])
        skip = next((r for r in runs if isinstance(r.get("reload_skipped"), dict)), None)
        if skip:
            stuck = [r for r in runs if r["run_at"] < skip["run_at"] and int(r.get("pages") or 0) >= 60][-4:]
            out.append({"key": "reload", "attack": "Re-stamp the whole table", "icon": "🔁", "held": "fixed",
                        "question": "NYC gives every row one new update time. Does the feed choke?",
                        "block": skip["reload_skipped"]["rows"], "stamp": skip["reload_skipped"]["timestamp"],
                        "before": [{"t": r["run_at"], "pages": int(r["pages"]), "seconds": round(float(r["seconds"]), 1),
                                    "rows": int(r.get("rows") or 0)} for r in stuck],
                        "after": {"t": skip["run_at"], "pages": int(skip.get("pages") or 0), "seconds": round(float(skip["seconds"]), 1),
                                  "rows": int(skip.get("rows") or 0)},
                        "href": gh + "docs/postmortems/2026-11-10-nyc-reload-stall.md"})
        rc = [r for r in runs if isinstance(r.get("reconcile"), dict) and r["reconcile"].get("checked")]
        if rc:
            sl = [{"t": r["run_at"], "checked": int(r["reconcile"]["checked"]), "changed": int(r["reconcile"].get("changed") or 0),
                   "gone": int(r["reconcile"].get("gone") or 0), "fields": {FIELD.get(k, k.replace("_", " ")): v for k, v in (r["reconcile"].get("fields") or {}).items()}} for r in rc]
            out.append({"key": "quiet", "attack": "Edit a row without telling", "icon": "✏️", "held": True,
                        "question": "NYC changes a row but not its update time. Does anyone notice?",
                        "slices": sl, "href": gh + "docs/postmortems/2026-11-07-apartment-number-dropped.md"})
        hs = fresh_hours(runs, now)
        if hs:
            out.append({"key": "stale", "attack": "Let the data go stale", "icon": "⏱", "held": all(h["good"] for h in hs),
                        "question": "Is there an hour when what we hold was over 26 hours behind NYC?",
                        "promise": 26, "hours": hs, "href": gh + "docs/reliability/slo.md"})
        m = measure(runs, now)["runs"]
        out.append({"key": "fail", "attack": "Make a run fail", "icon": "🧨", "held": m["status"] == "met",
                    "question": "Do runs fail, and does the feed recover?", "target": m["target"],
                    "runs": [{"t": r["run_at"], "ok": bool(r.get("ok")), "seconds": round(float(r.get("seconds") or 0), 1),
                              "rows": int(r.get("rows") or 0), "error": (str(r.get("error"))[:90] if r.get("error") else None),
                              "trigger": r.get("trigger")} for r in runs],
                    "href": gh + "docs/postmortems/2026-11-07-motherduck-duckdb.md"})
    if onboarding:
        out.append({"key": "stranger", "attack": "Hand it to a stranger", "icon": "🧑‍💻",
                    "held": all(all(e["verify"]) for e in onboarding["engineers"]),
                    "question": "Can an engineer who never saw the code add a source?", "checks": CHECKS,
                    "engineers": [{"who": e["who"], "source": e["source"], "minutes": e["minutes"], "verify": e["verify"],
                                   "guide": e.get("guide", "")} for e in onboarding["engineers"]],
                    "note": onboarding.get("note", ""), "href": gh + "docs/results/onboarding_second_engineer.md"})
    return out


def oncall(run, title: str, area: str, risk: str, fmt) -> dict:
    """An incident for the on-call phone: the incident() phases plus the headline number the alert shows, and the
    refusal the real approval rule (scenarios.approve) gives when the proposer tries to approve its own fix."""
    import copy

    from . import scenarios as SC
    d = {**incident(run, title, risk, fmt), "area": area}
    f = {k: v for k, v in (run.findings or {}).items() if k != "check" and isinstance(v, (int, float)) and not isinstance(v, bool)}
    d["headline"] = {"k": next(iter(f)).replace("_", " "), "v": next(iter(f.values()))} if f else None
    d["refusal"] = None
    if run.proposal:
        c = copy.deepcopy(run)
        c.status, c.approval = "proposed", {}
        try:
            SC.approve(c, run.proposal.get("proposed_by"), {run.proposal.get("proposed_by"): {run.proposal.get("role")}})
        except SC.NotAllowed as e:
            d["refusal"] = str(e)
        except Exception:  # noqa: BLE001 - a record missing a field shows no refusal rather than breaking the page
            pass
    return d
