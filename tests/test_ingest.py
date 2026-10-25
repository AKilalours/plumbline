"""Module A contract. Each test states the promise it holds the loader to."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import pytest

from plumbline.ingest import engine
from plumbline.ingest.checks import check_invariants
from plumbline.ingest.contracts import ContractError, load_contract, load_contracts
from plumbline.ingest.engine import ingest
from plumbline.ingest.faults import KINDS, inject
from plumbline.synth.config import SynthConfig
from plumbline.synth.generate import generate
from plumbline.synth.reference import load_reference

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "reference"
CONTRACTS = load_contracts(ROOT / "sources")
KEYS = {"crm": "crm_id", "vendor": "vendor_record_id", "email": "event_id"}


@pytest.fixture(scope="module")
def base_run(tmp_path_factory):
    d = tmp_path_factory.mktemp("synth") / "run"
    generate(load_reference(FIXTURE), SynthConfig(seed=21, n_people=1500), d)
    return d


@pytest.fixture
def run(base_run, tmp_path):
    d = tmp_path / "run"
    shutil.copytree(base_run, d)
    return d


def _con(wh):
    return duckdb.connect(str(wh))


def _current(con, source):
    k = KEYS[source]
    return dict(con.execute(f"SELECT {k}, _row_hash FROM current_{source}").fetchall())


# ---------------------------------------------------------------- independent oracle
def _sha(s):
    return hashlib.sha256(s.encode()).hexdigest()


def expected_keys(run_dir: Path, source: str) -> set[str]:
    """Re-derive the current key set with plain pandas, independently of the engine's SQL."""
    deliveries = json.loads((run_dir / "deliveries.json").read_text())
    groups: dict[str, tuple[int, str]] = {}
    for d in deliveries:
        if d["source"] != source:
            continue
        g = re.sub(r"_v\d+(?=\.csv$)", "", d["path"])
        if g not in groups or d["version"] > groups[g][0]:
            groups[g] = (d["version"], d["path"])
    priv = [d for d in deliveries if d["source"] == "privacy"][0]
    dels = pd.read_csv(run_dir / priv["path"], dtype=str)
    bad_p = set(dels["phone_sha256"].dropna())
    req_phone_by_email: dict[str, set] = {}
    for e_h, p_h in zip(dels["email_sha256"], dels["phone_sha256"]):
        if isinstance(e_h, str):
            req_phone_by_email.setdefault(e_h, set()).add(p_h if isinstance(p_h, str) else None)
    alive: set[str] = set()
    for _, path in groups.values():
        df = pd.read_csv(run_dir / path, dtype=str, keep_default_na=False)
        for _, r in df.iterrows():
            e = r.get("email", "").strip().lower()
            p = re.sub(r"\D", "", r.get("phone", ""))[-10:]
            ph = _sha(p) if len(p) == 10 else None
            if ph and ph in bad_p:
                continue                      # a phone match erases
            req = req_phone_by_email.get(_sha(e)) if e else None
            if req is not None and (ph is None or None in req or ph in req):
                continue                      # an email match erases unless the row's phone is someone else's
            alive.add(r[KEYS[source]])
    return alive


# ---------------------------------------------------------------- tests
def test_current_tables_match_independent_oracle(run, tmp_path):
    wh = tmp_path / "wh.duckdb"
    ingest(run, wh, CONTRACTS)
    con = _con(wh)
    for s in KEYS:
        assert set(_current(con, s)) == expected_keys(run, s), s
    assert all(v == 0 for v in check_invariants(con, CONTRACTS).values()), check_invariants(con, CONTRACTS)


def test_rerun_is_a_no_op(run, tmp_path):
    wh = tmp_path / "wh.duckdb"
    ingest(run, wh, CONTRACTS)
    con = _con(wh)
    before = {s: _current(con, s) for s in KEYS}
    n_rows = con.execute("SELECT count(*) FROM delivery_rows").fetchone()[0]
    con.close()
    rep = ingest(run, wh, CONTRACTS).totals()
    assert rep["deliveries_loaded"] == 0 and rep["rows_loaded"] == 0
    con = _con(wh)
    assert {s: _current(con, s) for s in KEYS} == before
    assert con.execute("SELECT count(*) FROM delivery_rows").fetchone()[0] == n_rows


