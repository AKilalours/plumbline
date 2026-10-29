"""Scenario Lab: inject a real incident on a private copy, then Detect, Inspect, Fix, Verify.

Each scenario is a run with a persisted record (runs/<id>.json): every step it took with its
timing, what the checks found, the proposed fix with its blast radius, who approved it, and the
before-and-after numbers. The incident report is generated from that record, never from a chat.

Detection never reads the answer key: it uses the checks a real team would have (identity
assertions, a source-to-landing reconciliation, a deletion verification against request hashes,
a point-in-time audit). The answer key is used once, at Verify, as an independent second opinion.
A fix runs only after a second person with the required role approves it, and only once.
"""
from __future__ import annotations

import json
import shutil
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pandas as pd

from .demo import CONTRACTS, Demo

PHASES = ("inject", "detect", "inspect", "propose", "approve", "fix", "verify")


class NotAllowed(PermissionError):
    pass


@dataclass
class Run:
    id: str
    scenario: str
    created_at: str
    work: str
    status: str = "new"                       # new, injected, detected, proposed, fixed, verified
    events: list = field(default_factory=list)
    findings: dict = field(default_factory=dict)
    evidence: dict = field(default_factory=dict)
    proposal: dict = field(default_factory=dict)
    approval: dict = field(default_factory=dict)
    verification: dict = field(default_factory=dict)
    state: dict = field(default_factory=dict)  # scenario-private, JSON-safe

    def log(self, phase: str, label: str, detail: str = "", seconds: float = 0.0) -> None:
        self.events.append({"at": datetime.now(timezone.utc).strftime("%H:%M:%S"), "phase": phase,
                            "label": label, "detail": detail, "seconds": round(seconds, 2)})

    def save(self, runs_dir: Path) -> Path:
        runs_dir.mkdir(parents=True, exist_ok=True)
        p = runs_dir / f"{self.id}.json"
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), indent=1, default=str))
        tmp.replace(p)
        return p

    @classmethod
    def load(cls, runs_dir: Path, run_id: str) -> "Run":
        return cls(**json.loads((runs_dir / f"{run_id}.json").read_text()))


class _Timer:
    def __enter__(self):
        self.t = time.time()
        return self

    def __exit__(self, *a):
        self.s = time.time() - self.t


def _require(run: Run, status: str) -> None:
    order = ["new", "injected", "detected", "proposed", "fixed", "verified"]
    if order.index(run.status) < order.index(status):
        raise ValueError(f"run {run.id} is {run.status}; this step needs {status}")


