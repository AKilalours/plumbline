"""Run the whole pipeline at a given size and record time and peak memory per stage.

    python scripts/scale_run.py --n-people 1000000

Writes data/scale/n<N>.json. Peak memory is the most the process had used by the end of each stage,
so a stage that needs less than an earlier one shows the earlier peak; read the first stage at which
it jumps.
"""
from __future__ import annotations

import argparse
import json
import platform
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plumbline.__main__ import main, peak_memory_mb  # noqa: E402
from plumbline.synth.config import SynthConfig  # noqa: E402
from plumbline.synth.generate import generate  # noqa: E402
from plumbline.synth.reference import load_reference  # noqa: E402


def run(n: int, seed: int, ref: Path, out: Path) -> dict:
    run_dir = out / f"seed{seed}_n{n}"
    wh = out / f"warehouse_n{n}.duckdb"
    t = time.time()
    if not (run_dir / "deliveries.json").exists():
        generate(load_reference(ref), SynthConfig(seed=seed, n_people=n), run_dir)
    gen = {"seconds": round(time.time() - t, 1), "peak_memory_mb": peak_memory_mb()}
    import contextlib
    import io
    buf = io.StringIO()
    t = time.time()
    with contextlib.redirect_stdout(buf):
        code = main(["--run-dir", str(run_dir), "--warehouse", str(wh), "--score", "--fresh", "--ref", str(ref),
                     "--models", str(out / f"models_n{n}")])
    text = buf.getvalue()
    summary = json.loads(text[text.index("{"):]) if "{" in text else {}
    return {"n_people": n, "exit_code": code, "machine": f"{platform.system()} {platform.machine()}",
            "python": platform.python_version(), "generate": gen,
            "pipeline_seconds": round(time.time() - t, 1),
            "stages": {k: {"seconds": v.get("seconds"), "peak_memory_mb": v.get("peak_memory_mb")} for k, v in summary.items()},
            "summary": summary}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--n-people", type=int, required=True)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--ref", default="data/reference")
    ap.add_argument("--out", default="data/scale")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    r = run(a.n_people, a.seed, Path(a.ref), out)
    (out / f"n{a.n_people}.json").write_text(json.dumps(r, indent=2, default=str) + "\n")
    print(json.dumps({k: r[k] for k in ("n_people", "exit_code", "machine", "generate", "pipeline_seconds", "stages")}, indent=2))
