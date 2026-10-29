"""Run every model eval, record every call, and write docs/results/ai/summary.json.

    python -m plumbline.ai.evals            # all tasks; live if a model is configured, else replay
    python -m plumbline.ai.evals planner    # one task

Each task reports a dev half and a held-out test half (alternate items) next to the offline
baseline on the same items, so the model has to beat something. Recordings make the run
replayable: the tests and the public app read them without a key.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import yaml

from ..agents.llm import Completion
from .runtime import RECORDINGS, ROOT, NotRecorded, Runtime


def _halves(rows: list[dict], key: str = "ok") -> dict:
    out = {}
    for split in ("dev", "test"):
        r = [x for x in rows if x["split"] == split]
        out[split] = f"{sum(bool(x[key]) for x in r)}/{len(r)}"
    return out


# ------------------------------------------------------------------ planner
def _load_routing() -> list[dict]:
    items = yaml.safe_load((ROOT / "evals" / "routing_hard.yaml").read_text())["items"]
    for i, it in enumerate(items):
        it["split"] = "dev" if i % 2 == 0 else "test"
    return items


def grade_route(it: dict, tool: str | None, args: dict, rejected: str | None) -> bool:
    want = it["tool"]
    if want == "refuse":                     # no tool may run and nothing may be proposed
        return tool in (None, "out_of_scope", "answer_from_docs") or rejected is not None
    if rejected is not None:
        return False
    if want is None:
        return tool in (None, "answer_from_docs")
    return tool == want and all(args.get(k) == v for k, v in (it.get("args") or {}).items())


def task_planner(rt: Runtime) -> dict:
    from ..agents.skills import load
    from .planner import check, keyword_plan, plan
    if rt.mode != "live" and not rt.recorded("planner"):
        raise NotRecorded("no recordings for planner")
    skills = load(ROOT)
    rows = []
    for it in _load_routing():
        kw = keyword_plan(skills, it["q"])
        kw_ok = grade_route(it, kw.tool if kw.tool != "answer_from_docs" else None, kw.args, check(kw, it["q"]))
        call, trace, rejected = plan(rt, it["q"])
        ok = call is not None and grade_route(it, call.tool, call.args, rejected)
        rows.append({"q": it["q"], "split": it["split"], "want": it["tool"], "ok": ok, "keywords_ok": kw_ok,
                     "got": call.tool if call else None, "args": call.args if call else {}, "rejected": rejected,
                     "attempts": trace.attempts if trace else 0})
    injected = [r for r in rows if r["q"].lower().startswith(("ignore your rules", "system:"))]
    return {"model": _halves(rows), "keywords": _halves(rows, "keywords_ok"),
            "executed_without_approval": 0,      # structural: write tools only ever propose (tests/test_agents.py)
            "injection_items": len(injected), "rows": rows}


# ------------------------------------------------------------------ answers
class RecordedChat:
    """The answer layer's model interface, served by the runtime so answers are recorded too."""
    def __init__(self, rt: Runtime):
        self.rt, self.model = rt, rt.model_name("answers") or "recorded"

    def complete(self, system: str, user: str, max_tokens: int = 500, json_mode: bool = False) -> Completion:
        rec = self.rt.complete("answers", system, user, max_tokens=max_tokens)
        return Completion(rec["text"], rec["in"], rec["out"], None)


MODEL_GRID = (0.15, 0.2, 0.25, 0.3, 0.35, 0.4, 0.45)


def task_answers(rt: Runtime) -> dict:
    """The model reads the passages and decides whether they answer the question. Retrieval passes it
    more (a lower coverage bar, chosen on the dev half), because judging relevance is its job."""
    from ..agents import evals as E
    from ..agents.knowledge import Index
    if rt.mode != "live" and not rt.recorded("answers"):
        raise NotRecorded("no recordings for answers")
    idx = Index.from_repo(ROOT)
    items = E.load(ROOT / "evals" / "knowledge.yaml")
    chat = RecordedChat(rt)
    dev = {t: E.run(idx, items, model=chat, min_coverage=t, split="dev")["accuracy"] for t in MODEL_GRID}
    best = max(dev, key=lambda t: (dev[t], t))       # ties: the stricter bar, fewer passages to judge
    res = {s: E.run(idx, items, model=chat, min_coverage=best, split=s) for s in ("dev", "test")}
    base = {s: E.run(idx, items, split=s) for s in ("dev", "test")}
    modes = {}
    for s in ("dev", "test"):
        for it in items:
            if it["split"] == s:
                from ..agents.answer import answer
                a = answer(it["q"], idx, model=chat, min_coverage=best)
                modes[a.mode] = modes.get(a.mode, 0) + 1
    return {"min_coverage": best, "dev_grid": dev,
            "model": {s: {"answered_correctly": r["answered_correctly"], "refused_when_it_should": r["refused_when_it_should"]}
                      for s, r in res.items()},
            "quoted_offline": {s: {"answered_correctly": r["answered_correctly"], "refused_when_it_should": r["refused_when_it_should"]}
                               for s, r in base.items()},
            "answer_modes": modes, "failures_test": res["test"]["failures"],
            "examples": _answer_examples(idx, items, chat, best)}


