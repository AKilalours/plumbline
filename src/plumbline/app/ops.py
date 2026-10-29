"""The operations the app lets a visitor perform: every one does real work on the live city or on a
private copy of it, and reports what it measured and against what.

- foundation_checks: the data-foundation checklist (fresh and clean, stale and low-confidence,
  first-party vs third-party conflicts, identity validated, consent honoured), each with a status,
  a count over its denominator, and the records behind it.
- match_evidence / IdentityEdits: why two records were joined, and split, merge and undo on a copy
  of the identity map, rescored against the answer key.
- failure_lab: a store API sync under injected throttling, errors, dropped connections and a crash,
  resumed and reconciled against what the store holds.
- Audience rules: a typed rule set (from toggles or a sentence), its live count, who it excludes
  for consent and why, and the provenance of one member's attributes.
- replay_after_deletion: re-deliver every original file after a deletion; nobody may come back.
- ActivityLog: what was done, by whom, with what result.
"""
from __future__ import annotations

import json
import re
import shutil
import tempfile
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pandas as pd

from ..identity.evaluate import pairwise_scores, truth_for
from .demo import CONTRACTS, Demo


# ----------------------------------------------------------------- data foundation checklist
def foundation_checks(con, run: Path, summary: dict) -> list[dict]:
    """Each check: id, title, status (pass | warn | fail), value, of (denominator), unit, note,
    evidence (DataFrame of the records behind the number)."""
    from ..ingest.checks import check_invariants
    from ..ingest.contracts import load_contracts
    out = []

    led = con.execute("SELECT source, sum(rows_in) AS rows_in, sum(rows_loaded) AS loaded, sum(rows_quarantined) AS quarantined, "
                      "count(*) FILTER (WHERE status = 'rejected') AS files_rejected FROM ingest_ledger GROUP BY 1 ORDER BY 1").df()
    viol = {k: v for k, v in check_invariants(con, load_contracts(CONTRACTS)).items() if v}
    q = con.execute("SELECT source, reason, count(*) AS rows FROM quarantine GROUP BY ALL ORDER BY rows DESC").df()
    out.append({"id": "clean", "title": "First-party data is current and clean",
                "status": "pass" if not viol else "fail", "value": int(led.quarantined.sum()), "of": int(led.rows_in.sum()),
                "unit": "rows held back by a contract", "note": f"{len(viol)} invariant violations",
                "evidence": q if len(q) else led})

    stale = con.execute("""SELECT person_key, first_name || ' ' || last_name AS name, address_line, address_source,
                                  address_confirmed_at, address_agreement
                           FROM products.dim_person WHERE address_is_stale OR address_agreement < 1
                           ORDER BY address_is_stale DESC, address_agreement""").df()
    n = con.execute("SELECT count(*) FROM products.dim_person").fetchone()[0]
    out.append({"id": "stale", "title": "Stale or low-confidence records flagged", "status": "warn" if len(stale) else "pass",
                "value": len(stale), "of": int(n), "unit": "people with a stale or disputed address",
                "note": "flagged, kept out of mail audiences", "evidence": stale})

    conflicts = con.execute("""SELECT v.person_key, v.attr, v.value, v.best_source AS source, v.observations, v.last_observed_at
                               FROM products.int_attribute_values v
                               JOIN (SELECT person_key, attr FROM products.int_attribute_winners WHERE distinct_values > 1) w
                                 USING (person_key, attr)
                               ORDER BY v.person_key, v.attr, v.observations DESC""").df()
    np_ = conflicts.person_key.nunique()
    out.append({"id": "conflicts", "title": "First- and third-party data cross-compared", "status": "warn" if np_ else "pass",
                "value": int(np_), "of": int(n), "unit": "people whose sources disagree on an attribute",
                "note": "the most trusted source wins; every value is kept with its source", "evidence": conflicts})

    s = identity_scores(con, run)
    out.append({"id": "identity", "title": "Identity resolution validated", "status": "pass" if s["precision"] >= 0.99 else "warn",
                "value": s["precision"], "of": None, "unit": "pairwise precision against the answer key",
                "note": f"recall {s['recall']:.3f}; {s['people_resolved_exactly']:.1%} of {s['people']:,} people resolved exactly",
                "evidence": pd.DataFrame([{"measure": k, "value": v} for k, v in s.items()])})

    bad = con.execute("""SELECT a.person_key, p.personal_email FROM products.mart_activation_audience a
                         JOIN products.dim_person p USING (person_key)
                         WHERE a.email_ok AND (sha256(p.personal_email) IN (SELECT email_sha256 FROM suppression WHERE email_sha256 IS NOT NULL)
                               OR p.personal_email IN (SELECT email FROM current_email WHERE event_type = 'unsubscribe'))""").df()
    blocked = con.execute("SELECT count(*) FROM products.mart_activation_audience WHERE NOT email_ok").fetchone()[0]
    out.append({"id": "consent", "title": "Consent honoured in every audience", "status": "pass" if bad.empty else "fail",
                "value": len(bad), "of": int(n), "unit": "emailable people who unsubscribed or asked to be deleted",
                "note": f"{blocked:,} people held out of email for consent or a missing address", "evidence": bad})

    d = summary.get("person_deletion", {})
    left = (d.get("deleted_people_with_data_left") or {}).get("after")
    out.append({"id": "deletion", "title": "Deletion requests carried out", "status": "pass" if not left and not d.get("innocent_records_removed") else "warn",
                "value": left if left is not None else 0, "of": None, "unit": "deleted people with any record left",
                "note": f"{d.get('innocent_records_removed', 0)} records of anyone else removed; {d.get('held_for_review', 0)} sent to a person",
                "evidence": pd.DataFrame([{"measure": k, "value": json.dumps(v) if isinstance(v, dict) else v} for k, v in d.items()])})
    return out


