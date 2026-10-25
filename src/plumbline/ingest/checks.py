"""Invariants that must hold after any ingest. Dagster asset checks and the tests both call these."""
from __future__ import annotations

from .contracts import SourceContract


def check_invariants(con, contracts: dict[str, SourceContract]) -> dict[str, int]:
    """Return a count of violations per check; every value must be 0."""
    v: dict[str, int] = {}
    for c in contracts.values():
        if c.role != "data":
            continue
        key = ", ".join(f'"{k}"' for k in c.key)
        v[f"{c.name}.duplicate_current_keys"] = con.execute(
            f"SELECT count(*) FROM (SELECT {key} FROM current_{c.name} GROUP BY {key} HAVING count(*) > 1)").fetchone()[0]
        for n, col in c.columns.items():
            if col.privacy:
                kind = col.privacy
                v[f"{c.name}.suppressed_{n}_in_bronze"] = con.execute(
                    f'SELECT count(*) FROM {c.table} WHERE "_sha_{n}" IN '
                    f"(SELECT {kind}_sha256 FROM suppression WHERE {kind}_sha256 IS NOT NULL)").fetchone()[0]
        v[f"{c.name}.duplicate_stored_rows"] = con.execute(
            f"SELECT count(*) FROM (SELECT _row_hash FROM {c.table} GROUP BY _row_hash HAVING count(*) > 1)").fetchone()[0]
        v[f"{c.name}.rows_without_a_loaded_delivery"] = con.execute(
            f"""SELECT count(*) FROM {c.table} WHERE _first_delivery_id NOT IN
                (SELECT delivery_id FROM ingest_ledger WHERE status = 'loaded')""").fetchone()[0]
    v["quarantine.suppressed_rows"] = con.execute(
        """SELECT count(*) FROM quarantine WHERE sha_email IN (SELECT email_sha256 FROM suppression)
           OR sha_phone IN (SELECT phone_sha256 FROM suppression)""").fetchone()[0]
    return v
