"""Add a source in about fifteen lines. The public interface other engineers build on.

You write one thing: how to ask the source for the next page. Everything else is the kit's job,
and every source gets it the same way:

    from plumbline.kit import Source, Page

    class Quakes(Source):
        name = "usgs_quakes"
        def pages(self, cursor):
            since = cursor or "2026-11-01T00:00:00"
            body = self.get_json(URL, updatedafter=since, format="geojson", orderby="time-asc")
            rows = [{"event_id": f["id"], "updated_at": iso_ms(f["properties"]["updated"]),
                     "magnitude": f["properties"]["mag"], "place": f["properties"]["place"]}
                    for f in body["features"]]
            yield Page(rows, cursor=max((r["updated_at"] for r in rows), default=since))

What the kit does for you (each one is tested in tests/test_kit.py, on the worked example):

- **Safe reruns.** A page is written to disk atomically, then the cursor is saved. A crash between pages
  resumes at the last saved cursor; a page read twice adds nothing, because the loader stores each row once.
- **Retries.** `get_json` retries 429 and 5xx with backoff, honours Retry-After, and gives up loudly after
  `max_retries`. Other 4xx are your bug or the source's, so they fail at once with the reason. An API that
  says in a header that an answer is stale (`stale_when`) is asked again, since a stale page can look like
  the end. `self.last_headers` holds the last answer's headers.
- **The contract decides the columns.** Only columns the reviewed contract lists are written. Anything
  else is counted as drift on the run line and held back, never silently loaded. A source without a
  contract yet gets a draft written for a person to read (`draft`).
- **Personal fields stay out** of the draft unless a person adds them to the contract with a privacy rule.
- **One report** per run: rows landed, loaded, quarantined, held back, retries, seconds.

    python -m plumbline.kit draft  my_source.py        # first page -> sources/drafts/<name>.yaml
    python -m plumbline.kit run    my_source.py        # sync + load under sources/<name>.yaml
    python -m plumbline.kit verify my_source.py        # the four promises, checked on your source

`docs/add_a_source.md` walks through it end to end.
"""
from __future__ import annotations

import csv
import io
import json
import os
import random
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

ROOT = Path(__file__).resolve().parents[2]
PERSONAL = re.compile(r"owner|applicant|contact|signature|phone|e_?mail|ssn|birth|dob|passport|address|street", re.I)
RETRY = {429, 500, 502, 503, 504}


class SourceError(RuntimeError):
    """The source refused in a way a retry will not fix. The message says why."""


@dataclass
class Page:
    rows: list[dict]
    cursor: Any = None          # where the next run should start; None keeps the last one


@dataclass
class Report:
    name: str
    pages: int = 0
    rows_landed: int = 0
    rows_loaded: int = 0
    rows_quarantined: int = 0
    rows_already_present: int = 0
    held_back: list[str] = field(default_factory=list)
    requests: int = 0
    retries: int = 0
    resumed_from: Any = None
    cursor: Any = None
    seconds: float = 0.0
    quarantine_reasons: dict = field(default_factory=dict)
    finished: bool = False          # the source had no more pages (not stopped by max_pages)
    stale_answers: int = 0          # answers marked stale by the source's stale_when, asked again

    def line(self) -> str:
        held = f", held back {', '.join(self.held_back)}" if self.held_back else ""
        held += f", {self.stale_answers} stale answers asked again" if self.stale_answers else ""
        return (f"{self.name}: {self.rows_landed} rows landed in {self.pages} pages, {self.rows_loaded} new, "
                f"{self.rows_already_present} already held, {self.rows_quarantined} quarantined, "
                f"{self.retries} retries{held}, {self.seconds:.1f}s")


