"""Access provisioning: who holds which role, granted and removed only by approval.

The access record writes itself: every grant and removal is a row with who asked, who approved, when
and through which proposal. ops/roles.yaml only seeds the first holders of an empty record.
"""
from __future__ import annotations

ROLES = ("admin", "data-platform", "docs", "privacy")

SCHEMA = """
CREATE SCHEMA IF NOT EXISTS ops;
CREATE TABLE IF NOT EXISTS ops.access (
    user_id VARCHAR, role VARCHAR, granted_by VARCHAR, granted_at TIMESTAMP, via_proposal VARCHAR,
    revoked_by VARCHAR, revoked_at TIMESTAMP, revoked_via VARCHAR);
"""


def ensure(con, seed: dict[str, set[str]] | None = None) -> None:
    con.execute(SCHEMA)
    if seed and not con.execute("SELECT count(*) FROM ops.access").fetchone()[0]:
        for user, roles in sorted(seed.items()):
            for r in sorted(roles):
                con.execute("INSERT INTO ops.access VALUES (?, ?, 'bootstrap', now(), NULL, NULL, NULL, NULL)", [user, r])


def current(con) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for u, r in con.execute("SELECT user_id, role FROM ops.access WHERE revoked_at IS NULL").fetchall():
        out.setdefault(u, set()).add(r)
    return out


def _check_role(role: str) -> None:
    if role not in ROLES:
        raise ValueError(f"unknown role {role!r}; roles are {', '.join(ROLES)}")


def grant(con, user: str, role: str, by: str, proposal: str | None) -> dict:
    _check_role(role)
    if role in current(con).get(user, set()):
        return {"user": user, "role": role, "note": "already held; nothing changed"}
    con.execute("INSERT INTO ops.access VALUES (?, ?, ?, now(), ?, NULL, NULL, NULL)", [user, role, by, proposal])
    return {"user": user, "role": role, "granted": True}


def revoke(con, user: str, roles: list[str], by: str, proposal: str | None) -> dict:
    for r in roles:
        _check_role(r)
    held = current(con)
    admins = {u for u, rs in held.items() if "admin" in rs}
    if "admin" in roles and admins == {user}:
        raise ValueError("refused: that would remove the last admin, and nobody could approve anything again")
    gone = [r for r in roles if r in held.get(user, set())]
    for r in gone:
        con.execute("""UPDATE ops.access SET revoked_by = ?, revoked_at = now(), revoked_via = ?
                       WHERE user_id = ? AND role = ? AND revoked_at IS NULL""", [by, proposal, user, r])
    return {"user": user, "removed": gone}
