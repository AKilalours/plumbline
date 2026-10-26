"""Answer a question from the documents, with a citation on every claim, or say it is not there.

Offline (no model configured) the answer is the most relevant sentences, quoted, each cited. With a
model, the model may only use the retrieved passages and must cite each sentence as [n]. Its answer
is then checked: every sentence needs a citation and every citation must point at a passage given.
An answer that fails the check is not shown; the quoted answer is shown instead and the reason is
logged. The model can make the wording better; it cannot add a claim nobody can trace.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .knowledge import Index, best_sentences
from .llm import ChatModel, Completion, LLMError

MIN_COVERAGE = 0.5          # chosen on the dev half of evals/knowledge.yaml; reported on the test half
REFUSAL = "I could not find this in our documents."

SYSTEM = ("You answer questions for the team that runs a data platform, using only the numbered "
          "passages given. After every sentence put the number of the passage it comes from, like [1]. "
          "If the passages do not answer the question, reply with exactly NOT_IN_SOURCES. "
          "Be brief: at most four sentences.")


@dataclass
class Answer:
    text: str
    sources: list[str]
    refused: bool
    mode: str                       # quoted | model | refused
    coverage: float
    note: str | None = None
    usage: Completion | None = None
    passages: list[str] = field(default_factory=list)


def _cited_ok(text: str, n: int) -> tuple[bool, str]:
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if len(s.strip()) > 3]
    if not sentences:
        return False, "empty answer"
    for s in sentences:
        cites = [int(c) for c in re.findall(r"\[(\d+)\]", s)]
        if not cites:
            return False, f"sentence without a citation: {s[:60]!r}"
        if any(c < 1 or c > n for c in cites):
            return False, f"citation to a passage that was not given: {cites}"
    return True, ""


def answer(question: str, index: Index, model: ChatModel | None = None, min_coverage: float = MIN_COVERAGE,
           k: int = 3) -> Answer:
    hits = index.search(question, k=k)
    if not hits or hits[0][2] < min_coverage:
        cov = hits[0][2] if hits else 0.0
        near = f" The closest section is {hits[0][0].source}, which does not answer it." if hits else ""
        return Answer(REFUSAL + near, [], True, "refused", cov)
    use = [h for h in hits if h[2] >= min_coverage * 0.8][:2]
    quoted_lines, sources = [], []
    for rank, (c, _, _) in enumerate(use):
        for sent in best_sentences(c.text, question, n=2 if rank == 0 else 1, idf=index.idf):
            quoted_lines.append(f"- {sent} [{c.source}]")
        sources.append(c.source)
    quoted = "\n".join(quoted_lines) or REFUSAL
    base = Answer(quoted, sources, not quoted_lines, "quoted" if quoted_lines else "refused", hits[0][2],
                  passages=[c.source for c, _, _ in use])
    if model is None:
        return base
    numbered = "\n\n".join(f"[{i + 1}] ({c.source})\n{c.text}" for i, (c, _, _) in enumerate(use))
    try:
        comp = model.complete(SYSTEM, f"Passages:\n\n{numbered}\n\nQuestion: {question}")
    except LLMError as e:
        base.note = f"model unavailable, quoted instead: {e}"
        return base
    text = comp.text.strip()
    if text == "NOT_IN_SOURCES":
        return Answer(REFUSAL, [], True, "refused", hits[0][2], note="model found no answer in the passages", usage=comp)
    ok, why = _cited_ok(text, len(use))
    if not ok:
        base.note, base.usage = f"model answer rejected ({why}); quoted instead", comp
        return base
    for i, (c, _, _) in enumerate(use):
        text = text.replace(f"[{i + 1}]", f"[{c.source}]")
    return Answer(text, [c.source for c, _, _ in use], False, "model", hits[0][2], usage=comp,
                  passages=[c.source for c, _, _ in use])
