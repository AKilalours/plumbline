"""Draft a source contract for a file nobody has described yet.

    python -m plumbline.ai.onboard partner.csv --name acme      # writes sources/drafts/acme.yaml

The drafter sees the header and a few sample values per column and proposes a type, whether the
column is an email or phone (privacy: hashed, checked against suppression, removed on deletion),
which identity role it plays, and the key. The draft is never loaded on its own: it is written to
sources/drafts/ with what it is unsure of, validated by the same loader as every contract, and a
person moves it into sources/ after reading it. Missing a privacy column is the costly mistake
(deletion and unsubscribe rules would not apply), so it is scored on its own.
"""
from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

from ..ingest.contracts import TYPES, load_contract
from .runtime import NotRecorded, Runtime, Trace

Type = Literal[tuple(sorted(TYPES))]                                            # type: ignore[valid-type]
Identity = Literal["first_name", "last_name", "full_name", "street", "zip", "dob"]


class ColumnDraft(BaseModel):
    name: str
    type: Type
    required: bool = False
    privacy: Literal["email", "phone"] | None = None
    identity: Identity | None = None
    values: list[str] = Field(default_factory=list, description="only for type enum: every allowed value")
    unsure: str = Field(default="", description="what a person should check, or empty")


class Draft(BaseModel):
    key: list[str] = Field(description="column(s) that identify one record")
    latest_by: list[str] = Field(default_factory=list, description="date column that says which version of a key is newest, if any")
    columns: list[ColumnDraft]


# ------------------------------------------------------------------ profiling
_PAT = {
    "email": re.compile(r"^[^@\s]+@[^@\s]+\.[a-z]{2,}$", re.I),
    "phone": re.compile(r"^\(?\+?1?[\s.-]?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}$"),
    "zip": re.compile(r"^\d{5}(-\d{4})?$"),
    "iso_date": re.compile(r"^\d{4}-\d{2}-\d{2}$"),
    "us_date": re.compile(r"^\d{1,2}/\d{1,2}/\d{2,4}$"),
    "timestamp": re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}"),
    "int": re.compile(r"^-?\d+$"),
    "float": re.compile(r"^-?\d*\.\d+$"),
    "flag": re.compile(r"^(y|n|yes|no|true|false|0|1|t|f)$", re.I),
    "address": re.compile(r"^\d+[A-Z]?\s+\w+.*\b(st|street|ave|avenue|rd|road|blvd|pl|place|ln|lane|dr|drive)\b", re.I),
}


def profile(path: str | Path, n: int = 300, samples: int = 5) -> list[dict]:
    with open(path, newline="") as f:
        r = csv.DictReader(f)
        rows = [row for _, row in zip(range(n), r)]
        header = r.fieldnames or []
    out = []
    for h in header:
        vals = [row.get(h) or "" for row in rows]
        filled = [v for v in vals if v.strip()]
        hits = {k: round(sum(bool(p.match(v.strip())) for v in filled) / max(len(filled), 1), 2) for k, p in _PAT.items()}
        seen, sample = set(), []
        for v in filled:
            if v not in seen:
                seen.add(v)
                sample.append(v)
            if len(sample) == samples:
                break
        out.append({"name": h, "fill": round(len(filled) / max(len(vals), 1), 2),
                    "distinct": round(len(set(filled)) / max(len(filled), 1), 2),
                    "distinct_values": sorted(set(filled))[:12] if len(set(filled)) <= 12 else None,
                    "samples": sample, "matches": {k: v for k, v in hits.items() if v >= 0.5}})
    return out


# ------------------------------------------------------------------ offline drafter
# Generic header words only. A first version also listed words from the eval's partner headers
# (e_addr, recipient, to_address), which is tuning the baseline on the test; they were removed.
_NAME_HINTS = [
    (r"e-?mail", "email"),
    (r"phone|mobile|tel", "phone"),
    (r"birth|dob", "dob"),
    (r"first", "first_name"),
    (r"last", "last_name"),
    (r"name", "full_name"),
    (r"zip|postal", "zip"),
    (r"address|street", "street"),
]


def heuristic_draft(prof: list[dict]) -> Draft:
    """Header words plus value patterns. No model: the baseline every model draft is compared with."""
    cols = []
    for c in prof:
        h, m = c["name"].lower(), c["matches"]
        hint = next((role for rx, role in _NAME_HINTS if re.search(rx, h)), None)
        if "email" in m or hint == "email":
            col = ColumnDraft(name=c["name"], type="email", privacy="email")
        elif "phone" in m and "int" not in m or hint == "phone":
            col = ColumnDraft(name=c["name"], type="phone", privacy="phone")
        elif "timestamp" in m:
            col = ColumnDraft(name=c["name"], type="timestamp")
        elif "us_date" in m:
            col = ColumnDraft(name=c["name"], type="loose_date", identity="dob" if hint == "dob" else None)
        elif "iso_date" in m:
            col = ColumnDraft(name=c["name"], type="date", identity="dob" if hint == "dob" else None)
        elif "zip" in m:
            col = ColumnDraft(name=c["name"], type="zip", identity="zip")
        elif "address" in m or hint == "street":
            col = ColumnDraft(name=c["name"], type="address", identity="street")
        elif "flag" in m and c["distinct_values"] and len(c["distinct_values"]) <= 3:
            col = ColumnDraft(name=c["name"], type="flag")
        elif "int" in m and c["distinct"] < 0.5:
            col = ColumnDraft(name=c["name"], type="int")
        elif "float" in m:
            col = ColumnDraft(name=c["name"], type="float")
        elif c["distinct_values"] and len(c["distinct_values"]) <= 6:
            col = ColumnDraft(name=c["name"], type="enum", values=c["distinct_values"])
        elif hint in ("first_name", "last_name", "full_name"):
            col = ColumnDraft(name=c["name"], type="name", identity=hint)
        else:
            col = ColumnDraft(name=c["name"], type="string")
        cols.append(col)
    key = next((c["name"] for c in prof if c["distinct"] == 1.0 and c["fill"] == 1.0), prof[0]["name"])
    for col in cols:
        if col.name == key:
            col.required = True
    return Draft(key=[key], columns=cols)