class Source:
    """Subclass this. Set `name`; write `pages(cursor)`. Optionally set `key` and `updated` to the
    names of the id column and the last-changed column, which the draft uses as the contract's key."""

    name: str = ""
    key: str | None = None
    updated: str | None = None
    max_retries: int = 6
    base_delay: float = 0.5
    timeout: float = 30.0
    headers: dict = {}
    stale_when: dict = {}           # response header -> value that means "this answer is from a copy that is behind"

    def __init__(self) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9_]*", self.name or ""):
            raise ValueError(f"{type(self).__name__}.name must be lowercase snake_case, got {self.name!r}")
        self._requests = self._retries = self._stale = 0
        self.last_headers: dict = {}        # the headers of the last answer, for a source that needs them
        self._sleep = time.sleep

    def pages(self, cursor) -> Iterator[Page]:
        raise NotImplementedError("write pages(cursor): yield Page(rows, cursor=...) for each page")

    def __repr__(self) -> str:      # headers may hold a token; never show them
        return f"<{type(self).__name__} {self.name}>"

    def get_json(self, url: str, **params) -> Any:
        """GET url?params as JSON, with retries for 429 and 5xx. Raises SourceError otherwise."""
        full = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(params) if params else url
        for attempt in range(self.max_retries + 1):
            self._requests += 1
            try:
                req = urllib.request.Request(full, headers={"Accept": "application/json", **self.headers})
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    self.last_headers = dict(r.headers.items())
                    body = json.loads(r.read().decode())
                if not self._is_stale():
                    return body
                self._stale += 1                     # a stale answer can look like the end of the data: ask again
                wait = None
            except urllib.error.HTTPError as e:
                self.last_headers = dict(e.headers.items()) if e.headers else {}
                if e.code not in RETRY:
                    raise SourceError(f"{self.name}: HTTP {e.code} from {urllib.parse.urlsplit(url).netloc}: "
                                      f"{e.read()[:200].decode(errors='replace')}") from None
                wait = _retry_after(e.headers.get("Retry-After")) if e.code == 429 else None
            except (urllib.error.URLError, TimeoutError, ConnectionError):
                self.last_headers, wait = {}, None
            if attempt == self.max_retries:
                if self._is_stale():
                    raise SourceError(f"{self.name}: answers from {urllib.parse.urlsplit(url).netloc} still marked stale after "
                                      f"{attempt + 1} tries; run again later")
                raise SourceError(f"{self.name}: gave up after {attempt + 1} tries on {urllib.parse.urlsplit(url).netloc}")
            self._retries += 1
            self._sleep(wait if wait is not None else self.base_delay * 2 ** attempt * (0.5 + random.random()))
        raise AssertionError("unreachable")


    def _is_stale(self) -> bool:
        have = {k.lower(): str(v).lower() for k, v in self.last_headers.items()}
        return any(have.get(k.lower()) == str(v).lower() for k, v in self.stale_when.items())


def _retry_after(v: str | None) -> float | None:
    try:
        return min(float(v), 300.0) if v else None
    except ValueError:
        return None


# ------------------------------------------------------------------ landing
def _atomic(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".partial")
    tmp.write_text(text)
    os.replace(tmp, path)


def _cell(v) -> str:
    if v is None:
        return ""
    if isinstance(v, (dict, list)):
        return json.dumps(v, sort_keys=True)
    return str(v)


def sync(src: Source, landing: Path, columns: list[str] | None, *, max_pages: int | None = None,
         after_page=None) -> Report:
    """Land pages as CSV under `landing`, saving the cursor after each page is safely on disk.
    `columns` is the contract's list; None (no contract yet) writes every column seen on the first page."""
    landing.mkdir(parents=True, exist_ok=True)
    sp = landing / "_kit_state.json"
    state = json.loads(sp.read_text()) if sp.exists() else {"cursor": None, "run": 0, "page": 0, "held_back": {}}
    state["run"] += 1
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")    # never reuse a name, even if the state is lost
    rep = Report(src.name, resumed_from=state["cursor"])
    t = time.perf_counter()
    for page in src.pages(state["cursor"]):
        if max_pages and rep.pages >= max_pages:
            break            # checked here, not after landing: a source whose window ends at the limit counts as finished
        rows = [dict(r) for r in page.rows]
        if rows:
            seen = list(dict.fromkeys(k for r in rows for k in r))
            cols = columns if columns is not None else seen
            for k in seen:
                if k not in cols:
                    state["held_back"][k] = state["held_back"].get(k, 0) + 1
                    rep.held_back.append(k) if k not in rep.held_back else None
            buf = io.StringIO()
            w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore", lineterminator="\n")
            w.writeheader()
            w.writerows({c: _cell(r.get(c)) for c in cols} for r in rows)
            state["page"] += 1
            _atomic(landing / f"{src.name}_{stamp}_p{state['page']:06d}.csv", buf.getvalue())
        if page.cursor is not None:
            state["cursor"] = page.cursor
        _atomic(sp, json.dumps(state, indent=1, default=str))      # after the page file: a crash re-reads, never skips
        rep.pages += 1
        rep.rows_landed += len(rows)
        if after_page:
            after_page(rep.pages)
    else:
        rep.finished = True
    rep.cursor, rep.requests, rep.retries, rep.stale_answers = state["cursor"], src._requests, src._retries, src._stale
    rep.seconds = time.perf_counter() - t
    return rep


# ------------------------------------------------------------------ contract, load, draft
def contract_path(src: Source, sources: Path | None = None) -> Path:
    return (sources or ROOT / "sources") / "kit" / f"{src.name}.yaml"


