"""Irreversible match keys of purged rows, so a deleted person's other records can still be found.

When a row is deleted we keep none of its values, only keyed hashes (HMAC-SHA256) of three
combinations: first name + last name + zip, last name + house + street, first name + last name +
date of birth. Plain SHA-256 of "jane|smith|10025" could be reversed by trying every common name in
every zip; HMAC with a secret key cannot be tested without the key. Set PLUMBLINE_TOMBSTONE_KEY in
production. The development default is public and says so.

A later record is linked to a tombstone only when two different key kinds match the same tombstone:
one kind alone matches spouses (same last name and address) or namesakes in one zip.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import uuid

from ..identity.standardize import clean_name, name_group, split_full_name, standardize_street

DEV_KEY = "plumbline-dev-key-not-secret"
ROLES = ("first_name", "last_name", "full_name", "street", "zip", "dob")


def _key() -> bytes:
    return os.environ.get("PLUMBLINE_TOMBSTONE_KEY", DEV_KEY).encode()


def _h(kind: str, *parts) -> str:
    return kind + ":" + hmac.new(_key(), "|".join(parts).encode(), hashlib.sha256).hexdigest()


def keys_from_parts(first, last, house, street, zip_, dob) -> list[str]:
    g = name_group(first) if first else None
    out = []
    if g and last and zip_:
        out.append(_h("fz", g, last, zip_))
    if last and house and street:
        out.append(_h("la", last, house, street))
    if g and last and dob and len(str(dob)) == 10:
        out.append(_h("fd", g, last, str(dob)))
    return out


def keys_from_row(identity_cols: dict[str, str], row: dict) -> list[str]:
    """identity_cols maps role -> column name; row holds raw or normalized values."""
    v = {r: row.get(c) for r, c in identity_cols.items()}
    first, last = clean_name(v.get("first_name")), clean_name(v.get("last_name"))
    if v.get("full_name") and not (first and last):
        first, last = split_full_name(v["full_name"])
    house, street, _ = standardize_street(v.get("street"))
    z = str(v.get("zip") or "").strip() or None
    dob = str(v.get("dob") or "").strip()
    if len(dob) == 10 and dob[2] == "/":
        dob = f"{dob[6:]}-{dob[:2]}-{dob[3:5]}"
    return keys_from_parts(first, last, house, street, z, dob if len(dob) == 10 else None)


def record(con, rows: list[tuple[dict, str, str]], identity_cols: dict[str, str], reason: str) -> int:
    """rows: (row values, request_id, strength). One tombstone per row that yields any key."""
    out = []
    for r, request_id, strength in rows:
        ks = keys_from_row(identity_cols, r)
        if ks:
            t = uuid.uuid4().hex
            out += [(t, k, reason, request_id, strength) for k in ks]
    if out:
        con.executemany("INSERT INTO deletion_tombstones VALUES (?, ?, ?, ?, ?, now())", out)
    return len({o[0] for o in out})