def test_corrected_v2_replaces_v1_without_dropping_unchanged_rows(run, tmp_path):
    """Regression: a v2 that repeats most of v1 once made all unchanged week-6 rows vanish."""
    wh = tmp_path / "wh.duckdb"
    ingest(run, wh, CONTRACTS)
    con = _con(wh)
    v1_path = [p for p in (run / "sources/vendor").glob("vendor_week_06_*.csv") if not p.stem.endswith("_v2")][0]
    v1 = pd.read_csv(v1_path, dtype=str, keep_default_na=False)
    v2 = pd.read_csv(next((run / "sources/vendor").glob("vendor_week_06_*_v2.csv")), dtype=str, keep_default_na=False)
    cur = dict(con.execute("SELECT vendor_record_id, full_name FROM current_vendor").fetchall())
    alive = expected_keys(run, "vendor")
    week6 = [k for k in v2["vendor_record_id"] if k in alive]
    assert week6 and all(k in cur for k in week6)
    changed = v2[v1["full_name"] != v2["full_name"]]
    assert len(changed) > 0
    for r in changed.itertuples():
        if r.vendor_record_id in cur:
            assert cur[r.vendor_record_id] == re.sub(r"\s+", " ", r.full_name).strip()


def test_corrected_email_day_uses_final_version(run, tmp_path):
    wh = tmp_path / "wh.duckdb"
    ingest(run, wh, CONTRACTS)
    con = _con(wh)
    v3 = next((run / "sources/email").glob("*_v3.csv"))
    day = re.search(r"(\d{4}-\d{2}-\d{2})", v3.name).group(1).replace("-", "")
    want = set(pd.read_csv(v3, dtype=str)["event_id"]) & expected_keys(run, "email")
    got = {r[0] for r in con.execute(f"SELECT event_id FROM current_email WHERE event_id LIKE 'EM{day}%'").fetchall()}
    assert got == want
    assert con.execute("SELECT count(*) FROM quarantine WHERE reason = 'duplicate_in_delivery'").fetchone()[0] > 0


def test_crm_change_files_win_by_updated_at(run, tmp_path):
    wh = tmp_path / "wh.duckdb"
    ingest(run, wh, CONTRACTS)
    con = _con(wh)
    cur = dict(con.execute("SELECT crm_id, last_name FROM current_crm").fetchall())
    ch = pd.concat([pd.read_csv(p, dtype=str, keep_default_na=False) for p in (run / "sources/crm").glob("crm_changes_*.csv")])
    checked = 0
    for r in ch.itertuples():
        if r.crm_id in cur:
            assert cur[r.crm_id] == r.last_name.strip()
            checked += 1
    assert checked > 0


def test_replayed_old_files_cannot_resurrect_deleted_people(run, tmp_path):
    wh = tmp_path / "wh.duckdb"
    ingest(run, wh, CONTRACTS)
    con = _con(wh)
    purged = con.execute("SELECT coalesce(sum(rows), 0) FROM suppression_log WHERE action = 'purged'").fetchone()[0]
    assert purged > 0
    con.close()
    # Re-send every pre-deletion vendor and CRM file as a "new" delivery with shuffled rows.
    rng = np.random.default_rng(0)
    for i, p in enumerate(sorted((run / "sources").glob("*/*.csv"))):
        if "privacy" in str(p):
            continue
        rows = p.read_text().splitlines()
        body = rows[1:]
        rng.shuffle(body)
        p.with_name(f"{p.stem}__replay{i}.csv").write_text("\n".join([rows[0]] + body) + "\n")
    rep = ingest(run, wh, CONTRACTS).totals()
    assert rep["rows_suppressed"] > 0
    con = _con(wh)
    assert all(v == 0 for v in check_invariants(con, CONTRACTS).values())
    for s in KEYS:
        assert set(_current(con, s)) == expected_keys(run, s), s


