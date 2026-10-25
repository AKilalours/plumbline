"""Delete whole persons, not just the rows whose email or phone matched a request.

Runs after identity resolution. Steps:
1. Compute tombstone keys for every current record and find records that match two different key
   kinds of one tombstone. Those are the deleted person's surviving records (a misspelled email, a
   work email the request did not list).
2. If one tombstone hits more than one resolved person, nothing is deleted for it: it goes to
   deletion_review, because deleting the wrong person cannot be undone.
3. Every record the identity graph puts in a hit person is deleted: CRM, vendor, store orders and
   email events, from storage and from quarantine.
4. Their emails and phones join the suppression list, so re-sent files cannot bring them back, and
   their own tombstones are written, so the next variant is caught too.
"""
from __future__ import annotations

import hashlib

import pandas as pd

from ..ingest.contracts import email_hash, phone_hash
from ..ingest.erasure import QUARANTINE_ERASE
from . import tombstones

# identity_map source -> contract name (a store profile is matched, but orders are what get deleted)
MAP_SOURCE = {"crm": "crm", "vendor": "vendor", "store_order": "store", "email": "email"}


def _record_keys(recs: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for u, f, l, h, s, z, d in recs[["unique_id", "first_name", "last_name", "house", "street", "zip", "dob"]].itertuples(index=False):
        for k in tombstones.keys_from_parts(f, l, h, s, z, d):
            rows.append((u, k))
    return pd.DataFrame(rows, columns=["unique_id", "key_hash"])


def expand_deletions(con, contracts) -> dict:
    con.execute("""CREATE TABLE IF NOT EXISTS deletion_review (
        tombstone_id VARCHAR, person_key VARCHAR, reason VARCHAR, logged_at TIMESTAMP)""")
    recs = con.execute("""SELECT unique_id, person_key, first_name, last_name, house, street, zip, dob
                          FROM identity_records""").df().astype(object)
    recs = recs.where(recs.notna(), None)
    keys = _record_keys(recs)
    # Which tombstones may drive deletion. Strong ones (phone matched) are the requester's own rows.
    # A weak one (email only, no phone on the row) may belong to anyone sharing the inbox: it counts
    # only if its name key agrees with a strong tombstone of the same request, or, for a request with
    # no strong tombstone, if all of that request's weak tombstones carry one name. Otherwise review.
    con.execute("""CREATE OR REPLACE TEMP TABLE usable_tombstones AS
        WITH tk AS (SELECT tombstone_id, request_id, strength, key_hash FROM deletion_tombstones),
             strong_names AS (SELECT DISTINCT request_id, key_hash FROM tk WHERE strength = 'strong' AND key_hash LIKE 'fz:%'),
             has_strong AS (SELECT DISTINCT request_id FROM tk WHERE strength = 'strong'),
             weak_names AS (SELECT request_id, count(DISTINCT key_hash) AS n FROM tk
                            WHERE strength = 'weak' AND key_hash LIKE 'fz:%' GROUP BY request_id)
        SELECT DISTINCT tombstone_id FROM tk WHERE strength = 'strong' OR request_id IS NULL
        UNION
        SELECT DISTINCT w.tombstone_id FROM tk w JOIN strong_names s ON s.request_id = w.request_id AND s.key_hash = w.key_hash
        WHERE w.strength = 'weak'
        UNION
        SELECT DISTINCT w.tombstone_id FROM tk w JOIN weak_names n USING (request_id)
        WHERE w.strength = 'weak' AND n.n = 1 AND w.request_id NOT IN (SELECT * FROM has_strong)""")
    unusable = con.execute("""SELECT DISTINCT tombstone_id FROM deletion_tombstones
                              WHERE tombstone_id NOT IN (SELECT tombstone_id FROM usable_tombstones)""").df()
    con.register("rk", keys)
    # Two key kinds must agree, and a date of birth known on both sides must not disagree: a father
    # and son with one name at one address match on name and address, and only the DOB tells them apart.
    hits = con.execute("""
        WITH m AS (SELECT rk.unique_id, t.tombstone_id, count(DISTINCT split_part(rk.key_hash, ':', 1)) AS kinds,
                          bool_or(rk.key_hash LIKE 'fd:%') AS dob_agrees
                   FROM rk JOIN deletion_tombstones t USING (key_hash)
                   WHERE t.tombstone_id IN (SELECT tombstone_id FROM usable_tombstones) GROUP BY ALL),
             t_dob AS (SELECT DISTINCT tombstone_id FROM deletion_tombstones WHERE key_hash LIKE 'fd:%'),
             r_dob AS (SELECT DISTINCT unique_id FROM rk WHERE key_hash LIKE 'fd:%')
        SELECT unique_id, tombstone_id FROM m
        WHERE kinds >= 2
          AND (dob_agrees OR tombstone_id NOT IN (SELECT * FROM t_dob) OR unique_id NOT IN (SELECT * FROM r_dob))""").df()
    # One key kind is not enough to delete (it also fits a namesake in the same zip, or a spouse at
    # the same address) but it is what a person who moved leaves behind. Those go to a person.
    maybe = con.execute("""
        SELECT rk.unique_id, t.tombstone_id FROM rk JOIN deletion_tombstones t USING (key_hash)
        WHERE t.tombstone_id IN (SELECT tombstone_id FROM usable_tombstones)
        GROUP BY ALL HAVING count(DISTINCT split_part(rk.key_hash, ':', 1)) = 1 AND bool_and(rk.key_hash LIKE 'fz:%')""").df()
    con.unregister("rk")
    hits = hits.merge(recs[["unique_id", "person_key"]], on="unique_id")
    maybe = maybe.merge(recs[["unique_id", "person_key"]], on="unique_id")
    maybe = maybe[~maybe.person_key.isin(set(hits.person_key))].drop_duplicates(["tombstone_id", "person_key"])
    per_tomb = hits.groupby("tombstone_id")["person_key"].nunique()
    ambiguous = set(per_tomb[per_tomb > 1].index)
    review = hits[hits.tombstone_id.isin(ambiguous)].drop_duplicates(["tombstone_id", "person_key"])
    persons = set(hits.loc[~hits.tombstone_id.isin(ambiguous), "person_key"])

    con.execute("BEGIN")
    try:
        if len(review):
            con.executemany("INSERT INTO deletion_review VALUES (?, ?, 'tombstone_matches_several_persons', now())",
                            review[["tombstone_id", "person_key"]].values.tolist())
        if len(maybe):
            con.executemany("INSERT INTO deletion_review VALUES (?, ?, 'one_key_matches', now())",
                            maybe[["tombstone_id", "person_key"]].values.tolist())
        targets = con.execute("SELECT source, record_id FROM identity_map WHERE list_contains(?, person_key)",
                              [sorted(persons)]).df() if persons else pd.DataFrame(columns=["source", "record_id"])
        removed, new_suppression = {}, 0
        for src, cname in MAP_SOURCE.items():
            c = contracts[cname]
            table, key = c.table, c.key[0]
            ids = targets.loc[targets.source == src, "record_id"].tolist()
            if not ids:
                continue
            con.register("ids", pd.DataFrame({"id": ids}))
            email_c = next((n for n, col in c.columns.items() if col.privacy == "email"), None)
            phone_c = next((n for n, col in c.columns.items() if col.privacy == "phone"), None)
            sel = ", ".join(x for x in (email_c, phone_c) if x) or "NULL"
            ident = con.execute(f"SELECT {sel} FROM {table} WHERE {key} IN (SELECT id FROM ids)").df()
            for _, r in ident.iterrows():
                e = r.get(email_c) if email_c else None
                p = r.get(phone_c) if phone_c else None
                eh = email_hash(e) if isinstance(e, str) and e else None
                ph = phone_hash(p) if isinstance(p, str) and p else None
                if (eh or ph) and not con.execute(
                        "SELECT 1 FROM suppression WHERE email_sha256 IS NOT DISTINCT FROM ? AND phone_sha256 IS NOT DISTINCT FROM ?",
                        [eh, ph]).fetchone():
                    con.execute("INSERT INTO suppression VALUES (?, ?, ?, current_date, 'person_level')",
                                ["PL" + hashlib.sha256(f"{eh}|{ph}".encode()).hexdigest()[:24], eh, ph])
                    new_suppression += 1
            idc = c.identity_cols
            if idc:
                gone = con.execute(f"SELECT {', '.join(idc.values())} FROM {table} WHERE {key} IN (SELECT id FROM ids)").df().astype(object)
                tombstones.record(con, [(r, None, "strong") for r in gone.where(gone.notna(), None).to_dict("records")],
                                  idc, f"person_level:{src}")
            n = con.execute(f"SELECT count(*) FROM {table} WHERE {key} IN (SELECT id FROM ids)").fetchone()[0]
            con.execute(f"DELETE FROM {table} WHERE {key} IN (SELECT id FROM ids)")
            con.unregister("ids")
            removed[src] = n
            con.execute("INSERT INTO suppression_log VALUES (?, 'person_level', 'purged', ?, now())", [src, n])
        q = con.execute(f"DELETE FROM quarantine WHERE {QUARANTINE_ERASE} RETURNING 1").fetchall()
        if persons:
            con.execute("DELETE FROM identity_map WHERE list_contains(?, person_key)", [sorted(persons)])
            con.execute("DELETE FROM identity_records WHERE list_contains(?, person_key)", [sorted(persons)])
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return {"persons_deleted": len(persons), "rows_removed": removed, "quarantine_rows_removed": len(q),
            "suppression_entries_added": new_suppression, "held_for_review": len(ambiguous) + len(maybe),
            "weak_tombstones_not_used": len(unusable)}

