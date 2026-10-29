"""The agent: route a message to a skill, run read tools, propose write tools, answer from the
documents, and log every step. Slack, the command line and the tests all call handle()."""
from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import duckdb
import yaml

from . import approvals, audit
from .answer import answer
from .knowledge import Index
from .llm import ChatModel
from .skills import load as load_skills, route
from .tools import TOOLS

APPROVE = re.compile(r"^\s*(approve|reject)\s+(P[0-9a-f]{10})\s*$", re.I)
# Anything else that starts with approve or reject is a decision with a bad id, never a question
# for the docs: answering it from the runbook leaves the person thinking they approved something.
LOOKS_LIKE_A_DECISION = re.compile(r"^\s*(approve|reject)\b", re.I)


@dataclass
class Context:
    warehouse: Path
    repo: Path
    contracts: Path
    agent_db: Path
    roles: dict[str, set[str]] = field(default_factory=dict)
    run_dir: Path | None = None
    model: ChatModel | None = None
    runtime: object | None = None          # plumbline.ai.Runtime: the model picks tools when set
    last_planner: str | None = None        # "model" or "keywords": who chose the tool for the last message
    agent_con: duckdb.DuckDBPyConnection | None = None
    _index: Index | None = None
    _skills: dict | None = None

    @classmethod
    def from_env(cls, repo=".", warehouse="data/warehouse.duckdb", agent_db="data/agent.duckdb",
                 run_dir="data/synth/seed7_n100000") -> "Context":
        repo = Path(repo)
        roles_file = Path(os.environ.get("PLUMBLINE_ROLES", repo / "ops" / "roles.yaml"))
        roles = {}
        if roles_file.exists():
            roles = {u: set(r) for u, r in (yaml.safe_load(roles_file.read_text()).get("users") or {}).items()}
        return cls(Path(warehouse), repo, repo / "sources", Path(agent_db), roles, Path(run_dir), ChatModel.from_env())

    def open(self) -> "Context":
        if self.agent_con is None:
            from . import access
            self.agent_db.parent.mkdir(parents=True, exist_ok=True)
            self.agent_con = duckdb.connect(str(self.agent_db))
            audit.ensure(self.agent_con)
            access.ensure(self.agent_con, self.roles)      # the yaml only seeds an empty access record
        return self

    def current_roles(self) -> dict[str, set[str]]:
        from . import access
        return access.current(self.open().agent_con)

    @property
    def index(self) -> Index:
        if self._index is None:
            self._index = Index.from_repo(self.repo)
        return self._index

    @property
    def skills(self) -> dict:
        if self._skills is None:
            self._skills = load_skills(self.repo)
        return self._skills


@dataclass
class Reply:
    text: str
    skill: str | None
    tool: str | None = None
    sources: list[str] = field(default_factory=list)
    proposals: list[str] = field(default_factory=list)
    refused: bool = False