def test_bad_rows_go_to_quarantine_with_reasons(run, tmp_path):
    wh = tmp_path / "wh.duckdb"
    ingest(run, wh, CONTRACTS)
    before = _current(_con(wh), "vendor")
    p = next((run / "sources/vendor").glob("vendor_week_01_*.csv"))
    df = pd.read_csv(p, dtype=str, keep_default_na=False).head(5)
    df.loc[0, "zip"] = "ABCDE"
    df.loc[1, "phone"] = "12"
    df.loc[2, "est_income_band"] = "9"
    df.loc[3, "vendor_record_id"] = ""
    df.to_csv(p.with_name("bad.csv"), index=False)
    rep = ingest(run, wh, CONTRACTS)
    r = [x for x in rep.results if x.path.endswith("bad.csv")][0]
    assert r.status == "loaded" and r.rows_quarantined == 4
    reasons = {x[0] for x in _con(wh).execute("SELECT reason FROM quarantine").fetchall()}
    assert {"bad_zip:zip", "bad_phone:phone", "out_of_range:est_income_band", "missing:vendor_record_id"} <= reasons
    assert _current(_con(wh), "vendor") == before


def test_missing_required_column_rejects_whole_file(run, tmp_path):
    """Loading a file without `email` would overwrite good emails with NULLs, so refuse it."""
    wh = tmp_path / "wh.duckdb"
    ingest(run, wh, CONTRACTS)
    before = _current(_con(wh), "crm")
    p = next((run / "sources/crm").glob("crm_full_*.csv"))
    pd.read_csv(p, dtype=str, keep_default_na=False).drop(columns=["email"]).to_csv(p.with_name("no_email.csv"), index=False)
    r = [x for x in ingest(run, wh, CONTRACTS).results if x.path.endswith("no_email.csv")][0]
    assert r.status == "rejected" and "missing_columns:email" in r.note
    assert _current(_con(wh), "crm") == before


def test_unknown_column_is_held_for_review_not_fatal(run, tmp_path):
    wh = tmp_path / "wh.duckdb"
    ingest(run, wh, CONTRACTS)
    before = _current(_con(wh), "email")
    p = next((run / "sources/email").glob("*_v1.csv"))
    df = pd.read_csv(p, dtype=str, keep_default_na=False)
    df["campaign_note"] = "x"
    df.to_csv(p.with_name("extra.csv"), index=False)
    r = [x for x in ingest(run, wh, CONTRACTS).results if x.path.endswith("extra.csv")][0]
    assert r.status == "loaded" and r.rows_loaded == 0
    con = _con(wh)
    assert con.execute("SELECT count(*) FROM schema_drift WHERE action = 'unknown_held_for_review'").fetchone()[0] == 1
    assert _current(con, "email") == before


def test_failed_load_rolls_back_completely(run, tmp_path, monkeypatch):
    wh = tmp_path / "wh.duckdb"
    real = engine._insert_new
    calls = {"n": 0}

    def flaky(con, c, rows):
        calls["n"] += 1
        if calls["n"] == 3:
            raise RuntimeError("disk full")
        return real(con, c, rows)

    monkeypatch.setattr(engine, "_insert_new", flaky)
    with pytest.raises(RuntimeError):
        ingest(run, wh, CONTRACTS)
    monkeypatch.setattr(engine, "_insert_new", real)
    con = _con(wh)
    loaded = con.execute("SELECT count(*) FROM ingest_ledger").fetchone()[0]
    orphans = con.execute("""SELECT count(*) FROM delivery_rows WHERE delivery_id NOT IN
                             (SELECT delivery_id FROM ingest_ledger)""").fetchone()[0]
    assert loaded == 2 and orphans == 0
    con.close()
    ingest(run, wh, CONTRACTS)
    con = _con(wh)
    for s in KEYS:
        assert set(_current(con, s)) == expected_keys(run, s)


