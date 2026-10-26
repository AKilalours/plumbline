"""Documented facts that can go stale, checked against the warehouse.

An agent that answers from documents repeats whatever they say, true or not, so the documents must
be kept true. Each claim names a passage and a query that confirms or contradicts it. A contradicted
claim yields the exact edit to make; the edit is proposed, never applied, until the docs owner approves.

  number claims: the passage is found by pattern with the number as a wildcard, so the claim still
                 recognises its passage after the number was corrected
  statement claims: `text` with `true_if`, and the `replacement` with `replacement_true_if`; once the
                 replacement is in the document, that is what gets checked
"""
from __future__ import annotations

import re
from pathlib import Path

import duckdb
import yaml


def load(repo) -> list[dict]:
    p = Path(repo) / "docs" / "claims.yaml"
    return yaml.safe_load(p.read_text())["claims"] if p.exists() else []


def _number_pattern(c: dict) -> tuple[re.Pattern, int]:
    m = re.search(c["number"], c["text"])
    pat = re.escape(c["text"][:m.start(1)]) + r"(\d+)" + re.escape(c["text"][m.end(1):])
    return re.compile(pat), m.start(1)


def check(ctx) -> list[dict]:
    stale = []
    with duckdb.connect(str(ctx.warehouse), read_only=True) as con:
        run = getattr(ctx, "run_dir", None)
        for c in load(ctx.repo):
            if c.get("run") and run is not None and Path(run).name != c["run"]:
                continue                       # a fact about another run; nothing to check here
            doc = (Path(ctx.repo) / c["doc"]).read_text()
            if "number" in c:
                pat, _ = _number_pattern(c)
                found = list(pat.finditer(doc))
                if len(found) != 1:
                    stale.append({"id": c["id"], "doc": c["doc"], "edit": None,
                                  "problem": f"passage found {len(found)} times; update docs/claims.yaml"})
                    continue
                stated, actual = int(found[0].group(1)), int(con.execute(c["sql"]).fetchone()[0])
                if stated != actual:
                    old = found[0].group(0)
                    new = old[:found[0].start(1) - found[0].start(0)] + str(actual) + old[found[0].end(1) - found[0].start(0):]
                    stale.append({"id": c["id"], "doc": c["doc"], "problem": f"says {stated}, the warehouse says {actual}",
                                  "edit": {"path": c["doc"], "old": old, "new": new}})
                continue
            n_old, n_new = doc.count(c["text"]), doc.count(c.get("replacement", "\0"))
            if n_new == 1 and n_old == 0:
                rule = c.get("replacement_true_if")
                if rule and not con.execute(rule).fetchone()[0]:
                    stale.append({"id": c["id"], "doc": c["doc"], "edit": None,
                                  "problem": "the corrected passage is contradicted too; the claim needs a person"})
                continue
            if n_old != 1:
                stale.append({"id": c["id"], "doc": c["doc"], "edit": None,
                              "problem": f"passage found {n_old} times; update docs/claims.yaml"})
                continue
            if not con.execute(c["true_if"]).fetchone()[0]:
                stale.append({"id": c["id"], "doc": c["doc"], "problem": "contradicted by the warehouse",
                              "edit": {"path": c["doc"], "old": c["text"], "new": c["replacement"]}})
    return stale