# ----------------------------------------------------------------- identity: evidence, split, merge, undo
def _truth(con, run: Path) -> tuple[pd.DataFrame, pd.Series]:
    from ..identity.prepare import build_records
    recs, po = build_records(con)
    return recs, truth_for(recs, po, run)


def identity_scores(con, run: Path, overrides: dict | None = None) -> dict:
    """Pairwise precision and recall over every matchable record, and the share of people whose
    records form exactly one cluster with nobody else's. overrides: unique_id -> person_key."""
    recs, truth = _truth(con, run)
    ir = con.execute("SELECT unique_id, person_key FROM identity_records").df().set_index("unique_id")["person_key"]
    if overrides:
        ir = ir.copy()
        for u, k in overrides.items():
            ir.loc[u] = k
    return pairwise_scores(ir.reindex(truth.index), truth)


FIELDS = {"First name": "first_name", "Last name": "last_name", "Email": "email", "Phone": "phone",
          "Street": "street", "Zip": "zip", "Date of birth": "dob"}


def match_evidence(con, person_keys: list[str]) -> pd.DataFrame:
    """Every scored pair inside the given persons: the match probability and, field by field, whether
    the two records agree, differ, or one of them is missing the value."""
    recs = con.execute("SELECT * FROM identity_records WHERE list_contains(?, person_key)", [person_keys]).df().set_index("unique_id")
    if recs.empty:
        return pd.DataFrame()
    ids = list(recs.index)
    pairs = con.execute("""SELECT unique_id_l, unique_id_r, match_probability FROM identity_pairs
                           WHERE list_contains(?, unique_id_l) AND list_contains(?, unique_id_r)""", [ids, ids]).df()
    rows = []
    for a, b, p in pairs.itertuples(index=False):
        ra, rb = recs.loc[a], recs.loc[b]
        row = {"Record A": a, "Record B": b, "Probability": round(float(p), 3)}
        for label, col in FIELDS.items():
            x, y = ra.get(col), rb.get(col)
            if pd.isna(x) or pd.isna(y) or x in ("", None) or y in ("", None):
                row[label] = "missing"
            else:
                row[label] = "agree" if str(x).strip().lower() == str(y).strip().lower() else "differ"
        rows.append(row)
    return pd.DataFrame(rows).sort_values("Probability", ascending=False).reset_index(drop=True)