# ------------------------------------------------------------------ model drafter
SYSTEM = f"""You draft data contracts for incoming customer-data files at a marketing data company.
For every column choose:
- type, one of: {", ".join(sorted(TYPES))}. name = a person's name, address = a street line,
  loose_date = dates in mixed or US formats, date = ISO dates, flag = yes/no values, enum = a small fixed set.
- privacy: "email" for any email address column and "phone" for any phone number column, else null.
  Getting these right matters most: they drive unsubscribe and deletion.
- identity: first_name, last_name, full_name, street, zip or dob when the column identifies a person, else null.
- required: true only for columns every record must have (ids, the key).
- values: for enum only, every allowed value seen.
- unsure: a short note when a person should check your choice, else empty.
Also give the key column(s), and latest_by: a date column that says which version of a record is newest, if any.
Use the column names exactly as given."""


def model_draft(rt: Runtime, prof: list[dict]) -> tuple[Draft | None, Trace | None]:
    view = [{k: c[k] for k in ("name", "fill", "distinct", "samples", "distinct_values")} for c in prof]
    try:
        return rt.structured("onboarding", SYSTEM, yaml.safe_dump({"columns": view}, sort_keys=False), Draft, max_tokens=1500)
    except NotRecorded:
        return None, None


# ------------------------------------------------------------------ output and grading
def to_contract(d: Draft, name: str, files: str) -> dict:
    cols = {}
    for c in d.columns:
        spec = {"type": c.type}
        for k in ("required", "privacy", "identity"):
            if getattr(c, k):
                spec[k] = getattr(c, k)
        if c.type == "enum":
            spec["values"] = c.values
        cols[c.name] = spec
    return {"name": name, "kind": "file", "files": files, "key": d.key, "latest_by": d.latest_by, "columns": cols}


def validate(d: Draft, name: str, tmp: Path) -> str | None:
    """The same loader every contract goes through. Returns why it fails, or None."""
    names = [c.name for c in d.columns]
    if len(set(names)) != len(names):
        return "a column appears twice"
    if any(k not in names for k in d.key):
        return "the key names a column the file does not have"
    p = tmp / f"{name}.yaml"
    p.write_text(yaml.safe_dump(to_contract(d, name, f"sources/{name}/*.csv"), sort_keys=False))
    try:
        load_contract(p)
    except Exception as e:  # noqa: BLE001 - any loader error is a reason to reject the draft
        return str(e)
    return None


def grade(d: Draft | None, gold: dict[str, dict], gold_key: list[str]) -> dict:
    """Per column: type (date and loose_date count as the same family), privacy, identity; and the key."""
    if d is None:
        return {"columns": len(gold), "type": 0, "privacy_found": 0, "privacy_false": 0, "privacy_total":
                sum(1 for g in gold.values() if g.get("privacy")), "identity": 0, "key": False, "valid": False}
    by = {c.name: c for c in d.columns}
    fam = lambda t: "date" if t in ("date", "loose_date") else t
    types = sum(1 for n, g in gold.items() if n in by and fam(by[n].type) == fam(g["type"]))
    priv_total = sum(1 for g in gold.values() if g.get("privacy"))
    priv_found = sum(1 for n, g in gold.items() if g.get("privacy") and n in by and by[n].privacy == g["privacy"])
    priv_false = sum(1 for n, g in gold.items() if not g.get("privacy") and n in by and by[n].privacy)
    ident = sum(1 for n, g in gold.items() if n in by and by[n].identity == g.get("identity"))
    return {"columns": len(gold), "type": types, "privacy_found": priv_found, "privacy_total": priv_total,
            "privacy_false": priv_false, "identity": ident, "key": list(d.key) == list(gold_key), "valid": True}


def main(argv: list[str] | None = None) -> int:
    import argparse
    import tempfile
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("csv")
    ap.add_argument("--name", required=True)
    ap.add_argument("--out", default="sources/drafts")
    a = ap.parse_args(argv)
    prof = profile(a.csv)
    rt = Runtime.from_env()
    d, _ = model_draft(rt, prof) if rt.mode == "live" else (None, None)
    how = "model" if d else "heuristic (no model configured)"
    d = d or heuristic_draft(prof)
    rt.save()
    why = validate(d, a.name, Path(tempfile.mkdtemp()))
    if why:
        print(f"draft rejected by the contract loader: {why}")
        return 1
    out = Path(a.out) / f"{a.name}.yaml"
    out.parent.mkdir(parents=True, exist_ok=True)
    notes = [f"#   {c.name}: {c.unsure}" for c in d.columns if c.unsure]
    out.write_text(f"# DRAFT by {how}. Read it, fix it, then move it to sources/.\n" + ("# Check:\n" + "\n".join(notes) + "\n" if notes else "")
                   + yaml.safe_dump(to_contract(d, a.name, f"sources/{a.name}/*.csv"), sort_keys=False))
    print(f"wrote {out} ({how})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
