"""Reconcile what the warehouse holds against what the source says now: a rotating slice of rows, every run.

An incremental sync trusts the source's update times. Three things get past that trust, and this catches them:
a publisher reload that the sync skips (2026-11-10: 803,457 NYC rows under one time), a row the source
changes without moving its update time, and a field the connector never wrote (apt_condo_no_s, dropped for
five days: see connectors/socrata.py). Each run takes the next `budget` rows of the ones held (ordered by a
hash of their id, so every row comes up in turn and a full pass takes held / budget runs), asks the source
for those ids now, and compares field by field through the loader's own parser, so a float written 1234.0 and
"1234" are the same and a real difference is not.

Outcomes per row: the same; changed (the source's current version is landed, so the next load and publish
correct it); or gone (the source no longer has the id: reported, never deleted on that evidence alone).
"""
from __future__ import annotations

import hashlib
import urllib.parse
from datetime import date, datetime

from .contracts import BadValue, normalize

CHUNK = 100                   # ids per request: a few kilobytes of URL


def order(ids) -> list[str]:
    """A stable order that is not the order rows arrived in, so each slice samples the whole table."""
    return sorted(map(str, ids), key=lambda i: hashlib.sha1(i.encode()).hexdigest())


def take(ids: list[str], offset: int, budget: int) -> tuple[list[str], int]:
    """The next `budget` ids from `offset`, wrapping around; returns them and the next offset."""
    if not ids:
        return [], 0
    offset %= len(ids)
    out = (ids[offset:] + ids[:offset])[:budget]
    return out, (offset + len(out)) % len(ids)


def fetch(client, dataset: str, held: list[dict], stats, by: str = "work_permit") -> dict[str, dict]:
    """The source's current version of each held row. NYC's API refuses "=" and "in" on its row id (:id is a row
    number, not text: checked 2026-11-12), so rows are asked for by a text column they carry (`by`), and each
    is then picked out by its row id. A row whose `by` value changed comes back as gone: reported, not deleted."""
    from .connectors.socrata import _flat
    got = {}
    values = sorted({str(r[by]) for r in held if r.get(by)})
    for i in range(0, len(values), CHUNK):
        part = values[i: i + CHUNK]
        where = f"{by} in (" + ",".join("'" + x.replace("'", "''") + "'" for x in part) + ")"
        q = {"$select": "*, :id, :updated_at", "$where": where, "$limit": 50_000}
        body, _ = client.get(f"/resource/{dataset}.json?" + urllib.parse.urlencode(q, quote_via=urllib.parse.quote), stats)
        for r in body if isinstance(body, list) else []:
            f = _flat(r)
            got[str(f["source_row_id"])] = f
    return got


def _text(v) -> str:
    if v is None:
        return ""
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    return str(v)


def _value(col, v):
    try:
        return normalize(col, _text(v))
    except BadValue:
        return ("unparseable", _text(v))


def compare(contract, held: dict, now: dict) -> list[str]:
    """The contract columns whose value differs, read the way the loader reads them (update time aside)."""
    return [n for n, col in contract.columns.items()
            if n not in ("source_updated_at",) and _value(col, held.get(n)) != _value(col, now.get(n))]


def reconcile(client, dataset: str, contract, held: list[dict], offset: int, budget: int, stats, by: str = "work_permit") -> dict:
    """-> {checked, same, changed, gone, fields, next_offset, rows (the changed rows' current versions)}."""
    by_id = {str(r["source_row_id"]): r for r in held}
    ids, nxt = take(order(by_id), offset, budget)
    now = fetch(client, dataset, [by_id[i] for i in ids], stats, by)
    changed, gone, fields = [], [], {}
    for i in ids:
        if i not in now:
            gone.append(i)
            continue
        diff = compare(contract, by_id[i], now[i])
        if diff:
            changed.append(now[i])
            for f in diff:
                fields[f] = fields.get(f, 0) + 1
    return {"checked": len(ids), "same": len(ids) - len(changed) - len(gone), "changed": len(changed), "gone": len(gone),
            "fields": dict(sorted(fields.items(), key=lambda kv: -kv[1])), "gone_ids": gone[:20],
            "held": len(by_id), "offset": offset, "next_offset": nxt, "rows": changed}