def run(src: Source, *, workdir: Path | None = None, warehouse: Path | None = None,
        contract: Path | None = None, max_pages: int | None = None, after_page=None) -> Report:
    """Sync, then load what landed under the source's reviewed contract."""
    import duckdb
    from .ingest.contracts import load_contract
    from .ingest.engine import ingest

    contract = contract or contract_path(src)
    if not contract.exists():
        raise FileNotFoundError(f"no contract for {src.name} at {contract}: run `python -m plumbline.kit draft`, "
                                f"read the draft, then move it there")
    c = load_contract(contract)
    workdir = workdir or ROOT / "data" / "kit"
    t = time.perf_counter()
    rep = sync(src, workdir / "run" / "sources" / src.name, list(c.columns), max_pages=max_pages, after_page=after_page)
    c = _with_files(c, f"sources/{src.name}/*.csv")
    res = ingest(workdir / "run", warehouse or workdir / "kit.duckdb", {c.name: c})
    tot = res.totals()
    rep.rows_loaded, rep.rows_quarantined, rep.rows_already_present = (
        tot["rows_loaded"], tot["rows_quarantined"], tot["rows_already_present"])
    with duckdb.connect(str(warehouse or workdir / "kit.duckdb"), read_only=True) as con:
        rep.quarantine_reasons = dict(con.execute(
            "SELECT reason, count(*) FROM quarantine WHERE source = ? GROUP BY 1 ORDER BY 2 DESC", [c.name]).fetchall())
    rep.seconds = time.perf_counter() - t
    return rep


def _with_files(c, files: str):
    from dataclasses import replace
    return replace(c, files=files)


def draft(src: Source, *, out: Path | None = None, workdir: Path | None = None) -> Path:
    """Read one page, write a contract draft for a person to review. Never applied on its own. Without a
    workdir, the scratch folder is removed afterwards."""
    if workdir is not None:
        return _draft(src, out, workdir)
    import tempfile
    with tempfile.TemporaryDirectory(prefix="plumbline-draft-") as tmp:
        return _draft(src, out, Path(tmp))


def _draft(src: Source, out: Path | None, workdir: Path) -> Path:
    import yaml
    from .ai.onboard import Draft, heuristic_draft, profile, to_contract, validate

    landing = workdir / "sources" / src.name
    sync(src, landing, None, max_pages=1)
    first = sorted(landing.glob("*.csv"))
    if not first:
        raise SourceError(f"{src.name}: the first page was empty, so there is nothing to draft from")
    d = heuristic_draft(profile(first[0]))
    cols = [c for c in d.columns if not (PERSONAL.search(c.name) and not c.privacy)]
    personal = [c.name for c in d.columns if c not in cols]
    key = [src.key] if src.key else list(d.key)
    upd = [src.updated] if src.updated else list(d.latest_by)
    cols = [c.model_copy(update={"required": True}) if c.name in key + upd else c for c in cols]
    d = Draft(key=key, latest_by=upd, columns=cols)
    check = workdir / "check"
    check.mkdir(parents=True, exist_ok=True)
    why = validate(d, src.name, check)
    if why:
        raise ValueError(f"{src.name}: the draft does not load: {why}. Set `key = ...` on the source.")
    spec = to_contract(d, src.name, f"sources/{src.name}/*.csv")
    spec["kind"] = "api"
    out = out or ROOT / "sources" / "drafts" / f"{src.name}.yaml"
    out.parent.mkdir(parents=True, exist_ok=True)
    note = f"# Drafted from the first page of {src.name}. Read it, then move it to sources/kit/{src.name}.yaml.\n"
    if personal:
        note += f"# Left out as personal (add back only with a privacy rule): {', '.join(personal)}\n"
    ident = [c.name for c in cols if c.identity]
    if ident:
        note += (f"# Marked identity (keys to find a person's rows when they ask to be deleted): {', '.join(ident)}. "
                 "Keep a line only if the column describes a person; where something happened is not a person.\n")
    out.write_text(note + yaml.safe_dump(spec, sort_keys=False))
    return out


# ------------------------------------------------------------------ verify: the promises, on your source
def verify(make, *, contract: Path, workdir: Path | None = None, max_pages: int = 3) -> list[tuple[str, bool, str]]:
    """Run a new source the ways production will, and check the promises hold. `make()` returns a fresh
    Source (a class works). Returns (check, passed, detail) for each: passed is None when a check could not be
    judged (the cursor check, when max_pages stopped the run mid-window). None may fail before a source ships.
    Without a workdir, the five warehouses it builds are removed afterwards."""
    if workdir is not None:
        return _verify(make, contract=contract, workdir=workdir, max_pages=max_pages)
    import tempfile
    with tempfile.TemporaryDirectory(prefix="plumbline-verify-") as tmp:
        return _verify(make, contract=contract, workdir=Path(tmp), max_pages=max_pages)