@dataclass
class IdentityEdits:
    """Split and merge on a private overlay of the identity map, with undo. Nothing in the city changes;
    each edit is rescored against the answer key."""
    overrides: dict = field(default_factory=dict)
    history: list = field(default_factory=list)

    def split(self, unique_id: str) -> None:
        self.history.append(dict(self.overrides))
        self.overrides[unique_id] = "PKsplit_" + unique_id

    def merge(self, con, keep: str, absorb: str) -> None:
        self.history.append(dict(self.overrides))
        members = con.execute("SELECT unique_id FROM identity_records WHERE person_key = ?", [absorb]).df().unique_id
        moved = [u for u, k in self.overrides.items() if k == absorb]
        for u in list(members) + moved:
            self.overrides[u] = keep

    def undo(self) -> bool:
        if not self.history:
            return False
        self.overrides = self.history.pop()
        return True


# ----------------------------------------------------------------- ingestion failure lab
def failure_lab(demo: Demo, p_429: float, p_503: float, p_drop: float, crash_after_page: int | None,
                work: str | Path | None = None) -> dict:
    """Sync every order from the store's API into an empty landing folder under the given faults;
    if asked, crash after a page, then run the sync again so it resumes from its saved cursor.
    Reconcile: orders the store holds, order ids received (no loss), and copies received (no duplicates)."""
    from ..ingest.connectors.store_api import StoreClient, sync_orders
    from ..store.server import Faults, MockStore
    work = Path(work or tempfile.mkdtemp(prefix="plumbline-faultlab-"))
    landing = work / "landing"
    events = []

    class Crash(Exception):
        pass

    def boom(page):
        if crash_after_page and page == crash_after_page and not events:
            events.append(f"crashed after page {page}: the process died mid-sync")
            raise Crash()
    try:
        f = Faults(p_429=p_429, p_503=p_503, p_drop=p_drop, retry_after=0, seed=7)
        t0 = time.time()
        with MockStore.from_run(demo.run, "lab-token", f) as s:
            client = StoreClient(s.url, "lab-token", base_delay=0.001, max_delay=0.01, max_retries=12, sleep=lambda x: None)
            runs = []
            try:
                runs.append(sync_orders(client, landing, page_size=100, after_page=boom))
            except Crash:
                state = json.loads((landing / "_sync_state.json").read_text())
                events.append(f"saved cursor: page {state['in_progress']['page']} of sync {state['in_progress']['sync_id']}")
                runs.append(sync_orders(client, landing, page_size=100))
                events.append("restarted: resumed from the saved cursor" if runs[-1].resumed else "restarted from scratch")
        got = pd.concat([pd.read_csv(p, dtype=str) for p in sorted(landing.glob("orders_*.csv"))], ignore_index=True)
        expected = pd.read_parquet(demo.run / "store" / "orders.parquet", columns=["order_id"]).order_id
        return {"expected": int(expected.nunique()), "received": int(got.order_id.nunique()),
                "copies": int(len(got)), "missing": int(len(set(expected) - set(got.order_id))),
                "requests": sum(r.requests for r in runs), "retries": sum(r.retries for r in runs),
                "throttled": sum(r.throttled for r in runs), "pages": sum(r.pages for r in runs),
                "errors": {k: sum(r.by_error.get(k, 0) for r in runs) for k in {e for r in runs for e in r.by_error}},
                "resumed": any(r.resumed for r in runs), "events": events, "seconds": round(time.time() - t0, 1)}
    finally:
        shutil.rmtree(work, ignore_errors=True)


# ----------------------------------------------------------------- audiences
FLAGS = {"is_customer": "Customers", "lapsed": "Lapsed (no order in 180 days)", "high_value": "High value (top decile)",
         "recent_mover": "Moved in the last 90 days", "homeowner": "Homeowners", "b2b2c_linked": "Work email linked"}
