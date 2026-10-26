"""The tools the agent may use. Read tools open the warehouse read-only. Write tools never run
directly: the agent can only propose them, and approvals.py runs them after a person with the
required role approves. Tool output carries no names, emails or phones: person_key at most."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import duckdb


@dataclass(frozen=True)
class Tool:
    name: str
    access: str                     # read | write
    description: str
    run: Callable
    approver_role: str | None = None
    args: tuple[str, ...] = ()


def _ro(ctx):
    return duckdb.connect(str(ctx.warehouse), read_only=True)


def _table_exists(con, schema, table) -> bool:
    return bool(con.execute("SELECT 1 FROM information_schema.tables WHERE table_schema = ? AND table_name = ?",
                            [schema, table]).fetchone())


# ----------------------------------------------------------------- read tools
def ingest_status(ctx, args) -> dict:
    from ..ingest.checks import check_invariants
    from ..ingest.contracts import load_contracts
    with _ro(ctx) as con:
        by = con.execute("""SELECT source, count(*) FILTER (WHERE status = 'loaded') AS loaded,
                                   count(*) FILTER (WHERE status = 'rejected') AS rejected,
                                   max(loaded_at) AS last_load
                            FROM ingest_ledger GROUP BY 1 ORDER BY 1""").df()
        rejected = con.execute("SELECT source, path, note FROM ingest_ledger WHERE status = 'rejected' ORDER BY loaded_at DESC LIMIT 10").df()
        quarantine = con.execute("SELECT source, reason, count(*) AS n FROM quarantine GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 10").df()
        violations = {k: v for k, v in check_invariants(con, load_contracts(ctx.contracts)).items() if v}
    return {"deliveries": by.to_dict("records"), "rejected_files": rejected.to_dict("records"),
            "quarantine_top_reasons": quarantine.to_dict("records"), "invariant_violations": violations,
            "runbook": "docs/runbooks/ingestion.md"}


def deletion_review_queue(ctx, args) -> dict:
    with _ro(ctx) as con:
        if not _table_exists(con, "main", "deletion_review"):
            return {"open": 0, "items": []}
        rows = con.execute("""SELECT person_key, reason, count(*) AS tombstones, min(logged_at) AS first_queued
                              FROM deletion_review GROUP BY 1, 2 ORDER BY first_queued, person_key""").df()
    done = set()
    if ctx.agent_con is not None:      # cases already closed by an approved decision
        from . import audit
        audit.ensure(ctx.agent_con)
        done = {r[0] for r in ctx.agent_con.execute(
            """SELECT json_extract_string(args, '$.person_key') FROM ops.proposals
               WHERE tool = 'resolve_deletion_review' AND status = 'executed'""").fetchall()}
    rows = rows[~rows.person_key.isin(done)]
    return {"open": int(len(rows)), "items": rows.to_dict("records"),
            "runbook": "docs/runbooks/ingestion.md#6-the-deletion-review-queue-is-not-empty"}


def model_status(ctx, args) -> dict:
    with _ro(ctx) as con:
        if not _table_exists(con, "ml", "model_runs"):
            return {"model": "home_buyer", "runs": 0}
        last = con.execute("""SELECT model_version, as_of, run_at, published, scored, not_scored, checks
                              FROM ml.model_runs ORDER BY run_at DESC LIMIT 1""").df().iloc[0]
        checks = json.loads(last.checks)
    failed = [c["name"] for c in checks if not c["passed"]]
    warn = [c for c in checks if c["name"] == "input_drift_psi"]
    return {"model": "home_buyer", "version": last.model_version, "scored_as_of": str(last.as_of)[:10],
            "published": bool(last.published), "scored": int(last.scored), "not_scored": int(last.not_scored),
            "failed_checks": failed, "drift": warn[0]["value"] if warn else None,
            "card": "docs/model_card_home_buyer.md"}


def data_products_status(ctx, args) -> dict:
    with _ro(ctx) as con:
        out = {}
        for t in ("dim_person", "dim_household", "fct_life_events", "mart_activation_audience"):
            if _table_exists(con, "products", t):
                out[t] = con.execute(f"SELECT count(*) FROM products.{t}").fetchone()[0]
        if "mart_activation_audience" in out:
            out["contactable"] = dict(zip(["email", "phone", "mail"], con.execute(
                "SELECT sum(email_ok::int), sum(phone_ok::int), sum(mail_ok::int) FROM products.mart_activation_audience").fetchone()))
    return out


def check_doc_claims(ctx, args) -> dict:
    from .claims import check
    return {"stale": check(ctx)}


# ----------------------------------------------------------------- write tools (proposal only)
def resolve_deletion_review(ctx, args) -> dict:
    """decision 'delete': remove every record of the person; 'keep': record that it was reviewed."""
    from ..ingest.contracts import load_contracts
    from ..privacy.person_deletion import delete_persons
    pk, decision = args["person_key"], args["decision"]
    if decision not in ("delete", "keep"):
        raise ValueError("decision must be delete or keep")
    with duckdb.connect(str(ctx.warehouse)) as con:
        if not con.execute("SELECT 1 FROM deletion_review WHERE person_key = ?", [pk]).fetchone():
            raise ValueError(f"{pk} is not in the deletion review queue")
        if decision == "keep":
            return {"person_key": pk, "decision": "keep"}
        return {"person_key": pk, "decision": "delete",
                **delete_persons(con, load_contracts(ctx.contracts), [pk], reason=f"review:{pk}")}


def update_doc(ctx, args) -> dict:
    """Replace one exact passage in a document. Refuses unless the old text occurs exactly once, so an
    edit made by someone else in the meantime cannot be overwritten."""
    path = (Path(ctx.repo) / args["path"]).resolve()
    if not str(path).startswith(str(Path(ctx.repo).resolve())) or path.suffix != ".md":
        raise ValueError("update_doc may only edit Markdown files inside the repository")
    text = path.read_text()
    if text.count(args["old"]) != 1:
        raise ValueError(f"the passage to replace occurs {text.count(args['old'])} times in {args['path']}; expected exactly once")
    path.write_text(text.replace(args["old"], args["new"]))
    return {"path": args["path"], "replaced_chars": len(args["old"]), "new_chars": len(args["new"])}


def access_record(ctx, args) -> dict:
    from . import access
    con = ctx.agent_con
    access.ensure(con, ctx.roles)
    q = "SELECT user_id, role, granted_by, granted_at, revoked_by, revoked_at FROM ops.access"
    rows = con.execute(q + (" WHERE user_id = ?" if args.get("user") else "") + " ORDER BY granted_at",
                       [args["user"]] if args.get("user") else []).df()
    active = rows[rows.revoked_at.isna()]
    return {"active": active[["user_id", "role", "granted_by", "granted_at"]].to_dict("records"),
            "history_rows": int(len(rows)), "runbook": "docs/runbooks/agent.md"}


def _decider(args) -> str:
    return args.get("_decided_by", "unknown")


def grant_role(ctx, args) -> dict:
    from . import access
    return access.grant(ctx.agent_con, args["user"], args["role"], _decider(args), args.get("_proposal"))


def revoke_role(ctx, args) -> dict:
    from . import access
    return access.revoke(ctx.agent_con, args["user"], [args["role"]], _decider(args), args.get("_proposal"))


def offboard_person(ctx, args) -> dict:
    """Remove every role a person holds and reject their open requests, in one approved step."""
    from . import access
    held = sorted(access.current(ctx.agent_con).get(args["user"], set()))
    out = access.revoke(ctx.agent_con, args["user"], held, _decider(args), args.get("_proposal"))
    n = ctx.agent_con.execute("""UPDATE ops.proposals SET status = 'rejected', decided_by = ?, decided_at = now(),
                                 result = '{"reason": "requester offboarded"}' WHERE requested_by = ? AND status = 'pending'
                                 RETURNING id""", [_decider(args), args["user"]]).fetchall()
    return {**out, "open_requests_closed": len(n)}


TOOLS = {t.name: t for t in [
    Tool("ingest_status", "read", "Loads per source, rejected files, quarantine reasons, invariant violations.", ingest_status),
    Tool("deletion_review_queue", "read", "Open deletion-review cases (person keys and reasons only).", deletion_review_queue),
    Tool("model_status", "read", "Latest home-buyer model run: version, published, failed checks, drift.", model_status),
    Tool("data_products_status", "read", "Row counts of the data products and contactable people per channel.", data_products_status),
    Tool("check_doc_claims", "read", "Checks documented numbers against the warehouse; lists stale passages.", check_doc_claims),
    Tool("resolve_deletion_review", "write", "Close a deletion-review case: delete the person or keep them.",
         resolve_deletion_review, approver_role="privacy", args=("person_key", "decision")),
    Tool("update_doc", "write", "Replace one exact passage in a Markdown document.", update_doc,
         approver_role="docs", args=("path", "old", "new")),
    Tool("access_record", "read", "Who holds which role now, and how many grants and removals are on record.", access_record),
    Tool("grant_role", "write", "Give a person a role.", grant_role, approver_role="admin", args=("user", "role")),
    Tool("revoke_role", "write", "Remove a role from a person.", revoke_role, approver_role="admin", args=("user", "role")),
    Tool("offboard_person", "write", "Remove every role a person holds and close their open requests.", offboard_person,
         approver_role="admin", args=("user",)),
]}

PERSON_KEY = re.compile(r"\bPK[0-9a-f]{12}\b")
