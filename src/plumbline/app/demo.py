"""Build the world the app shows: a small synthetic city, generated and run through the same pipeline
a laptop runs (`python -m plumbline --score`), in front of the viewer. Nothing is a canned database.

The world is small on purpose. Identity, deletion, households and addresses behave the same at 4,000
people as at 1,000,000 (the scale tables say so), and a hosted app has a few GB of memory. The models
and the experiment need a larger world to mean anything, so the app shows those from saved runs
(docs/results/scale.json) and says so.
"""
from __future__ import annotations

import contextlib
import io
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

REPO = Path(__file__).resolve().parents[3]
# Invented tax lots and zip incomes: the same files the tests use, safe to ship with a public app.
REFERENCE = REPO / "tests" / "fixtures" / "reference"
CONTRACTS = REPO / "sources"
STAGES = {"generate": "Generating the city and its answer key",
          "store_build": "Opening the online store",
          "store_sync": "Pulling orders from the store's API (with injected 429s, 503s and dropped pages)",
          "ingest": "Loading every delivery against its contract",
          "identity": "Resolving who is who",
          "person_deletion": "Carrying out deletion requests",
          "products": "Building households, addresses and the audience with dbt"}


@dataclass(frozen=True)
class Demo:
    root: Path
    run: Path
    warehouse: Path
    n_people: int
    seed: int
    summary: dict


class _Progress(io.TextIOBase):
    """Collects what the pipeline prints and reports each "[stage] ..." line as it starts."""

    def __init__(self, cb: Callable[[str], None]):
        self.parts: list[str] = []
        self.cb = cb

    def write(self, s: str) -> int:
        self.parts.append(s)
        for line in s.splitlines():
            if line.startswith("[") and line.rstrip().endswith("..."):
                self.cb(line[1:line.index("]")])
        return len(s)

    def text(self) -> str:
        return "".join(self.parts)


def build(root: str | Path, n_people: int = 4000, seed: int = 11,
          progress: Callable[[str], None] | None = None) -> Demo:
    """Generate and process the demo world under root, or reuse the one already there."""
    from ..__main__ import main
    from ..synth.config import SynthConfig
    from ..synth.generate import generate
    from ..synth.reference import load_reference

    root = Path(root)
    run, wh, done = root / f"seed{seed}_n{n_people}", root / "warehouse.duckdb", root / "summary.json"
    report = progress or (lambda _: None)
    if done.exists() and wh.exists():
        s = json.loads(done.read_text())
        if (s.get("n_people"), s.get("seed")) == (n_people, seed):
            return Demo(root, run, wh, n_people, seed, s["pipeline"])
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True)
    report("generate")
    generate(load_reference(REFERENCE), SynthConfig(seed=seed, n_people=n_people), run)
    out = _Progress(report)
    with contextlib.redirect_stdout(out):
        code = main(["--run-dir", str(run), "--warehouse", str(wh), "--contracts", str(CONTRACTS),
                     "--ref", str(REFERENCE), "--models", str(root / "models"), "--score", "--fresh", "--no-model"])
    text = out.text()
    if code != 0 or "{" not in text:
        raise RuntimeError(f"the pipeline stopped (exit {code}): {text[-1500:]}")
    summary = json.loads(text[text.index("{"):])
    done.write_text(json.dumps({"n_people": n_people, "seed": seed, "pipeline": summary}, indent=2))
    return Demo(root, run, wh, n_people, seed, summary)