def test_hundred_random_faults_change_nothing_they_should_not(run, tmp_path):
    """The headline receipt: 100 injected faults, then the current tables equal the clean baseline."""
    wh = tmp_path / "wh.duckdb"
    ingest(run, wh, CONTRACTS)
    con = _con(wh)
    baseline = {s: _current(con, s) for s in KEYS}
    con.close()
    rng = np.random.default_rng(7)
    kinds = [KINDS[i] for i in rng.integers(0, len(KINDS), 100)]
    for n, kind in enumerate(kinds):
        inject(run, rng, kind, n)
        if n % 10 == 9:
            ingest(run, wh, CONTRACTS)
    rep = ingest(run, wh, CONTRACTS)
    con = _con(wh)
    assert {s: _current(con, s) for s in KEYS} == baseline
    assert all(v == 0 for v in check_invariants(con, CONTRACTS).values())
    led = dict(con.execute("SELECT status, count(*) FROM ingest_ledger WHERE path LIKE '%__fault%' GROUP BY 1").fetchall())
    renamed = {hashlib.sha256(p.read_bytes()).hexdigest()
               for p in (run / "sources").glob("*/*__fault*_renamed_required_column.csv")}
    assert led.get("rejected", 0) == len(renamed)   # identical re-sends are skipped, not rejected twice


def test_new_source_onboards_with_yaml_only(run, tmp_path):
    """Adding a source is a config file plus its data; no engine change."""
    cdir = tmp_path / "contracts"
    shutil.copytree(ROOT / "sources", cdir)
    (cdir / "loyalty.yaml").write_text(
        "name: loyalty\nkind: file\nfiles: 'sources/loyalty/*.csv'\nkey: [member_id]\nlatest_by: [updated_at]\n"
        "columns:\n  member_id: {type: string, required: true}\n  email: {type: email, privacy: email}\n"
        "  points: {type: int, min: 0}\n  updated_at: {type: date, required: true}\n")
    (run / "sources/loyalty").mkdir()
    (run / "sources/loyalty/members_2025-12-30.csv").write_text(
        "member_id,email,points,updated_at\nL1,a@inbox.test,10,2025-12-30\nL2,b@inbox.test,-5,2025-12-30\n")
    wh = tmp_path / "wh.duckdb"
    ingest(run, wh, load_contracts(cdir))
    con = _con(wh)
    assert con.execute("SELECT member_id FROM current_loyalty").fetchall() == [("L1",)]
    assert con.execute("SELECT reason FROM quarantine WHERE source = 'loyalty'").fetchall() == [("out_of_range:points",)]


def test_contract_errors_are_caught_at_load(tmp_path):
    p = tmp_path / "x.yaml"
    p.write_text("name: x\nkind: file\nfiles: '*.csv'\nkey: [id]\ncolumns:\n  id: {type: uuid}\n")
    with pytest.raises(ContractError, match="unknown type"):
        load_contract(p)
    p.write_text("name: x\nkind: file\nfiles: '*.csv'\nkey: [missing]\ncolumns:\n  id: {type: string}\n")
    with pytest.raises(ContractError, match="unknown columns"):
        load_contract(p)


def test_deleted_people_never_land_in_quarantine(run, tmp_path):
    """Regression: a broken row from a deleted person, sent after the deletion, was kept in quarantine."""
    wh = tmp_path / "wh.duckdb"
    ingest(run, wh, CONTRACTS)
    dels = pd.read_csv(next((run / "sources/privacy").glob("*.csv")), dtype=str)
    crm = pd.read_csv(next((run / "sources/crm").glob("crm_full_*.csv")), dtype=str, keep_default_na=False)
    hit = crm[crm["email"].str.strip().str.lower().map(_sha).isin(set(dels["email_sha256"]))].head(3).copy()
    assert len(hit) > 0
    hit["zip"] = "BAD"
    hit.to_csv(run / "sources/crm/broken_after_deletion.csv", index=False)
    r = [x for x in ingest(run, wh, CONTRACTS).results if x.path.endswith("broken_after_deletion.csv")][0]
    assert r.rows_suppressed == len(hit) and r.rows_quarantined == 0
    assert check_invariants(_con(wh), CONTRACTS)["quarantine.suppressed_rows"] == 0


