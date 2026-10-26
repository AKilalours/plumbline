"""Proposals and decisions: the only way a write tool runs. `con` is the agent's own database
(agent.duckdb), never the warehouse: the record of who approved what lives apart from the data it governs.

Rules, all enforced here and tested:
  - a write is first a proposal; nothing changes until someone decides
  - the decider must hold the tool's approver role, and must not be the person who asked
  - a decision is final: a proposal is decided once; executing it twice does nothing the second time
  - every step is in the audit log
"""
from __future__ import annotations

import json
import uuid

from . import audit
from .tools import TOOLS


class NotAllowed(PermissionError):
    pass


def propose(con, ctx, *, requested_by: str, skill: str, tool: str, args: dict, summary: str, channel: str = "cli") -> str:
    t = TOOLS[tool]
    if t.access != "write":
        raise ValueError(f"{tool} is a read tool; it needs no proposal")
    missing = [a for a in t.args if a not in args]
    if missing:
        raise ValueError(f"{tool} needs {missing}")
    audit.ensure(con)
    pid = "P" + uuid.uuid4().hex[:10]
    con.execute("INSERT INTO ops.proposals VALUES (?, now(), ?, ?, ?, ?, ?, ?, 'pending', NULL, NULL, NULL)",
                [pid, requested_by, skill, tool, json.dumps(args), summary, t.approver_role])
    # The summary can name the record (a person key) and stays in the proposals table, which needs it
    # to execute. The audit log gets the tool, the argument hash and the role: a regression test
    # found the person key in clear in the audit detail when the summary was copied there.
    audit.log(con, actor=requested_by, channel=channel, skill=skill, action="propose", tool=tool, args=args,
              proposal_id=pid, outcome="pending", detail={"approver_role": t.approver_role})
    return pid


def decide(con, ctx, proposal_id: str, *, decided_by: str, approve: bool, roles: dict[str, set[str]],
           channel: str = "cli") -> dict:
    audit.ensure(con)
    row = con.execute("SELECT tool, args, requested_by, approver_role, status, skill FROM ops.proposals WHERE id = ?",
                      [proposal_id]).fetchone()
    if not row:
        raise ValueError(f"no proposal {proposal_id}")
    tool, args, requested_by, role, status, skill = row
    if status in ("executed", "rejected", "failed"):
        audit.log(con, actor=decided_by, channel=channel, skill=skill, action="decide", tool=tool,
                  proposal_id=proposal_id, outcome=f"already_{status}")
        return {"proposal": proposal_id, "status": status, "note": "already decided; nothing done"}
    why = None
    if role not in roles.get(decided_by, set()):
        why = f"{decided_by} does not hold the {role} role"
    elif decided_by == requested_by:
        why = "the person who asked cannot approve their own request"
    if why:
        audit.log(con, actor=decided_by, channel=channel, skill=skill, action="decide", tool=tool,
                  proposal_id=proposal_id, outcome="refused", detail={"why": why})
        raise NotAllowed(why)
    if not approve:
        con.execute("UPDATE ops.proposals SET status = 'rejected', decided_by = ?, decided_at = now() WHERE id = ?",
                    [decided_by, proposal_id])
        audit.log(con, actor=decided_by, channel=channel, skill=skill, action="decide", tool=tool,
                  proposal_id=proposal_id, outcome="rejected")
        return {"proposal": proposal_id, "status": "rejected"}
    # Mark approved before running, so a crash during execution cannot leave it runnable twice.
    con.execute("UPDATE ops.proposals SET status = 'approved', decided_by = ?, decided_at = now() WHERE id = ? AND status = 'pending'",
                [decided_by, proposal_id])
    try:
        result = TOOLS[tool].run(ctx, json.loads(args))
        status, outcome = "executed", "executed"
    except Exception as e:                     # recorded, not swallowed: the decider sees it
        result, status, outcome = {"error": str(e)}, "failed", "failed"
    con.execute("UPDATE ops.proposals SET status = ?, result = ? WHERE id = ?", [status, json.dumps(result, default=str), proposal_id])
    audit.log(con, actor=decided_by, channel=channel, skill=skill, action="execute", tool=tool,
              proposal_id=proposal_id, outcome=outcome, detail={"result": result})
    return {"proposal": proposal_id, "status": status, "result": result}