def _verify(make, *, contract: Path, workdir: Path | None = None, max_pages: int = 3) -> list[tuple[str, bool, str]]:
    """Run a new source the ways production will, and check the promises hold. `make()` returns a fresh
    Source (a class works). Returns (check, passed, detail) for each: passed is None when a check could not be
    judged (the cursor check, when max_pages stopped the run mid-window). None may fail before a source ships."""
    import duckdb

    base = workdir
    out = []

    a = run(make(), workdir=base / "a", contract=contract, max_pages=max_pages)
    out.append(("it lands and loads rows", a.rows_loaded > 0,
                f"{a.rows_landed} landed, {a.rows_loaded} loaded, {a.rows_quarantined} quarantined"))

    (base / "a" / "run" / "sources" / a.name / "_kit_state.json").unlink()      # the cursor is lost: read it all again
    b = run(make(), workdir=base / "a", contract=contract, max_pages=max_pages)
    out.append(("reading the same pages again adds nothing", b.rows_landed > 0 and b.rows_loaded == 0,
                f"read {b.rows_landed} rows again, {b.rows_loaded} added"))

    class Crash(Exception):
        pass

    def boom(n):
        if n == 1:
            raise Crash
    run(make(), workdir=base / "d", contract=contract, max_pages=max_pages)       # clean first: a live source only grows after
    try:
        run(make(), workdir=base / "c", contract=contract, max_pages=max_pages, after_page=boom)
    except Crash:
        pass
    run(make(), workdir=base / "c", contract=contract, max_pages=max_pages)
    with duckdb.connect(str(base / "c" / "kit.duckdb"), read_only=True) as c1, \
         duckdb.connect(str(base / "d" / "kit.duckdb"), read_only=True) as c2:
        q = f"SELECT count(*) FROM current_{a.name}"
        n1, n2 = c1.execute(q).fetchone()[0], c2.execute(q).fetchone()[0]
    out.append(("a crash after page 1 resumes and loses nothing", n1 >= n2 > 0,
                f"{n1} rows after crash and resume, {n2} in a clean run; a resume may read further, never less"))

    if a.finished:
        out.append(("a finished run leaves a cursor", a.cursor is not None, f"next run starts from {a.cursor}"))
    else:          # stopped by max_pages mid-window: no cursor is the right answer there, so this is not judged
        out.append(("a finished run leaves a cursor", None, f"not checked: the window is longer than {max_pages} pages; "
                                                           f"rerun verify with a larger --max-pages"))
    return out


# ------------------------------------------------------------------ CLI
def _load(path: str) -> type[Source]:
    import importlib.util
    spec = importlib.util.spec_from_file_location(Path(path).stem, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    # defined in this file: a base class it imports (PoliteSource, say) is not the source
    found = [v for v in vars(mod).values() if isinstance(v, type) and issubclass(v, Source) and v.__module__ == mod.__name__]
    if len(found) != 1:
        raise SystemExit(f"{path}: expected one Source subclass, found {len(found)}")
    return found[0]


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="python -m plumbline.kit", description=__doc__.split("\n\n")[0])
    ap.add_argument("action", choices=["draft", "run", "verify"])
    ap.add_argument("source_file")
    ap.add_argument("--contract", default=None)
    ap.add_argument("--max-pages", type=int, default=None)
    ap.add_argument("--workdir", default=None)
    a = ap.parse_args(argv)
    cls = _load(a.source_file)
    wd = Path(a.workdir) if a.workdir else None
    if a.action == "draft":
        print(f"draft written: {draft(cls(), workdir=wd)}")
        return 0
    contract = Path(a.contract) if a.contract else contract_path(cls())
    if a.action == "run":
        r = run(cls(), workdir=wd, contract=contract, max_pages=a.max_pages)
        print(r.line())
        print(json.dumps(asdict(r), default=str))
        return 0
    checks = verify(cls, contract=contract, workdir=wd, max_pages=a.max_pages or 3)
    for name, ok, detail in checks:
        print(f"{'SKIP' if ok is None else 'PASS' if ok else 'FAIL'}  {name}  ({detail})")
    return 0 if all(ok is not False for _, ok, _ in checks) else 1


if __name__ == "__main__":
    # Under `python -m plumbline.kit` this file runs as __main__, a second copy of the module whose Source is
    # not the one a source file imports. Run the real module's main, so there is one Source class.
    from plumbline.kit import main as _main
    raise SystemExit(_main())