def test_rows_dropped_from_a_corrected_file_disappear(run, tmp_path):
    """A _v2 that retracts rows must remove them from current, not just add corrections."""
    wh = tmp_path / "wh.duckdb"
    ingest(run, wh, CONTRACTS)
    v1 = sorted((run / "sources/email").glob("*_v1.csv"))[0]
    if (v1.parent / v1.name.replace("_v1", "_v2")).exists():
        v1 = sorted((run / "sources/email").glob("*_v1.csv"))[1]
    df = pd.read_csv(v1, dtype=str, keep_default_na=False)
    retracted = set(df["event_id"].head(5))
    df[~df["event_id"].isin(retracted)].to_csv(v1.with_name(v1.name.replace("_v1", "_v2")), index=False)
    ingest(run, wh, CONTRACTS)
    cur = set(_current(_con(wh), "email"))
    assert retracted.isdisjoint(cur)
    assert set(df["event_id"].iloc[5:]) & expected_keys(run, "email") <= cur


def test_rebuild_from_scratch_is_identical(run, tmp_path):
    """The runbook's last resort (delete the warehouse, reload every file) must reproduce it exactly."""
    a, b = tmp_path / "a.duckdb", tmp_path / "b.duckdb"
    ingest(run, a, CONTRACTS)
    ingest(run, b, CONTRACTS)
    ca, cb = _con(a), _con(b)
    for s in KEYS:
        assert _current(ca, s) == _current(cb, s)
    q = "SELECT source, reason, count(*) FROM quarantine GROUP BY 1, 2 ORDER BY 1, 2"
    assert ca.execute(q).fetchall() == cb.execute(q).fetchall()


def test_erasure_rule_is_never_null():
    """Regression: a NULL phone made the erase predicate NULL, and NOT NULL hid the row from the
    current view although no deletion request matched it."""
    from plumbline.ingest.erasure import erase_sql
    con = duckdb.connect()
    con.execute("CREATE TABLE suppression (email_sha256 VARCHAR, phone_sha256 VARCHAR)")
    con.execute("INSERT INTO suppression VALUES ('e1', 'p1'), ('e2', NULL)")
    rows = [(None, None, False), ("e9", None, False), (None, "p1", True), ("e1", None, True),
            ("e1", "p1", True), ("e1", "p7", False), ("e2", "p7", True)]
    for e, p, want in rows:
        got = con.execute(f"SELECT {erase_sql('$1', '$2')}", [e, p]).fetchone()[0]
        assert got is want, (e, p, got)


def test_shared_inbox_keeps_family_members_with_their_own_phone(run, tmp_path):
    """A request names one person's email and phone. A family member who shares the inbox but has
    a different phone of their own is not the requester and must not be erased."""
    wh = tmp_path / "wh.duckdb"
    req = sorted((run / "sources" / "privacy").glob("*.csv"))[0]
    dels = pd.read_csv(req, dtype=str)
    crm_files = sorted((run / "sources" / "crm").glob("*.csv"))
    crm = pd.concat([pd.read_csv(f, dtype=str, keep_default_na=False) for f in crm_files])
    wanted = set(dels.email_sha256)
    # a requester's email as it appears in CRM; then add a housemate with a phone of their own
    email = next(e for e in crm.email if e and _sha(e.strip().lower()) in wanted)
    row = crm.iloc[[0]].assign(crm_id="C99999999", first_name="Housemate", email=email, phone="5550000001")
    row.to_csv(crm_files[0].with_name("crm_zz_housemate.csv"), index=False)
    ingest(run, wh, CONTRACTS)
    con = _con(wh)
    assert con.execute("SELECT count(*) FROM current_crm WHERE crm_id = 'C99999999'").fetchone()[0] == 1
    assert con.execute("SELECT count(*) FROM current_crm WHERE email = ?", [email]).fetchone()[0] == 1