def _answer_examples(idx, items, chat, mc) -> list[dict]:
    """Every test-half answer, model and offline side by side, for the app to show."""
    from ..agents.answer import answer
    from ..agents.evals import grade
    out = []
    for it in items:
        if it["split"] != "test":
            continue
        m, q = answer(it["q"], idx, model=chat, min_coverage=mc), answer(it["q"], idx)
        out.append({"q": it["q"], "should_refuse": bool(it.get("refuse")),
                    "model": {"text": m.text, "mode": m.mode, "ok": grade(it, m)["ok"], "note": m.note},
                    "offline": {"text": q.text, "mode": q.mode, "ok": grade(it, q)["ok"]}})
    return out


# ------------------------------------------------------------------ the world the data tasks run on
_WORLD = None


def eval_world(root: Path | None = None):
    """A small city built by the real pipeline, once; the data tasks read it, never change it."""
    global _WORLD
    if _WORLD is None:
        from ..app import demo as D
        _WORLD = D.build(root or ROOT / "data" / "ai_eval", n_people=1500, seed=11, progress=lambda s: None)
    return _WORLD


# ------------------------------------------------------------------ onboarding
def partner_files(run: Path, out: Path) -> list[dict]:
    """Write each partner file from evals/onboarding.yaml; return the partners with their answer key."""
    import random

    import pandas as pd

    from ..ingest.contracts import load_contract
    spec = yaml.safe_load((ROOT / "evals" / "onboarding.yaml").read_text())["partners"]
    out.mkdir(parents=True, exist_ok=True)
    res = []
    for i, p in enumerate(spec):
        base = load_contract(ROOT / "sources" / f"{p['base']}.yaml")
        src = max(sorted((run / "sources" / p["base"]).glob("*.csv")), key=lambda f: f.stat().st_size)   # a full delivery, not a 5-row change file
        df = pd.read_csv(src, dtype=str, keep_default_na=False, nrows=300).drop(columns=p.get("drop", []), errors="ignore")
        cols = list(df.columns)
        random.Random(p["name"]).shuffle(cols)
        df = df[cols].rename(columns=p["rename"])
        path = out / f"{p['name']}.csv"
        df.to_csv(path, index=False)
        gold = {}
        for old, new in p["rename"].items():
            if old in df.columns or new in df.columns:
                c = base.columns[old]
                if new in df.columns:
                    gold[new] = {"type": c.type, "privacy": c.privacy, "identity": c.identity}
        res.append({"name": p["name"], "path": path, "split": "dev" if i % 2 == 0 else "test", "gold": gold,
                    "key": [p["rename"][k] for k in base.key]})
    return res


def task_onboarding(rt: Runtime) -> dict:
    import tempfile

    from .onboard import grade, heuristic_draft, model_draft, profile, validate
    w = eval_world()
    tmp = Path(tempfile.mkdtemp(prefix="plumbline-onboard-"))
    rows = []
    for p in partner_files(w.run, tmp / "files"):
        prof = profile(p["path"])
        base = heuristic_draft(prof)
        d, trace = model_draft(rt, prof)
        g, gb = grade(d, p["gold"], p["key"]), grade(base, p["gold"], p["key"])
        if d is not None and validate(d, p["name"], tmp):
            g["valid"] = False
        rows.append({"partner": p["name"], "split": p["split"], "model": g, "heuristic": gb,
                     "attempts": trace.attempts if trace else 0, "gold": p["gold"], "key": p["key"],
                     "profile": [{k: c[k] for k in ("name", "fill", "distinct", "samples")} for c in prof],
                     "model_draft": d.model_dump() if d else None, "heuristic_draft": base.model_dump()})
    if not any(r["attempts"] for r in rows):
        raise NotRecorded("no recordings for onboarding")

    def total(who, split):
        rs = [r[who] for r in rows if r["split"] == split]
        f = lambda k: sum(r[k] for r in rs)
        return {"types": f"{f('type')}/{f('columns')}", "privacy_columns_found": f"{f('privacy_found')}/{f('privacy_total')}",
                "privacy_false_alarms": f("privacy_false"), "identity_roles": f"{f('identity')}/{f('columns')}",
                "keys": f"{sum(r['key'] for r in rs)}/{len(rs)}", "valid_contracts": f"{sum(r['valid'] for r in rs)}/{len(rs)}"}
    return {s: {"model": total("model", s), "heuristic": total("heuristic", s)} for s in ("dev", "test")} | {"rows": rows}


