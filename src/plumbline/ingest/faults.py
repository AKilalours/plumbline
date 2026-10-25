"""Break delivered files on purpose, the way real feeds break, to prove the loader survives.

Each fault writes a NEW file next to an existing delivery (named `...__fault<n>_<kind>.csv`), as if
the vendor re-sent something. The originals are never modified. Every fault has a known correct
outcome: either it changes nothing in the current tables, or its bad rows land in quarantine, or the
whole file is rejected.
"""
from __future__ import annotations

import csv
import io
from pathlib import Path

import numpy as np

KINDS = ("exact_resend", "partial_resend", "whitespace_case_resend", "truncated",
         "renamed_required_column", "extra_column", "bad_values")


def _read(path: Path) -> tuple[list[str], list[list[str]]]:
    rows = list(csv.reader(io.StringIO(path.read_text())))
    return rows[0], rows[1:]


def _write(path: Path, header: list[str], rows: list[list[str]]) -> None:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(header)
    w.writerows(rows)
    path.write_text(buf.getvalue())


def inject(run_dir: str | Path, rng: np.random.Generator, kind: str, n: int,
           sources: tuple[str, ...] = ("crm", "vendor", "email")) -> Path:
    run_dir = Path(run_dir)
    files = sorted(p for s in sources for p in (run_dir / "sources" / s).glob("*.csv") if "__fault" not in p.name)
    src = files[int(rng.integers(0, len(files)))]
    out = src.with_name(f"{src.stem}__fault{n:03d}_{kind}.csv")
    header, rows = _read(src)
    if kind == "exact_resend":
        out.write_bytes(src.read_bytes())
    elif kind == "partial_resend":
        keep = rng.random(len(rows)) < 0.3
        sub = [r for r, k in zip(rows, keep) if k]
        rng.shuffle(sub)
        _write(out, header, sub)
    elif kind == "whitespace_case_resend":
        # Same facts, different bytes: padded fields, doubled spaces, upper-case emails.
        ei = header.index("email") if "email" in header else -1
        new = []
        for r in rows:
            r = [f"  {v.replace(' ', '  ')} " if v else v for v in r]
            if ei >= 0:
                r[ei] = r[ei].upper()
            new.append(r)
        _write(out, header, new)
    elif kind == "truncated":
        body = src.read_bytes()
        first_nl = body.index(b"\n") + 1
        cut = int(rng.integers(first_nl + 1, len(body)))
        out.write_bytes(body[:cut])
    elif kind == "renamed_required_column":
        h = list(header)
        h[0] = h[0] + "_renamed"      # column 0 is always the key
        _write(out, h, rows)
    elif kind == "extra_column":
        _write(out, header + ["vendor_notes"], [r + ["n/a"] for r in rows])
    elif kind == "bad_values":
        bad = []
        for r in rows[: max(1, len(rows) // 10)]:
            r = list(r)
            for col, val in (("zip", "ABCDE"), ("phone", "12"), ("event_ts", "not-a-time"), ("est_income_band", "9")):
                if col in header:
                    r[header.index(col)] = val
            bad.append(r)
        _write(out, header, bad)
    else:
        raise ValueError(f"unknown fault {kind}")
    return out