CHANNELS = {"email_ok": "Email", "mail_ok": "Mail", "phone_ok": "Phone"}
_WORDS = [  # phrase -> (field, value); checked in order, longest first, so "not customers" wins over "customers"
    (r"\bnever (bought|ordered|purchased)|non[- ]?customers?|prospects?\b", ("is_customer", False)),
    (r"\b(customers?|buyers?|bought|purchased|ordered)\b", ("is_customer", True)),
    (r"\b(lapsed|inactive|churn\w*|haven'?t (bought|purchased|ordered)|win[- ]?back)\b", ("lapsed", True)),
    (r"\b(high[- ]value|big spenders?|vips?|top customers?|best customers?)\b", ("high_value", True)),
    (r"\b(movers?|moved|relocat\w+|new address)\b", ("recent_mover", True)),
    (r"\b(renters?|renting|tenants?)\b", ("homeowner", False)),
    (r"\b(homeowners?|own (a|their) home)\b", ("homeowner", True)),
    (r"\b(work email|b2b2c|professionals?)\b", ("b2b2c_linked", True)),
    (r"\b(affluent|wealthy|high[- ]income|rich)\b", ("income_band", 4)),
    (r"\b(e-?mail\w*)\b", ("channel", "email_ok")),
    (r"\b(mail|postal|direct mail|mailers?)\b", ("channel", "mail_ok")),
    (r"\b(phone|sms|text|call)\b", ("channel", "phone_ok")),
]


def parse_audience(text: str) -> tuple[dict, list[str]]:
    """A sentence -> typed rules {flags: {field: bool}, income_min: int|None, channel: str|None}, and the
    phrases it understood. Nothing is guessed: words it does not know change nothing, and the rules
    are shown back before anything runs."""
    t = text.lower()
    rules = {"flags": {}, "income_min": None, "channel": None}
    understood = []
    for pat, (f, v) in _WORDS:
        m = re.search(pat, t)
        if not m:
            continue
        if f == "channel":
            if rules["channel"] is None:
                rules["channel"] = v
        elif f == "income_band":
            rules["income_min"] = v
        elif f not in rules["flags"]:
            rules["flags"][f] = v
        else:
            continue
        understood.append(m.group(0))
        t = t[:m.start()] + " " * (m.end() - m.start()) + t[m.end():]
    m = re.search(r"income (band )?(\d)\+?", text.lower())
    if m:
        rules["income_min"] = int(m.group(2))
        understood.append(m.group(0))
    return rules, understood


def audience(con, rules: dict) -> dict:
    """Run typed rules against the audience mart. Returns the segment, who can be reached on the
    chosen channel, and everyone excluded with the reason."""
    where, params = [], []
    for f, v in rules.get("flags", {}).items():
        if f not in FLAGS:
            raise ValueError(f"unknown rule field {f!r}")
        where.append(f"a.{f} = ?")          # strict: someone whose value is unknown is in neither "homeowners" nor "renters"
        params.append(bool(v))
    if rules.get("income_min"):
        where.append("a.income_band >= ?")
        params.append(int(rules["income_min"]))
    sql = ("SELECT a.*, p.personal_email, p.address_line, p.address_is_stale, p.phone AS phone_number "
           "FROM products.mart_activation_audience a JOIN products.dim_person p USING (person_key)"
           + (" WHERE " + " AND ".join(where) if where else ""))
    seg = con.execute(sql, params).df()
    total = con.execute("SELECT count(*) FROM products.mart_activation_audience").fetchone()[0]
    ch = rules.get("channel")
    if ch and ch not in CHANNELS:
        raise ValueError(f"unknown channel {ch!r}")
    if ch:
        ok = seg[ch].fillna(False).astype(bool)
        why = {"email_ok": lambda r: "no email on file" if pd.isna(r.personal_email) else "unsubscribed or asked to be deleted",
               "mail_ok": lambda r: "no address on file" if pd.isna(r.address_line) else "address may be stale",
               "phone_ok": lambda r: "no phone on file" if pd.isna(r.phone_number) else "asked to be deleted"}[ch]
        excluded = seg[~ok].copy()
        excluded["reason"] = [why(r) for r in excluded.itertuples()]
        reach = seg[ok]
    else:
        excluded, reach = seg.iloc[:0].assign(reason=[]), seg
    return {"segment": int(len(seg)), "population": int(total), "reachable": int(len(reach)),
            "excluded": excluded[["person_key", "first_name", "last_name", "reason"]].reset_index(drop=True),
            "members": reach[["person_key", "first_name", "last_name"]].reset_index(drop=True)}


