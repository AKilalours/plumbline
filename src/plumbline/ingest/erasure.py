"""The single rule for "does this deletion request cover this row". Load, purge, the current views,
quarantine and the invariant checks all use it, so they can never disagree.

A request carries the requester's email hash and phone hash. Measured on the seed-7 world:
- a phone match never hit anyone but the requester, so a phone match erases;
- an email match on a row whose own phone differs from the request's phone was never the requester
  (family members sharing one inbox: 20 rows, 0 of them the requester's), so that row is kept;
- an email match on a row with no phone is ambiguous and erases. Email events only describe the
  address, which is on the list; remaining namesake-free cases are left to person-level deletion.
"""
from __future__ import annotations


def erase_sql(email_expr: str | None, phone_expr: str | None) -> str:
    """SQL predicate, true when the row must be erased. Arguments are SQL expressions or None."""
    conds = []
    if phone_expr:
        conds.append(f"{phone_expr} IN (SELECT phone_sha256 FROM suppression WHERE phone_sha256 IS NOT NULL)")
    if email_expr:
        phone_ok = f"({phone_expr} IS NULL OR s.phone_sha256 IS NULL OR s.phone_sha256 = {phone_expr})" if phone_expr else "TRUE"
        conds.append(f"EXISTS (SELECT 1 FROM suppression s WHERE s.email_sha256 = {email_expr} AND {phone_ok})")
    # coalesce: with a NULL phone, "NULL IN (...)" is NULL, not FALSE, and NOT NULL is still NULL,
    # so a view filtering on NOT <this> would silently drop every row without a phone.
    return "coalesce(" + " OR ".join(conds) + ", FALSE)" if conds else "FALSE"


def contract_erase_sql(c, alias: str = "") -> str:
    a = f"{alias}." if alias else ""
    e = next((n for n, col in c.columns.items() if col.privacy == "email"), None)
    p = next((n for n, col in c.columns.items() if col.privacy == "phone"), None)
    return erase_sql(f'{a}"_sha_{e}"' if e else None, f'{a}"_sha_{p}"' if p else None)


QUARANTINE_ERASE = erase_sql("quarantine.sha_email", "quarantine.sha_phone")


class RequestIndex:
    """In-memory form of the same rule, for rows checked before anything is stored."""

    def __init__(self, con):
        self.by_phone: dict[str, str] = {}
        self.by_email: dict[str, list[tuple[str, str | None]]] = {}
        for rid, e, p in con.execute("SELECT request_id, email_sha256, phone_sha256 FROM suppression ORDER BY request_id").fetchall():
            if p:
                self.by_phone.setdefault(p, rid)
            if e:
                self.by_email.setdefault(e, []).append((rid, p))

    def match(self, email_h: str | None, phone_h: str | None) -> tuple[str, str] | None:
        """-> (request_id, "strong" | "weak") or None. Strong: the phone matched, so this row is the
        requester's. Weak: only the email matched and the row has no phone, so it may be anyone
        who uses that inbox."""
        if phone_h and phone_h in self.by_phone:
            return self.by_phone[phone_h], "strong"
        for rid, req_phone in self.by_email.get(email_h, []) if email_h else []:
            if phone_h is None or req_phone is None:
                return rid, "weak"
        return None

    def erases(self, email_h: str | None, phone_h: str | None) -> bool:
        return self.match(email_h, phone_h) is not None