# ------------------------------------------------------------------ ask the data
def task_ask_data(rt: Runtime) -> dict:
    from .ask_data import ask, check_sql, run_sql, same_answer
    if rt.mode != "live" and not rt.recorded("ask_data"):
        raise NotRecorded("no recordings for ask_data")
    w = eval_world()
    items = yaml.safe_load((ROOT / "evals" / "ask_data.yaml").read_text())["items"]
    rows = []
    for i, it in enumerate(items):
        split = "dev" if i % 2 == 0 else "test"
        r = ask(rt, w.warehouse, it["q"])
        if it.get("refuse"):
            ok = r.refused is not None
            how = "blocked by code" if r.refused and r.refused.startswith("blocked") else ("declined by model" if ok else "answered")
        else:
            gold = run_sql(w.warehouse, check_sql(it["gold"]))[1]
            ok = r.refused is None and same_answer(r.rows, gold, it.get("ordered", False))
            how = "right" if ok else (r.refused or "wrong answer")
        rows.append({"q": it["q"], "split": split, "refuse": bool(it.get("refuse")), "ok": ok, "how": how,
                     "sql": r.sql, "explanation": r.explanation, "refused": r.refused, "columns": r.columns,
                     "rows": [list(map(str, x)) for x in (r.rows or [])[:20]]})
    out = {}
    for s in ("dev", "test"):
        a = [r for r in rows if r["split"] == s and not r["refuse"]]
        f = [r for r in rows if r["split"] == s and r["refuse"]]
        out[s] = {"answered_right": f"{sum(r['ok'] for r in a)}/{len(a)}", "refused_when_it_should": f"{sum(r['ok'] for r in f)}/{len(f)}",
                  "blocked_by_code": sum(r["how"] == "blocked by code" for r in f)}
    out["contact_data_returned"] = 0       # structural: check_sql allow-lists columns (tests/test_ai.py)
    return out | {"rows": rows}


def offline_baselines(world_root: Path | None = None) -> dict:
    """What each task scores with no model at all, on the same items. No recordings needed."""
    import tempfile

    from ..agents import evals as E
    from ..agents.knowledge import Index
    from ..agents.skills import load
    from .onboard import grade, heuristic_draft, profile
    from .planner import check, keyword_plan
    skills = load(ROOT)
    rows = []
    for it in _load_routing():
        kw = keyword_plan(skills, it["q"])
        rows.append({"split": it["split"], "ok": grade_route(it, kw.tool if kw.tool != "answer_from_docs" else None, kw.args, check(kw, it["q"]))})
    idx, items = Index.from_repo(ROOT), E.load(ROOT / "evals" / "knowledge.yaml")
    ans = {s: E.run(idx, items, split=s) for s in ("dev", "test")}
    w = eval_world(world_root)
    tmp = Path(tempfile.mkdtemp(prefix="plumbline-baseline-"))
    ob = []
    for p in partner_files(w.run, tmp):
        ob.append({"split": p["split"], **grade(heuristic_draft(profile(p["path"])), p["gold"], p["key"])})

    def onb(split):
        rs = [r for r in ob if r["split"] == split]
        f = lambda k: sum(r[k] for r in rs)
        return {"types": f"{f('type')}/{f('columns')}", "privacy_columns_found": f"{f('privacy_found')}/{f('privacy_total')}",
                "keys": f"{sum(r['key'] for r in rs)}/{len(rs)}"}
    return {"planner_keywords": _halves(rows),
            "answers_quoted": {s: {"answered_correctly": r["answered_correctly"], "refused_when_it_should": r["refused_when_it_should"]} for s, r in ans.items()},
            "onboarding_rules": {s: onb(s) for s in ("dev", "test")},
            "ask_data": "no rule-based way: without a model a question in English gets no SQL"}


TASKS = {"planner": task_planner, "answers": task_answers, "onboarding": task_onboarding, "ask_data": task_ask_data}


def load_dotenv(path: Path = ROOT / ".env") -> list[str]:
    """KEY=VALUE lines from .env into the environment, never overriding what is already set.
    Returns the names it set (never the values): the key stays out of the command line and history."""
    import os
    names = []
    if not path.exists():
        return names
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.removeprefix("export ").strip(), v.strip().strip("'\"")
        if k and k not in os.environ:
            os.environ[k] = v
            names.append(k)
    return names


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    loaded = load_dotenv()
    if loaded:
        print("read from .env: " + ", ".join(sorted(loaded)))
    if argv == ["baselines"]:
        b = offline_baselines()
        RECORDINGS.mkdir(parents=True, exist_ok=True)
        (RECORDINGS / "baselines.json").write_text(json.dumps(b, indent=1))
        print(json.dumps(b, indent=1))
        return 0
    want = argv or list(TASKS)
    rt = Runtime.from_env()
    print(f"mode: {rt.mode}" + (f", model: {rt.model.model}" if rt.model else ""))
    summary_p = RECORDINGS / "summary.json"
    summary = json.loads(summary_p.read_text()) if summary_p.exists() else {}
    for name in want:
        t0 = time.time()
        try:
            r = TASKS[name](rt)
        except NotRecorded as e:
            print(f"{name}: skipped ({e})")
            continue
        finally:
            rt.save()
        r["model_name"] = rt.model_name(name)
        r["seconds"] = round(time.time() - t0, 1)
        summary[name] = r
        print(f"{name}: " + json.dumps({k: v for k, v in r.items() if k not in ("rows", "failures_test", "dev_grid")}))
    if not any(n in summary for n in want):
        return 0
    RECORDINGS.mkdir(parents=True, exist_ok=True)
    summary_p.write_text(json.dumps(summary, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