def handle(ctx: Context, user: str, text: str, channel: str = "cli") -> Reply:
    ctx.open()
    t0 = time.time()
    con = ctx.agent_con
    m = APPROVE.match(text)
    if m:
        verb, pid = m.group(1).lower(), m.group(2)
        try:
            r = approvals.decide(con, ctx, pid, decided_by=user, approve=verb == "approve", roles=ctx.current_roles(), channel=channel)
        except approvals.NotAllowed as e:
            return Reply(f"Not done: {e}.", "approvals", refused=True)
        except ValueError as e:
            return Reply(f"Not done: {e}.", "approvals", refused=True)
        return Reply(_fmt_decision(r), "approvals", proposals=[pid])
    if LOOKS_LIKE_A_DECISION.match(text):
        audit.log(con, actor=user, channel=channel, skill="approvals", action="decide", outcome="bad_id")
        return Reply("Not done: that is not a proposal id. Proposal ids look like P followed by 10 characters, "
                     "for example `approve P1a2b3c4d5e`; the proposal message shows the exact one. Nothing changed.",
                     "approvals", refused=True)

    skill, tool_name, args, why_not, planner = _decide(ctx, text)
    ctx.last_planner = planner
    if why_not is not None:
        audit.log(con, actor=user, channel=channel, skill=skill.name, action="plan", tool=tool_name,
                  outcome="refused", detail={"why": why_not, "planner": planner})
        return Reply(f"Not done: {why_not}. Nothing changed.", skill.name, tool_name, refused=True)
    if tool_name is None:
        a = answer(text, ctx.index, model=ctx.model)
        audit.log(con, actor=user, channel=channel, skill=skill.name, action="answer",
                  outcome="refused" if a.refused else a.mode, detail={"sources": a.sources, "coverage": a.coverage, "note": a.note},
                  usage=a.usage, latency_ms=int((time.time() - t0) * 1000))
        return Reply(a.text, skill.name, sources=a.sources, refused=a.refused)

    tool = TOOLS[tool_name]
    if tool.name not in skill.tools:            # defence in depth: skills.load already refuses this
        audit.log(con, actor=user, channel=channel, skill=skill.name, action="tool", tool=tool.name, outcome="not_permitted")
        return Reply(f"The {skill.name} skill may not use {tool.name}.", skill.name, tool.name, refused=True)

    if tool.access == "read":
        try:
            out = tool.run(ctx, args)
            outcome = "ok"
        except Exception as e:
            out, outcome = {"error": str(e)}, "error"
        audit.log(con, actor=user, channel=channel, skill=skill.name, action="tool", tool=tool.name, args=args,
                  outcome=outcome, latency_ms=int((time.time() - t0) * 1000))
        src = [v for k, v in out.items() if k in ("runbook", "card") and isinstance(v, str)]
        return Reply(_fmt_read(tool.name, out), skill.name, tool.name, sources=src)

    # write: propose only
    if tool.name == "update_doc" and not args:
        stale = TOOLS["check_doc_claims"].run(ctx, {})["stale"]
        fixable = [s for s in stale if s["edit"]]
        pids = [approvals.propose(con, ctx, requested_by=user, skill=skill.name, tool="update_doc", args=s["edit"],
                                  summary=f"{s['doc']}: {s['problem']} (claim {s['id']})", channel=channel) for s in fixable]
        if not pids:
            return Reply("Every checked claim in the documents still matches the warehouse. Nothing to fix.", skill.name, tool.name)
        lines = [f"Proposed {p}: {s['doc']} ({s['id']}): {s['problem']}." for p, s in zip(pids, fixable)]
        return Reply("\n".join(lines) + f"\nEach needs approval from someone with the {tool.approver_role} role: "
                     "reply `approve <id>` or `reject <id>`.", skill.name, tool.name, proposals=pids)
    missing = [a for a in tool.args if a not in args]
    if missing:
        return Reply(f"To propose {tool.name} I need {', '.join(missing)}.", skill.name, tool.name, refused=True)
    summary = f"{tool.name} {args}"
    pid = approvals.propose(con, ctx, requested_by=user, skill=skill.name, tool=tool.name, args=args, summary=summary, channel=channel)
    return Reply(f"Proposed {pid}: {tool.description} {args}. Nothing has changed yet. It needs approval from "
                 f"someone with the {tool.approver_role} role other than you: reply `approve {pid}` or `reject {pid}`.",
                 skill.name, tool.name, proposals=[pid])


