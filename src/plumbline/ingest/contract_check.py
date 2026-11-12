"""Fail a change that would break the data's consumers, unless the contract says it means to.

    python -m plumbline.ingest.contract_check --base origin/main      # in CI: compare every contract with the base

A contract is a promise to whoever reads its tables. Comparing each contract file with its version at `base`:

- **breaking:** a column removed; a column's type changed; a value made required, or a column no longer
  optional; a new column that every delivery must carry; the key or "newest by" changed; a delete rule added or
  changed (rows can vanish from the current view).
- **compatible:** a new optional column, a wider allowed range or value list, a description.

A breaking change passes only when the contract's `version` goes up (a missing version counts as 1), which is
the author saying "consumers must change too" in the same diff a reviewer reads. A removed contract is breaking.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
DIRS = ["sources", "sources/streams", "sources/databases", "sources/open_data"]


def _cols(c: dict) -> dict:
    return c.get("columns") or {}


# The loader's two flags (ingest/contracts.py): `required` means the value may not be empty; a column is
# expected in every delivery unless it is `optional`. Making either stricter breaks deliveries that load today.
_req = lambda spec: bool(spec.get("required", False))
_present = lambda spec: not spec.get("optional", False)


def breaking(old: dict, new: dict | None) -> list[str]:
    """What in `new` would break a reader of `old`. None for `new` means the contract was removed."""
    if new is None:
        return ["the contract was removed"]
    out = []
    oc, nc = _cols(old), _cols(new)
    for name, spec in oc.items():
        if name not in nc:
            out.append(f"column {name} removed")
            continue
        if spec.get("type") != nc[name].get("type"):
            out.append(f"column {name} type {spec.get('type')} -> {nc[name].get('type')}")
        if not _req(spec) and _req(nc[name]):
            out.append(f"column {name} made required (empty values now quarantined)")
        if not _present(spec) and _present(nc[name]):
            out.append(f"column {name} no longer optional (deliveries without it now rejected)")
    for name, spec in nc.items():
        if name not in oc and (_req(spec) or _present(spec)):
            out.append(f"new column {name} must be in every delivery (mark it optional, or raise the version)")
    for k in ("key", "latest_by", "deleted_when"):
        if old.get(k) != new.get(k):
            out.append(f"{k} {old.get(k)} -> {new.get(k)}")
    return out


def check(old: dict, new: dict | None) -> tuple[list[str], bool]:
    """-> (breaking changes, allowed): allowed when there are none, or the version went up."""
    b = breaking(old, new)
    if not b or new is None:
        return b, not b
    return b, int(new.get("version", 1)) > int(old.get("version", 1))


def _at(ref: str, path: str) -> dict | None:
    r = subprocess.run(["git", "show", f"{ref}:{path}"], cwd=ROOT, capture_output=True, text=True)
    return yaml.safe_load(r.stdout) if r.returncode == 0 else None


def _listed(ref: str) -> list[str]:
    r = subprocess.run(["git", "ls-tree", "--name-only", ref, *[d + "/" for d in DIRS]], cwd=ROOT, capture_output=True, text=True)
    return [p for p in r.stdout.split() if p.endswith(".yaml")]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--base", required=True, help="git ref to compare with, e.g. origin/main or the push's before-sha")
    a = ap.parse_args(argv)
    if set(a.base) <= {"0"}:
        print("no base to compare with (first push of a branch): nothing to check")
        return 0
    bad = 0
    for path in _listed(a.base):
        old = _at(a.base, path)
        new = yaml.safe_load((ROOT / path).read_text()) if (ROOT / path).exists() else None
        if not isinstance(old, dict) or "columns" not in old:
            continue
        b, ok = check(old, new)
        if b:
            print(f"{'ok (version raised)' if ok else 'BREAKING'}  {path}: " + "; ".join(b))
            bad += not ok
    if bad:
        print(f"\n{bad} contract change(s) would break their consumers. Raise `version:` in the contract if that is "
              "intended, so the reviewer sees it.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
