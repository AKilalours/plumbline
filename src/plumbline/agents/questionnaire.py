"""Security questionnaires, answered from docs/security.md and the runbooks, one row per question.

Every answer cites the section it comes from. A question the documents do not settle is marked for a
person, not answered: a questionnaire is signed as a statement of fact, so a wrong "yes" is worse
than a blank. The compliance owner still owns the answers; this drafts them and says which need them.
"""
from __future__ import annotations

import csv
import time
from pathlib import Path

from . import audit
from .answer import answer
from .knowledge import Index

SOURCES = ["docs/security.md", "docs/runbooks/*.md"]
# Questionnaire items are long and full of words the controls page never uses ("handle", "traffic"),
# so the general 0.5 refused answerable items. Chosen on the dev half of evals/questionnaire.yaml with
# the same rule as the knowledge eval (evals.choose_threshold, grid=GRID): 0.25 and 0.3 score
# perfectly there and the one closest to 0.5 wins. It sits on a tie: until the runbook gained the
# date-of-birth review case, 0.15 to 0.25 were the perfect ones. The documents change the scores, so a test re-runs
# the choice and fails if this constant no longer matches it.
GRID = (0.15, 0.2, 0.25, 0.3, 0.35, 0.4, 0.45, 0.5)
MIN_COVERAGE = 0.3


def index(repo) -> Index:
    return Index.from_repo(repo, SOURCES)


def answer_all(ctx, questions: list[str], user: str = "cli") -> list[dict]:
    ix = index(ctx.repo)
    ctx.open()
    rows = []
    for q in questions:
        t0 = time.time()
        a = answer(q, ix, model=ctx.model, min_coverage=MIN_COVERAGE)
        status = "needs a person" if a.refused else "drafted"
        rows.append({"question": q, "status": status, "answer": "" if a.refused else a.text,
                     "sources": "; ".join(a.sources)})
        audit.log(ctx.agent_con, actor=user, channel="cli", skill="security_questionnaire", action="answer",
                  outcome="refused" if a.refused else a.mode, detail={"sources": a.sources},
                  usage=a.usage, latency_ms=int((time.time() - t0) * 1000))
    return rows


def run_file(ctx, src: str | Path, dst: str | Path, user: str = "cli") -> dict:
    """CSV in with a `question` column, CSV out with status, draft answer and sources."""
    with open(src, newline="") as f:
        qs = [r["question"].strip() for r in csv.DictReader(f) if r.get("question", "").strip()]
    rows = answer_all(ctx, qs, user)
    with open(dst, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["question", "status", "answer", "sources"])
        w.writeheader()
        w.writerows(rows)
    return {"questions": len(rows), "drafted": sum(r["status"] == "drafted" for r in rows),
            "needs_a_person": sum(r["status"] != "drafted" for r in rows), "written": str(dst)}
