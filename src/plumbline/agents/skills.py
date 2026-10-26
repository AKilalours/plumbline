"""The skill library: one YAML file per skill, with an owner, the tools it may use, and an eval.
A skill that names an unknown tool, has no owner, or has no eval file is refused at load."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .tools import TOOLS


class SkillError(ValueError):
    pass


@dataclass
class Intent:
    tool: str
    when: list[re.Pattern]
    args: dict[str, re.Pattern] = field(default_factory=dict)


@dataclass
class Skill:
    name: str
    owner: str
    description: str
    tools: list[str]
    eval: str
    intents: list[Intent]


def load(repo) -> dict[str, Skill]:
    repo = Path(repo)
    out = {}
    for p in sorted((repo / "skills").glob("*.yaml")):
        d = yaml.safe_load(p.read_text())
        for k in ("name", "owner", "description", "tools", "eval"):
            if not d.get(k) and d.get(k) != []:
                raise SkillError(f"{p.name}: missing {k}")
        unknown = [t for t in d["tools"] if t not in TOOLS]
        if unknown:
            raise SkillError(f"{p.name}: unknown tools {unknown}")
        if not (repo / d["eval"]).exists():
            raise SkillError(f"{p.name}: eval file {d['eval']} does not exist")
        intents = []
        for it in d.get("intents", []):
            if it["tool"] not in d["tools"]:
                raise SkillError(f"{p.name}: intent uses {it['tool']}, which the skill may not use")
            intents.append(Intent(it["tool"], [re.compile(w, re.I) for w in it["when"]],
                                  {k: re.compile(v, re.I) for k, v in (it.get("args") or {}).items()}))
        out[d["name"]] = Skill(d["name"], d["owner"], d["description"], d["tools"], d["eval"], intents)
    if "knowledge_qa" not in out:
        raise SkillError("knowledge_qa is the fallback skill and must exist")
    return out


# "How does the review queue get filled?" names the queue but asks how it works, not what is in it.
# Questions about how or why something works go to the documents, whatever they name; questions
# about the current state ("what's in", "how many", "is ... published") go to the tools.
EXPLAIN = re.compile(r"^\s*(how (does|do|is|are|did) (?!many)|why\b|what (does|do) .+ mean\b|explain\b)", re.I)


def route(skills: dict[str, Skill], text: str) -> tuple[Skill, Intent | None, dict]:
    """First matching intent wins, in file order, write intents listed first within each skill.
    Anything that matches no intent, or asks how or why something works, is a question for the documents."""
    if EXPLAIN.search(text):
        return skills["knowledge_qa"], None, {}
    for s in skills.values():
        for it in s.intents:
            if any(w.search(text) for w in it.when):
                args = {}
                for k, rx in it.args.items():
                    m = rx.search(text)
                    if m:
                        args[k] = (m.group(1) if m.groups() else m.group(0)).lower() if k != "person_key" else m.group(1)
                return s, it, args
    return skills["knowledge_qa"], None, {}