def _decide(ctx: Context, text: str):
    """-> (skill, tool or None for the documents, args, reason it may not run or None, planner used).
    The model plans when a runtime is attached and has an answer; otherwise the keyword router does."""
    if ctx.runtime is not None:
        from ..ai.planner import DOCS, REFUSE, plan
        call, _, rejected = plan(ctx.runtime, text)
        if call is not None:
            if rejected:
                return ctx.skills["knowledge_qa"], call.tool if call.tool in TOOLS else None, {}, rejected, "model"
            if call.tool == REFUSE:
                return ctx.skills["knowledge_qa"], None, {}, "that is outside what this agent does, or it names no exact id", "model"
            if call.tool == DOCS:
                return ctx.skills["knowledge_qa"], None, {}, None, "model"
            skill = next(s for s in ctx.skills.values() if call.tool in s.tools)
            return skill, call.tool, call.args, None, "model"
    skill, intent, args = route(ctx.skills, text)
    return skill, intent.tool if intent else None, args, None, "keywords"


def _fmt_decision(r: dict) -> str:
    if r.get("note"):                      # decided earlier: report it, do nothing
        return f"{r['proposal']} is already {r['status']}; nothing done."
    if r["status"] == "executed":
        return f"{r['proposal']} approved and done: {r['result']}."
    if r["status"] == "failed":
        return f"{r['proposal']} approved, but it failed and changed nothing: {r['result'].get('error')}."
    return f"{r['proposal']} {r['status']}. Nothing changed."


def _fmt_read(tool: str, o: dict) -> str:
    if "error" in o:
        return f"I could not read that: {o['error']}."
    if tool == "ingest_status":
        loaded = sum(d["loaded"] for d in o["deliveries"])
        rej = o["rejected_files"]
        s = f"{loaded} deliveries loaded across {len(o['deliveries'])} sources; {len(rej)} rejected."
        if rej:
            s += " Rejected: " + "; ".join(f"{r['path']} ({r['note']})" for r in rej[:5]) + "."
        if o["quarantine_top_reasons"]:
            s += " Quarantine: " + ", ".join(f"{q['source']} {q['reason']} x{q['n']}" for q in o["quarantine_top_reasons"][:3]) + "."
        s += " Invariant violations: " + (str(o["invariant_violations"]) if o["invariant_violations"] else "none") + "."
        return s + f" Runbook: {o['runbook']}."
    if tool == "deletion_review_queue":
        if not o["open"]:
            return "The deletion review queue is empty."
        items = "; ".join(f"{i['person_key']} ({i['reason']})" for i in o["items"][:10])
        return f"{o['open']} open deletion-review cases: {items}. How to decide: {o['runbook']}."
    if tool == "model_status":
        if o.get("runs") == 0:
            return "The home-buyer model has not run yet."
        s = (f"Home-buyer model {o['version']}, scored as of {o['scored_as_of']}: "
             f"{'published' if o['published'] else 'NOT published'}, {o['scored']} people scored, {o['not_scored']} not scored.")
        s += f" Failed checks: {', '.join(o['failed_checks'])}." if o["failed_checks"] else " All publishing checks passed."
        d = o.get("drift")
        if isinstance(d, dict) and d.get("feature"):
            level = "above the warning level" if d["max"] > d.get("warn_above", 1) else "within limits"
            s += f" Largest input drift: {d['feature']} at PSI {d['max']} ({level}, warn above {d.get('warn_above')})."
        return s + f" Model card: {o['card']}."
    if tool == "access_record":
        if not o["active"]:
            return "Nobody holds a role yet."
        by = {}
        for r in o["active"]:
            by.setdefault(r["role"], []).append(r["user_id"])
        return "Roles now: " + "; ".join(f"{k}: {', '.join(sorted(v))}" for k, v in sorted(by.items())) + \
            f". {o['history_rows']} grants and removals on record."
    if tool == "data_products_status":
        return "; ".join(f"{k}: {v}" for k, v in o.items()) + "."
    if tool == "check_doc_claims":
        if not o["stale"]:
            return "Every checked claim in the documents still matches the warehouse."
        return f"{len(o['stale'])} stale passages: " + "; ".join(f"{s['doc']} ({s['id']}): {s['problem']}" for s in o["stale"]) + \
            ". Say `fix the stale docs` to propose the edits."
    return str(o)
