"""The knowledge index: the team's own documents, split at headings and searched with BM25.

Why BM25 and not embeddings: the corpus is a few thousand words of exact terms (table names,
command names, metric names) where keyword match is what matters, it runs offline with no model,
and its scores are explainable. The answer layer is separate, so retrieval can be swapped without
touching how answers are cited or refused.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

DEFAULT_DOCS = ["README.md", "docs/*.md", "docs/runbooks/*.md"]
MAX_WORDS = 180
STOP = set("""a an and are as at be by can do does for from has have how i if in into is it its
of on or our so that the their them then there these this to was we what when where which who why
will with you your not no yes did done get got than also only any each more most""".split())
_TOKEN = re.compile(r"[a-z0-9_]+(?:\.[a-z0-9_]+)*")


def tokens(text: str) -> list[str]:
    out = []
    for t in _TOKEN.findall(text.lower()):
        if t in STOP or len(t) < 2:
            continue
        if len(t) > 4 and t.endswith("s") and not t.endswith("ss"):
            t = t[:-1]          # a light plural fold: "files" and "file" match
        out.append(t)
    return out


@dataclass(frozen=True)
class Chunk:
    source: str          # path#heading-slug, the citation
    heading: str
    text: str


def _slug(h: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", h.lower()).strip("-")


def split_markdown(path: str, text: str) -> list[Chunk]:
    """One chunk per heading section; long sections split on paragraph boundaries, each part keeping
    its heading so a passage is never cited without its context."""
    chunks, heading, buf = [], Path(path).stem, []

    def flush():
        paras = [p.strip() for p in "\n".join(buf).split("\n\n") if p.strip()]
        part, words = [], 0
        for p in paras:
            n = len(p.split())
            if part and words + n > MAX_WORDS:
                chunks.append(Chunk(f"{path}#{_slug(heading)}", heading, "\n\n".join(part)))
                part, words = [], 0
            part.append(p)
            words += n
        if part:
            chunks.append(Chunk(f"{path}#{_slug(heading)}", heading, "\n\n".join(part)))

    for line in text.splitlines():
        m = re.match(r"^(#{1,4})\s+(.*)", line)
        if m:
            flush()
            heading, buf = m.group(2).strip(), []
        else:
            buf.append(line)
    flush()
    return chunks


class Index:
    def __init__(self, chunks: list[Chunk], k1: float = 1.5, b: float = 0.75):
        self.chunks = chunks
        self.k1, self.b = k1, b
        self.tf = [Counter(tokens(c.heading + " " + c.text)) for c in chunks]
        self.len = [sum(t.values()) for t in self.tf]
        self.avg = sum(self.len) / max(len(self.len), 1)
        df = Counter(w for t in self.tf for w in t)
        n = len(chunks)
        self.idf = {w: math.log(1 + (n - d + 0.5) / (d + 0.5)) for w, d in df.items()}

    @classmethod
    def from_repo(cls, root: str | Path, patterns: list[str] = DEFAULT_DOCS) -> "Index":
        root = Path(root)
        chunks = []
        for pat in patterns:
            for p in sorted(root.glob(pat)):
                chunks += split_markdown(p.relative_to(root).as_posix(), p.read_text())
        return cls(chunks)

    def search(self, query: str, k: int = 3) -> list[tuple[Chunk, float, float]]:
        """-> [(chunk, bm25 score, coverage)] where coverage is the share of the query's idf weight
        the chunk contains: how much of the question this passage actually speaks to."""
        q_all = list(dict.fromkeys(tokens(query)))
        q = [w for w in q_all if w in self.idf]
        # A word no document contains still counts against coverage, at the highest weight: it is
        # the strongest sign the question is outside what the documents cover. (Dropping it once
        # made "what is our parental leave policy" look fully covered by any passage with "policy".)
        unseen = math.log(1 + (len(self.chunks) + 0.5) / 0.5)
        total = sum(self.idf.get(w, unseen) for w in q_all) or 1.0
        scored = []
        for i, tf in enumerate(self.tf):
            s, cov = 0.0, 0.0
            for w in q:
                f = tf.get(w, 0)
                if f:
                    s += self.idf[w] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * self.len[i] / self.avg))
                    cov += self.idf[w]
            if s > 0:
                scored.append((self.chunks[i], s, cov / total))
        scored.sort(key=lambda x: (-x[1], x[0].source))
        return scored[:k]


def answer_units(text: str) -> list[str]:
    """Bullets, table rows and paragraphs, with wrapped continuation lines joined. Runbooks answer
    in bullets (the symptom, then the remedy), and the remedy often shares no words with the
    question, so the unit quoted must be the whole bullet, not the sentence that matched."""
    units, cur, header = [], [], None
    lines = text.splitlines()
    for j, line in enumerate(lines):
        st = line.strip()
        # A table row means nothing without its column names (the "F1" is in the header, the
        # 0.981 in the row), so every row is quoted with its header.
        nxt = lines[j + 1].strip() if j + 1 < len(lines) else ""
        if st.startswith("|") and nxt.startswith("|") and set(nxt) <= set("|-: "):
            header = st
            continue
        if not st.startswith("|"):
            header = None
        starts = st.startswith(("- ", "* ", "|")) or re.match(r"^\d+\.\s", st) or not st
        if starts and cur:
            units.append(" ".join(cur))
            cur = []
        if st and not set(st) <= set("|-: "):        # skip table separator rows
            cur.append(st)
        if st.startswith("|"):
            row = " ".join(cur)
            units.append(f"{header} {row}" if header and not set(st) <= set("|-: ") else row)
            cur = []
    if cur:
        units.append(" ".join(cur))
    return [u for u in units if len(u) > 20 and not u.startswith("```")]


def best_sentences(text: str, query: str, n: int = 2, idf: dict | None = None) -> list[str]:
    """The answer units of a passage that best match the query, in reading order. Words count by
    rarity (idf), so "duplicate_in_delivery" outweighs "rows"."""
    q = set(tokens(query))
    w = idf or {}
    parts = answer_units(text)
    score = [sum(w.get(t, 1.0) for t in q & set(tokens(p))) for p in parts]
    ranked = sorted(range(len(parts)), key=lambda i: -score[i])[:n]
    return [parts[i] for i in sorted(ranked) if score[i] > 0]
