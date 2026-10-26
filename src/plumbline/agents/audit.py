"""Every question, tool call, proposal and decision, with who did it. The table doubles as
compliance evidence: it answers "who approved deleting this person, and when" without anyone's memory."""
from __future__ import annotations

import hashlib
import json

SCHEMA = """
CREATE SCHEMA IF NOT EXISTS ops;
CREATE TABLE IF NOT EXISTS ops.audit (
    logged_at TIMESTAMP, actor VARCHAR, channel VARCHAR, skill VARCHAR, action VARCHAR, tool VARCHAR,
    args_sha256 VARCHAR, proposal_id VARCHAR, outcome VARCHAR, detail JSON,
    input_tokens INTEGER, output_tokens INTEGER, cost DOUBLE, latency_ms INTEGER);
CREATE TABLE IF NOT EXISTS ops.proposals (
    id VARCHAR PRIMARY KEY, created_at TIMESTAMP, requested_by VARCHAR, skill VARCHAR, tool VARCHAR,
    args JSON, summary VARCHAR, approver_role VARCHAR, status VARCHAR,
    decided_by VARCHAR, decided_at TIMESTAMP, result JSON);
CREATE TABLE IF NOT EXISTS ops.slack_events (event_id VARCHAR PRIMARY KEY, received_at TIMESTAMP);
"""


def ensure(con) -> None:
    con.execute(SCHEMA)


def args_hash(args: dict) -> str:
    """Arguments are stored as a hash, not in clear: an audit log must not become a second copy of
    the personal data it is auditing. The proposal row keeps the arguments it needs to execute."""
    return hashlib.sha256(json.dumps(args, sort_keys=True, default=str).encode()).hexdigest()


def log(con, *, actor: str, channel: str, skill: str | None, action: str, tool: str | None = None,
        args: dict | None = None, proposal_id: str | None = None, outcome: str, detail: dict | None = None,
        usage=None, latency_ms: int | None = None) -> None:
    ensure(con)
    con.execute("INSERT INTO ops.audit VALUES (now(), ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [actor, channel, skill, action, tool, args_hash(args) if args is not None else None, proposal_id,
                 outcome, json.dumps(detail or {}, default=str),
                 getattr(usage, "input_tokens", None), getattr(usage, "output_tokens", None),
                 getattr(usage, "cost", None), latency_ms])
