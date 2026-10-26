"""Run the knowledge eval: right answers with the right source, and refusals where the documents
have nothing. Alternate items form a dev half (for choosing the refusal threshold) and a test half."""
from __future__ import annotations

from pathlib import Path

import yaml

from .answer import answer
from .knowledge import Index


def load(path: str | Path) -> list[dict]:
    items = yaml.safe_load(Path(path).read_text())["items"]
    for it in items:   # YAML reads `off` as false and `[33,287]` as two numbers; a term must be text
        bad = [t for t in it.get("terms", []) if not isinstance(t, str)]
        if bad:
            raise ValueError(f"{path}: quote the terms {bad} in {it['q']!r}")
    for i, it in enumerate(items):
        it["split"] = "dev" if i % 2 == 0 else "test"
    return items


def grade(it: dict, a) -> dict:
    if it.get("refuse"):
        return {"ok": a.refused, "kind": "unanswerable"}
    if a.refused:
        return {"ok": False, "kind": "answerable", "why": "refused"}
    src_ok = any(s.startswith(e) for s in a.sources for e in it["source"])
    # whitespace collapsed on both sides: a right answer whose phrase wraps across a line still counts
    text = " ".join(a.text.lower().split())
    missing = [t for t in it["terms"] if " ".join(str(t).lower().split()) not in text]
    why = None if src_ok and not missing else ("wrong source" if not src_ok else f"missing {missing}")
    return {"ok": src_ok and not missing, "kind": "answerable", "why": why}


def run(index: Index, items: list[dict], model=None, min_coverage: float | None = None, split: str | None = None) -> dict:
    from .answer import MIN_COVERAGE
    mc = MIN_COVERAGE if min_coverage is None else min_coverage
    rows = []
    for it in items:
        if split and it["split"] != split:
            continue
        a = answer(it["q"], index, model=model, min_coverage=mc)
        rows.append({"q": it["q"], "split": it["split"], **grade(it, a), "mode": a.mode, "coverage": round(a.coverage, 3)})
    ans = [r for r in rows if r["kind"] == "answerable"]
    un = [r for r in rows if r["kind"] == "unanswerable"]
    wrong_refusals = sum(1 for r in ans if r.get("why") == "refused")
    return {"min_coverage": mc, "items": len(rows),
            "answered_correctly": f"{sum(r['ok'] for r in ans)}/{len(ans)}",
            "refused_when_it_should": f"{sum(r['ok'] for r in un)}/{len(un)}",
            "refused_a_question_it_could_answer": wrong_refusals,
            "accuracy": round(sum(r["ok"] for r in rows) / max(len(rows), 1), 3),
            "failures": [{k: r[k] for k in ("q", "why", "coverage") if k in r} for r in rows if not r["ok"]]}


def choose_threshold(index: Index, items: list[dict], grid=(0.3, 0.35, 0.4, 0.45, 0.5, 0.55, 0.6, 0.65, 0.7)) -> tuple[float, dict]:
    """Pick the refusal threshold on the dev half only; among equal scores, the one closest to 0.5."""
    scores = {t: run(index, items, min_coverage=t, split="dev")["accuracy"] for t in grid}
    best = max(scores, key=lambda t: (scores[t], -abs(t - 0.5)))
    return best, scores


def run_routing(skills: dict, items: list[dict]) -> dict:
    from .skills import route
    rows = []
    for it in yaml.safe_load(Path(items).read_text())["items"] if isinstance(items, (str, Path)) else items:
        s, intent, _ = route(skills, it["q"])
        got = (s.name, intent.tool if intent else None)
        rows.append({"q": it["q"], "ok": got == (it["skill"], it["tool"]), "got": got, "want": (it["skill"], it["tool"])})
    return {"routed_correctly": f"{sum(r['ok'] for r in rows)}/{len(rows)}",
            "failures": [r for r in rows if not r["ok"]]}