def provenance(con, person_key: str) -> pd.DataFrame:
    """Every attribute the golden profile holds for one person, the source it came from, when that
    source last saw it, and how many distinct values the sources gave."""
    return con.execute("""SELECT attr AS attribute, value, source, last_observed_at AS seen, distinct_values AS "values seen",
                                 agreement FROM products.int_attribute_winners WHERE person_key = ? ORDER BY attr""",
                       [person_key]).df()


# ----------------------------------------------------------------- deletion: replay check
def replay_after_deletion(demo: Demo, person_id: str) -> dict:
    """Delete one person on a copy, then deliver every original file again as if a sender re-sent
    them all, and count how many of that person's records came back (must be 0) and how many of
    everyone else's survived (must be all)."""
    from ..ingest.contracts import load_contracts
    from ..ingest.engine import ingest
    from ..privacy.evaluate import snapshot
    from . import queries as Q
    work = Path(tempfile.mkdtemp(prefix="plumbline-replay-"))
    try:
        first = Q.deletion_sandbox(demo, person_id, work / "first", keep=True)
        run, wh = first["run"], first["warehouse"]
        for p in sorted((run / "sources").rglob("*.csv")):
            if "privacy" not in p.parts:
                p.rename(p.with_name("resent_" + p.name))       # new file names: the loader sees new deliveries
        ingest(run, wh, load_contracts(CONTRACTS))
        with duckdb.connect(str(wh), read_only=True) as con:
            after = snapshot(con)
        links = Q.links(run)
        back = sum(1 for src, ids in after.items() for rid in ids
                   if links[(links.source == src) & (links.record_id == rid)].person_id.eq(person_id).any())
        return {"deleted": first["removed"], "came_back": int(back),
                "files_redelivered": len(list((run / "sources").rglob("resent_*.csv")))}
    finally:
        shutil.rmtree(work, ignore_errors=True)


# ----------------------------------------------------------------- activity log
class ActivityLog:
    """Append-only JSON lines next to the demo: every operation a visitor runs, who, what, and the result.
    Bounded (a public page must not grow a file forever) and tolerant of a half-written last line."""

    _lock = threading.Lock()

    def __init__(self, path: str | Path, keep: int = 5000):
        self.path = Path(path)
        self.keep = keep

    def add(self, actor: str, action: str, result: dict | str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps({"at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"), "who": actor,
                           "action": action, "result": result}, default=str) + "\n"
        with self._lock:
            with open(self.path, "a") as f:
                f.write(line)
            lines = self.path.read_text().splitlines(keepends=True)
            if len(lines) > 2 * self.keep:
                tmp = self.path.with_suffix(".tmp")
                tmp.write_text("".join(lines[-self.keep:]))
                tmp.replace(self.path)

    def recent(self, n: int = 50) -> pd.DataFrame:
        if not self.path.exists():
            return pd.DataFrame(columns=["at", "who", "action", "result"])
        rows = []
        for x in self.path.read_text().splitlines()[-n:]:
            try:
                rows.append(json.loads(x))
            except json.JSONDecodeError:
                continue
        if not rows:
            return pd.DataFrame(columns=["at", "who", "action", "result"])
        return pd.DataFrame(rows[::-1])