def approve(run: Run, approver: str, roles: dict[str, set[str]]) -> None:
    """A second person with the required role; never the proposer; once."""
    _require(run, "proposed")
    if run.approval:
        raise NotAllowed(f"already approved by {run.approval['by']}")
    need = run.proposal["role"]
    if approver == run.proposal["proposed_by"]:
        raise NotAllowed("whoever proposed a fix cannot approve it" + (": the agent never approves its own work" if approver == AGENT else ""))
    if need not in roles.get(approver, set()):
        raise NotAllowed(f"{approver} does not hold the {need} role")
    run.approval = {"by": approver, "role": need, "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "idempotency_key": f"{run.id}:{run.proposal['action']}"}
    run.log("approve", f"Approved by {approver}", f"role {need}")


# =================================================================== 1. false identity merge
class FalseMerge:
    key, title, role = "false_merge", "False identity merge", "admin"

    @staticmethod
    def _assertions(con, overrides: dict) -> dict[str, list[str]]:
        """Clusters that break identity rules: two birth years, or first names that cannot be one person."""
        from ..identity.cluster import first_names_compatible
        r = con.execute("SELECT unique_id, person_key, first_name, birth_year FROM identity_records").df()
        r["k"] = [overrides.get(u, k) for u, k in zip(r.unique_id, r.person_key)]
        bad = {}
        for k, g in r.groupby("k"):
            years = set(g.birth_year.dropna()) - {""}
            firsts = sorted(set(g.first_name.dropna()) - {""})
            why = []
            if len(years) > 1:
                why.append("conflicting birth years")
            if any(not first_names_compatible(a, b) for i, a in enumerate(firsts) for b in firsts[i + 1:]):
                why.append("incompatible first names")
            if why:
                bad[k] = why
        return bad

    def inject(self, run: Run, demo: Demo) -> None:
        from .ops import IdentityEdits
        with _Timer() as t, duckdb.connect(str(demo.warehouse), read_only=True) as con:
            hh = con.execute("""SELECT household_key, list(person_key ORDER BY person_key) AS ks
                                FROM products.dim_person WHERE household_key IS NOT NULL
                                GROUP BY household_key, last_name HAVING count(*) = 2 ORDER BY household_key""").df()
            base_bad = self._assertions(con, {})
            pick = None
            for ks in hh.ks:
                if not any(k in base_bad for k in ks):
                    edits = IdentityEdits()
                    edits.merge(con, ks[0], ks[1])
                    if any(k in self._assertions(con, edits.overrides) for k in ks[:1]):
                        pick = (list(ks), edits.overrides)
                        break
        if pick is None:
            raise RuntimeError("no household of two namesakes to merge in this city")
        run.state = {"keep": pick[0][0], "absorb": pick[0][1], "overrides": pick[1], "baseline_failures": sorted(base_bad)}
        run.log("inject", "Merged two people who share a home and a last name", f"{pick[0][1]} into {pick[0][0]}", t.s)
        run.status = "injected"

    def detect(self, run: Run, demo: Demo) -> None:
        _require(run, "injected")
        with _Timer() as t, duckdb.connect(str(demo.warehouse), read_only=True) as con:
            n = con.execute("SELECT count(*) FROM identity_records").fetchone()[0]
            bad = self._assertions(con, run.state["overrides"])
        new = {k: v for k, v in bad.items() if k not in set(run.state["baseline_failures"])}
        run.log("detect", "Loaded the identity map", f"{n:,} records", t.s * 0.2)
        run.log("detect", "Checked identity assertions", f"{len(bad)} failing, {len(new)} new", t.s * 0.8)
        run.findings = {"check": "identity assertions", "failing": len(bad), "new": len(new),
                        "new_clusters": {k: v for k, v in new.items()}}
        run.status = "detected"

    def inspect(self, run: Run, demo: Demo) -> None:
        _require(run, "detected")
        keep, absorb = run.state["keep"], run.state["absorb"]
        with _Timer() as t, duckdb.connect(str(demo.warehouse), read_only=True) as con:
            people = con.execute("""SELECT person_key, first_name, dob, household_key, address_line, records
                                    FROM products.dim_person WHERE person_key IN (?, ?)""", [keep, absorb]).df()
            down = {tbl: con.execute(f"SELECT count(*) FROM products.{tbl} WHERE person_key IN (?, ?)", [keep, absorb]).fetchone()[0]
                    for tbl in ("dim_person", "mart_activation_audience", "fct_person_features", "fct_life_events")}
        a, b = people.set_index("person_key").loc[keep], people.set_index("person_key").loc[absorb]
        shared = [n for n, c in (("home", "household_key"), ("address", "address_line")) if a[c] == b[c]]
        differ = [n for n, c in (("first name", "first_name"), ("birth date", "dob")) if pd.notna(a[c]) and pd.notna(b[c]) and a[c] != b[c]]
        run.evidence = {"identity_a": keep, "identity_b": absorb, "shared": shared, "conflicting": differ,
                        "records": int(a.records + b.records), "downstream_rows": {k: int(v) for k, v in down.items() if v}}
        run.log("inspect", "Compared the two identities", " · ".join(["shared " + ", ".join(shared)] + (["conflicting " + ", ".join(differ)] if differ else [])), t.s * 0.5)
        run.log("inspect", "Traced affected products", f"{sum(1 for v in down.values() if v)} products, {sum(down.values())} rows", t.s * 0.5)

    def propose(self, run: Run, proposed_by: str) -> None:
        _require(run, "detected")
        ev = run.evidence
        run.proposal = {"action": "split_merged_identity", "summary": f"Split {ev['identity_b']} back out of {ev['identity_a']}",
                        "affected_profiles": 2, "downstream": ev["downstream_rows"], "role": self.role, "proposed_by": proposed_by}
        run.log("propose", "Prepared a correction", "No changes applied")
        run.status = "proposed"

    def fix(self, run: Run, demo: Demo) -> None:
        with _Timer() as t:
            run.state["overrides"] = {}
        run.log("fix", "Split the merged identity", "overlay removed", t.s)
        run.status = "fixed"

    def verify(self, run: Run, demo: Demo) -> None:
        from .ops import identity_scores
        with _Timer() as t, duckdb.connect(str(demo.warehouse), read_only=True) as con:
            bad = self._assertions(con, run.state["overrides"])
            from .ops import IdentityEdits
            e = IdentityEdits()
            e.merge(con, run.state["keep"], run.state["absorb"])
            during = identity_scores(con, demo.run, e.overrides)
            after = identity_scores(con, demo.run, run.state["overrides"])
        new = [k for k in bad if k not in set(run.state["baseline_failures"])]
        run.verification = {"passed": not new, "checks": {"new assertion failures": len(new)},
                            "before_after": {"precision": [during["precision"], after["precision"]],
                                             "people resolved exactly": [during["people_resolved_exactly"], after["people_resolved_exactly"]]}}
        run.log("verify", "Re-ran identity assertions", f"{len(new)} new failures", t.s * 0.4)
        run.log("verify", "Scored against the answer key", f"precision {during['precision']} → {after['precision']}", t.s * 0.6)
        run.status = "verified"


# =================================================================== 2. interrupted source sync
class InterruptedSync:
    key, title, role = "interrupted_sync", "Interrupted source sync", "data-platform"

    def _client(self, demo: Demo, faults: bool = True):
        from ..ingest.connectors.store_api import StoreClient
        from ..store.server import Faults, MockStore
        f = Faults(p_429=0.15, p_503=0.08, p_drop=0.05, retry_after=0, seed=7) if faults else Faults(seed=7)
        store = MockStore.from_run(demo.run, "lab-token", f)
        return store, lambda url: StoreClient(url, "lab-token", base_delay=0.001, max_delay=0.01, max_retries=12, sleep=lambda x: None)

    def _landed(self, run: Run) -> pd.DataFrame:
        files = sorted((Path(run.work) / "landing").glob("orders_*.csv"))
        return pd.concat([pd.read_csv(p, dtype=str) for p in files], ignore_index=True) if files else pd.DataFrame(columns=["order_id"])

    def inject(self, run: Run, demo: Demo) -> None:
        from ..ingest.connectors.store_api import sync_orders

        class Crash(Exception):
            pass

        def boom(page):
            if page == 4:
                raise Crash()
        store, client = self._client(demo)
        with _Timer() as t, store as s:
            try:
                sync_orders(client(s.url), Path(run.work) / "landing", page_size=100, after_page=boom)
            except Crash:
                pass
        run.log("inject", "Sync process killed after page 4", "store API throttling and failing", t.s)
        run.status = "injected"

    def detect(self, run: Run, demo: Demo) -> None:
        _require(run, "injected")
        with _Timer() as t:
            expected = pd.read_parquet(demo.run / "store" / "orders.parquet", columns=["order_id"]).order_id
            got = self._landed(run)
            state = json.loads((Path(run.work) / "landing" / "_sync_state.json").read_text())
        missing = len(set(expected) - set(got.order_id))
        run.findings = {"check": "source-to-landing reconciliation", "store_orders": int(expected.nunique()),
                        "landed": int(got.order_id.nunique()), "missing": missing,
                        "sync_in_progress": bool(state.get("in_progress"))}
        run.log("detect", "Reconciled store against landing", f"{missing:,} of {expected.nunique():,} orders missing", t.s * 0.7)
        run.log("detect", "Read the sync checkpoint", "a sync is still marked in progress" if state.get("in_progress") else "no sync in progress", t.s * 0.3)
        run.status = "detected"

    def inspect(self, run: Run, demo: Demo) -> None:
        _require(run, "detected")
        state = json.loads((Path(run.work) / "landing" / "_sync_state.json").read_text())
        got = self._landed(run)
        ip = state.get("in_progress") or {}
        run.evidence = {"cursor_page": ip.get("page"), "sync_id": ip.get("sync_id"), "pages_landed": len(list((Path(run.work) / "landing").glob("orders_*.csv"))),
                        "duplicates_so_far": int(len(got) - got.order_id.nunique()), "watermark": state.get("watermark")}
        run.log("inspect", "Inspected the checkpoint", f"cursor at page {ip.get('page')}, {run.evidence['pages_landed']} pages landed atomically")

    def propose(self, run: Run, proposed_by: str) -> None:
        _require(run, "detected")
        run.proposal = {"action": "resume_sync_from_cursor", "summary": f"Resume the store sync from page {run.evidence.get('cursor_page')}",
                        "affected_profiles": 0, "downstream": {"orders to fetch": run.findings["missing"]}, "role": self.role, "proposed_by": proposed_by}
        run.log("propose", "Prepared a correction", "No changes applied")
        run.status = "proposed"

    def fix(self, run: Run, demo: Demo) -> None:
        from ..ingest.connectors.store_api import sync_orders
        store, client = self._client(demo)
        with _Timer() as t, store as s:
            r = sync_orders(client(s.url), Path(run.work) / "landing", page_size=100)
        run.state["resumed"] = bool(r.resumed)
        run.log("fix", "Resumed from the saved cursor" if r.resumed else "Restarted the sync", f"{r.pages} pages, {r.retries} retries", t.s)
        run.status = "fixed"

    def verify(self, run: Run, demo: Demo) -> None:
        with _Timer() as t:
            expected = pd.read_parquet(demo.run / "store" / "orders.parquet", columns=["order_id"]).order_id
            got = self._landed(run)
        missing, dupes = len(set(expected) - set(got.order_id)), int(len(got) - got.order_id.nunique())
        run.verification = {"passed": missing == 0 and dupes == 0 and run.state.get("resumed", False),
                            "checks": {"missing orders": missing, "duplicate orders": dupes, "resumed, not restarted": run.state.get("resumed", False)},
                            "before_after": {"orders landed": [run.findings["landed"], int(got.order_id.nunique())]}}
        run.log("verify", "Reconciled again", f"{missing} missing, {dupes} duplicates", t.s)
        run.status = "verified"


# =================================================================== 3. stale deletion snapshot
class StaleRestore:
    key, title, role = "stale_restore", "Restore from a stale backup", "privacy"

    @staticmethod
    def _violations(wh: Path, run_dir: Path) -> pd.DataFrame:
        """Deletion verification: rows in any current table that a request covers, by the pipeline's own
        single erasure rule (ingest/erasure.py), applied to the request files rather than the warehouse's
        suppression table, because a restored warehouse carries a stale one. A first version matched any
        hash, and flagged family members who share an inbox, rows the rule keeps on purpose."""
        from ..ingest.contracts import load_contracts
        from ..ingest.erasure import contract_erase_sql
        reqs = pd.concat([pd.read_csv(p, dtype=str) for p in sorted((run_dir / "sources" / "privacy").glob("*.csv"))], ignore_index=True)
        q = []
        for c in load_contracts(CONTRACTS).values():
            if c.role != "data":
                continue
            key = c.key[0]
            rule = contract_erase_sql(c, "t").replace("suppression", "_requests")
            q.append(f"SELECT '{c.name}' AS tbl, t.\"{key}\"::VARCHAR AS record_id FROM current_{c.name} t WHERE {rule}")
        with duckdb.connect(str(wh), read_only=True) as con:
            con.register("_requests", reqs)
            return con.execute(" UNION ALL ".join(q)).df()

    def inject(self, run: Run, demo: Demo) -> None:
        from .queries import deletion_candidates, deletion_sandbox, identity_outcomes
        with _Timer() as t:
            with duckdb.connect(str(demo.warehouse), read_only=True) as con:
                pid = deletion_candidates(con, demo.run, identity_outcomes(con, demo.run))[0]
            r = deletion_sandbox(demo, pid, Path(run.work) / "sandbox", keep=True)
            shutil.copy(demo.warehouse, r["warehouse"])            # yesterday's backup: taken before the request
        run.state = {"person_id": pid, "run": str(r["run"]), "warehouse": str(r["warehouse"]), "deleted_records": r["removed"]}
        run.log("inject", "Deletion request processed", f"{r['removed']} records removed", t.s * 0.8)
        run.log("inject", "Warehouse restored from the previous night's backup", "the backup predates the request", t.s * 0.2)
        run.status = "injected"

    def detect(self, run: Run, demo: Demo) -> None:
        _require(run, "injected")
        with _Timer() as t:
            v = self._violations(Path(run.state["warehouse"]), Path(run.state["run"]))
        run.findings = {"check": "deletion verification", "rows_back": int(len(v)),
                        "by_table": v.groupby("tbl").size().astype(int).to_dict()}
        run.log("detect", "Verified every deletion request", f"{len(v)} rows a request covers are back", t.s)
        run.status = "detected"

    def inspect(self, run: Run, demo: Demo) -> None:
        _require(run, "detected")
        with duckdb.connect(run.state["warehouse"], read_only=True) as con:
            loaded = set(con.execute("SELECT path FROM ingest_ledger WHERE source = 'privacy'").df().path)
        files = sorted(p.name for p in (Path(run.state["run"]) / "sources" / "privacy").glob("*.csv"))
        unseen = [f for f in files if not any(f in p for p in loaded)]
        run.evidence = {"rows_by_table": run.findings["by_table"], "request_files": len(files), "not_in_restored_ledger": unseen}
        run.log("inspect", "Compared the ledger with the request files", f"{len(unseen)} request file(s) unknown to the restored warehouse")

    def propose(self, run: Run, proposed_by: str) -> None:
        _require(run, "detected")
        run.proposal = {"action": "reapply_deletions", "summary": "Re-apply every deletion request the backup missed",
                        "affected_profiles": 1, "downstream": run.findings["by_table"], "role": self.role, "proposed_by": proposed_by}
        run.log("propose", "Prepared a correction", "No changes applied")
        run.status = "proposed"

    def fix(self, run: Run, demo: Demo) -> None:
        from ..ingest.contracts import load_contracts
        from ..ingest.engine import ingest
        from ..privacy.person_deletion import expand_deletions
        with _Timer() as t:
            contracts = load_contracts(CONTRACTS)
            ingest(Path(run.state["run"]), Path(run.state["warehouse"]), contracts)
            with duckdb.connect(run.state["warehouse"]) as con:
                expand_deletions(con, contracts)
        run.log("fix", "Re-applied deletions", "purge at load, then person-level deletion", t.s)
        run.status = "fixed"

    def verify(self, run: Run, demo: Demo) -> None:
        from ..privacy.evaluate import snapshot
        from .queries import links
        with _Timer() as t:
            v = self._violations(Path(run.state["warehouse"]), Path(run.state["run"]))
            with duckdb.connect(str(demo.warehouse), read_only=True) as con:
                before = snapshot(con)
            with duckdb.connect(run.state["warehouse"], read_only=True) as con:
                after = snapshot(con)
            truth = links(Path(run.state["run"]))
            pid = run.state["person_id"]
            left = innocent = 0
            for src in before:
                own = dict(zip(truth[truth.source == src].record_id, truth[truth.source == src].person_id))
                left += sum(1 for r in after[src] if own.get(r) == pid)
                innocent += sum(1 for r in before[src] - after[src] if own.get(r) != pid)
        run.verification = {"passed": len(v) == 0 and left == 0 and innocent == 0,
                            "checks": {"rows a request covers": int(len(v)), "requester's records left (answer key)": left,
                                       "other people's records removed (answer key)": innocent},
                            "before_after": {"rows back": [run.findings["rows_back"], int(len(v))]}}
        run.log("verify", "Verified deletions again", f"{len(v)} rows a request covers", t.s * 0.5)
        run.log("verify", "Checked against the answer key", f"{left} left, {innocent} innocent removed", t.s * 0.5)
        run.status = "verified"


# =================================================================== 4. future-dated feature
class FutureFeature:
    key, title, role = "future_feature", "Future-dated feature", "data-platform"
    AS_OF = "2025-09-30"
    LEAKED = {"vendor_homeowner": "homeowner", "income_band": "income_band"}

    def _snapshot(self, run: Run, demo: Demo) -> pd.DataFrame:
        from ..products.build import build
        wh = Path(run.work) / "snapshot.duckdb"
        shutil.copy(demo.warehouse, wh)
        build(wh, as_of=self.AS_OF)
        with duckdb.connect(str(wh), read_only=True) as con:
            return con.execute("SELECT * FROM products.fct_person_features").df()

    def audit(self, snap: pd.DataFrame, demo: Demo) -> pd.DataFrame:
        """Point-in-time audit: a value in a snapshot must have been first seen on or before its as_of."""
        with duckdb.connect(str(demo.warehouse), read_only=True) as con:
            seen = con.execute("""SELECT person_key, attr, min(first_observed_at) AS first_seen
                                  FROM products.int_attribute_values GROUP BY 1, 2""").df()
        rows = []
        for feat, attr in self.LEAKED.items():
            s = snap[["person_key", "as_of", feat]].dropna(subset=[feat]).merge(seen[seen.attr == attr], on="person_key", how="left")
            s = s[pd.to_datetime(s.first_seen) >= pd.to_datetime(s.as_of) + pd.Timedelta(days=1)]
            rows.append(s.assign(feature=feat)[["person_key", "feature", "as_of", "first_seen"]])
        return pd.concat(rows, ignore_index=True)

    def inject(self, run: Run, demo: Demo) -> None:
        with _Timer() as t:
            snap = self._snapshot(run, demo)
        run.log("inject", f"Built a training snapshot as of {self.AS_OF}", f"{len(snap):,} people", t.s)
        with _Timer() as t, duckdb.connect(str(demo.warehouse), read_only=True) as con:
            latest = con.execute(f"SELECT person_key, {', '.join(self.LEAKED)} FROM products.fct_person_features").df().set_index("person_key")
            for f in self.LEAKED:
                snap[f] = snap.person_key.map(latest[f]).where(snap.person_key.map(latest[f]).notna(), snap[f])
            snap.to_parquet(Path(run.work) / "snapshot.parquet")
        run.log("inject", "A backfill filled vendor attributes from December", ", ".join(self.LEAKED), t.s)
        run.status = "injected"

    def detect(self, run: Run, demo: Demo) -> None:
        _require(run, "injected")
        with _Timer() as t:
            v = self.audit(pd.read_parquet(Path(run.work) / "snapshot.parquet"), demo)
        run.findings = {"check": "point-in-time audit", "values_from_the_future": int(len(v)), "people": int(v.person_key.nunique()),
                        "by_feature": v.groupby("feature").size().astype(int).to_dict(),
                        "first_seen_range": [str(v.first_seen.min())[:10], str(v.first_seen.max())[:10]] if len(v) else []}
        run.log("detect", "Audited every feature value against its first-seen date", f"{len(v):,} values seen after {self.AS_OF}", t.s)
        run.status = "detected"

    def inspect(self, run: Run, demo: Demo) -> None:
        _require(run, "detected")
        from .demo import REPO
        leak = json.loads((REPO / "docs" / "results" / "leak_demo.json").read_text())
        run.evidence = {"as_of": self.AS_OF, "first_seen_range": run.findings["first_seen_range"], "at_100k": leak}
        run.log("inspect", "Looked up what this leak did at 100,000 people", f"AUC {leak['leaky_auc']} vs ceiling {leak['ceiling_auc']}")

    def propose(self, run: Run, proposed_by: str) -> None:
        _require(run, "detected")
        run.proposal = {"action": "rebuild_snapshot_point_in_time", "summary": f"Rebuild the {self.AS_OF} snapshot point in time",
                        "affected_profiles": int(run.findings["people"]), "downstream": dict(run.findings["by_feature"]),
                        "role": self.role, "proposed_by": proposed_by}
        run.log("propose", "Prepared a correction", "No changes applied")
        run.status = "proposed"

    def fix(self, run: Run, demo: Demo) -> None:
        with _Timer() as t:
            self._snapshot(run, demo).to_parquet(Path(run.work) / "snapshot.parquet")
        run.log("fix", "Rebuilt the snapshot with dbt as of the cutoff", "", t.s)
        run.status = "fixed"

    def verify(self, run: Run, demo: Demo) -> None:
        with _Timer() as t:
            v = self.audit(pd.read_parquet(Path(run.work) / "snapshot.parquet"), demo)
        run.verification = {"passed": len(v) == 0, "checks": {"values seen after the cutoff": int(len(v))},
                            "before_after": {"future values": [run.findings["values_from_the_future"], int(len(v))]}}
        run.log("verify", "Audited the rebuilt snapshot", f"{len(v)} values from the future", t.s)
        run.status = "verified"


SCENARIOS = {s.key: s for s in (FalseMerge(), InterruptedSync(), StaleRestore(), FutureFeature())}


KEEP_RECORDS, STALE_WORK_S = 50, 3600


def prune(runs_dir: Path, now: float | None = None) -> int:
    """A public page must not fill the disk: keep the newest records, and delete the working copy of any
    run left unfinished for an hour. Returns how many working copies were removed."""
    now = now or time.time()
    recs = sorted(runs_dir.glob("run_*.json"), key=lambda p: p.stat().st_mtime, reverse=True) if runs_dir.exists() else []
    removed = 0
    for i, p in enumerate(recs):
        try:
            work = Path(json.loads(p.read_text())["work"])
        except Exception:  # noqa: BLE001 - a torn record is just removed
            p.unlink(missing_ok=True)
            continue
        if work.exists() and (i >= KEEP_RECORDS or now - p.stat().st_mtime > STALE_WORK_S):
            shutil.rmtree(work, ignore_errors=True)
            removed += 1
        if i >= KEEP_RECORDS:
            p.unlink(missing_ok=True)
    return removed


def start(scenario: str, demo: Demo, runs_dir: Path) -> Run:
    prune(runs_dir)
    run = Run(id=f"run_{uuid.uuid4().hex[:6]}", scenario=scenario,
              created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
              work=tempfile.mkdtemp(prefix=f"plumbline-{scenario}-"))
    SCENARIOS[scenario].inject(run, demo)
    run.save(runs_dir)
    return run


AGENT = "plumbline-agent"


def investigate(run: Run, demo: Demo, runs_dir: Path, proposed_by: str = AGENT) -> Run:
    s = SCENARIOS[run.scenario]
    s.detect(run, demo)
    s.inspect(run, demo)
    s.propose(run, proposed_by)
    run.save(runs_dir)
    return run


def fix_and_verify(run: Run, demo: Demo, runs_dir: Path) -> Run:
    if not run.approval:
        raise NotAllowed("the fix needs an approval first")
    if run.status in ("fixed", "verified"):
        return run                                   # idempotent: the same approval never runs twice
    s = SCENARIOS[run.scenario]
    try:
        s.fix(run, demo)
        s.verify(run, demo)
    finally:
        run.save(runs_dir)
        shutil.rmtree(run.work, ignore_errors=True)        # the record is the evidence; the copy is not needed
    return run


def _fmt(v) -> str:
    if isinstance(v, dict):
        return "; ".join(f"{str(k).replace('_', ' ')} {_fmt(x)}" for k, x in v.items())
    if isinstance(v, (list, tuple)):
        return " to ".join(map(str, v)) if len(v) == 2 and all(isinstance(x, str) for x in v) else ", ".join(map(_fmt, v))
    return f"{v:,}" if isinstance(v, int) and not isinstance(v, bool) else str(v)


def report(run: Run) -> str:
    """The incident report, from the run record alone."""
    s = SCENARIOS[run.scenario]
    v, p = run.verification, run.proposal
    status = "verified" if v.get("passed") else ("verification FAILED" if v else run.status)
    lines = [f"### Incident {run.id}: {s.title}", "", f"**{status}** · opened {run.created_at}", "",
             "#### Found", f"- check: {run.findings.get('check')}"]
    lines += [f"- {k.replace('_', ' ')}: {_fmt(val)}" for k, val in run.findings.items() if k != "check"]
    lines += ["", "#### Evidence"] + [f"- {k.replace('_', ' ')}: {_fmt(val)}" for k, val in run.evidence.items()]
    if p:
        lines += ["", "#### Correction", f"- {p['summary']}", f"- proposed by {p['proposed_by']}; needs the {p['role']} role"]
        if run.approval:
            lines.append(f"- approved by {run.approval['by']} at {run.approval['at']} (idempotency key `{run.approval['idempotency_key']}`)")
    if v:
        lines += ["", "#### Verification"] + [f"- {k}: {_fmt(val)}" for k, val in v["checks"].items()]
        lines += [f"- {k}: {a} → {b}" for k, (a, b) in v["before_after"].items()]
    lines += ["", "#### Remaining risk", RISKS[run.scenario], "", "#### Trace"]
    lines += [f"- {e['at']} {e['phase']}: {e['label']}" + (f" ({e['detail']})" if e["detail"] else "") + f", {e['seconds']}s" for e in run.events]
    return "\n".join(lines) + "\n"


RISKS = {
    "false_merge": "The assertion catches merges with conflicting birth years or first names. Two people with the same first name and no birth date on file would pass it; the review queue is the backstop.",
    "interrupted_sync": "Resume covers a crash between pages. A store that rewrote history after the watermark needs a full re-pull (delete the checkpoint).",
    "stale_restore": "Any restore from backup must be followed by re-applying deletions; this check runs nightly, so a restore leaves at most a day of exposure.",
    "future_feature": "The audit covers attributes with a first-seen date. A feature computed from a source without timestamps cannot be audited this way.",
}
