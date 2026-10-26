"""Person-level deletion: close leaks, never delete someone who did not ask, keep nothing readable."""
from __future__ import annotations

import re
import shutil
from pathlib import Path

import duckdb
import numpy as np
import pytest

from plumbline.identity.pipeline import resolve
from plumbline.ingest.connectors.store_api import StoreClient, sync_orders
from plumbline.ingest.contracts import load_contracts
from plumbline.ingest.engine import ingest
from plumbline.ingest.checks import check_invariants
from plumbline.privacy import tombstones
from plumbline.privacy.evaluate import deletion_report, snapshot
from plumbline.privacy.person_deletion import expand_deletions
from plumbline.store.db import build_store
from plumbline.store.server import MockStore
from plumbline.synth.config import SynthConfig
from plumbline.synth.generate import generate
from plumbline.synth.reference import load_reference

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = load_contracts(ROOT / "sources")


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    d = tmp_path_factory.mktemp("priv")
    run = d / "run"
    # a higher deletion rate than production so a small world still has leaks to close
    generate(load_reference(ROOT / "tests" / "fixtures" / "reference"),
             SynthConfig(seed=5, n_people=4000, deletion_request_rate=0.03), run)
    build_store(run)
    with MockStore.from_run(run, "t") as s:
        sync_orders(StoreClient(s.url, "t", sleep=lambda x: None), run / "sources" / "store")
    wh = d / "w.duckdb"
    ingest(run, wh, CONTRACTS)
    with duckdb.connect(str(wh)) as con:
        before = deletion_report(con, run)
        resolve(con)
        snap = snapshot(con)
        stats = expand_deletions(con, CONTRACTS)
        after = deletion_report(con, run, snap)
    return run, wh, before, stats, after


@pytest.fixture
def copy(world, tmp_path):
    run, wh, *_ = world
    r, w = tmp_path / "run", tmp_path / "w.duckdb"
    shutil.copytree(run, r)
    shutil.copy(wh, w)
    return r, w


def test_person_level_deletion_closes_leaks(world):
    _, _, before, stats, after = world
    assert before["people_with_any_record_left"] > 0, "fixture must have leaks to close"
    assert after["people_with_any_record_left"] < before["people_with_any_record_left"]
    assert stats["persons_deleted"] > 0


def test_nobody_who_did_not_ask_is_deleted(world):
    _, _, _, _, after = world
    assert sum(after["innocent_records_removed"].values()) == 0, after


def test_tombstones_hold_no_readable_values(world):
    _, wh, *_ = world
    with duckdb.connect(str(wh)) as con:
        keys = [k for (k,) in con.execute("SELECT key_hash FROM deletion_tombstones").fetchall()]
        names = {n for (n,) in con.execute("SELECT DISTINCT lower(last_name) FROM bronze_crm WHERE last_name IS NOT NULL").fetchall()}
    assert keys and all(re.fullmatch(r"(fz|la|fd):[0-9a-f]{64}", k) for k in keys)
    assert not any(n in k for k in keys for n in list(names)[:200] if len(n) > 3)


def test_tombstone_keys_depend_on_the_secret(monkeypatch):
    parts = ("jane", "smith", "12", "MAIN ST", "10025", "1980-01-02")
    a = tombstones.keys_from_parts(*parts)
    monkeypatch.setenv("PLUMBLINE_TOMBSTONE_KEY", "another-secret")
    b = tombstones.keys_from_parts(*parts)
    assert len(a) == 3 and not set(a) & set(b), "without the key the hashes cannot be recomputed"


def test_a_second_pass_deletes_nothing_more(copy):
    _, wh = copy
    with duckdb.connect(str(wh)) as con:
        s = expand_deletions(con, CONTRACTS)
    assert s["persons_deleted"] == 0 and not s["rows_removed"]


def test_person_level_deletions_survive_a_full_replay(copy):
    """Files re-sent after person-level deletion must not bring the person back."""
    run, wh = copy
    with duckdb.connect(str(wh)) as con:
        gone_before = deletion_report(con, run)
    rng = np.random.default_rng(1)
    for i, p in enumerate(sorted((run / "sources").glob("*/*.csv"))):
        if "privacy" in str(p):
            continue
        rows = p.read_text().splitlines()
        body = rows[1:]
        rng.shuffle(body)
        p.with_name(f"{p.stem}__replay{i}.csv").write_text("\n".join([rows[0]] + body) + "\n")
    ingest(run, wh, CONTRACTS)
    with duckdb.connect(str(wh)) as con:
        after = deletion_report(con, run)
        assert {k: v for k, v in check_invariants(con, CONTRACTS).items() if v} == {}
    assert after["people_with_any_record_left"] <= gone_before["people_with_any_record_left"], (gone_before, after)


def test_same_name_same_address_different_birthday_is_not_deleted():
    """Father and son, one name, one address. The son asked to be deleted; his tombstone carries a
    date of birth. The father's vendor record has a different one and must survive. A record with
    no date of birth cannot be told apart and is deleted: only a known disagreement vetoes."""
    from plumbline.ingest.engine import ensure_schema
    import pandas as pd
    con = duckdb.connect()
    ensure_schema(con, CONTRACTS)
    son = tombstones.keys_from_parts("brian", "johnson", "12", "MAIN ST", "10025", "1975-04-11")
    con.executemany("INSERT INTO deletion_tombstones VALUES ('t1', ?, 'purged:vendor', 'R1', 'strong', now())",
                    [[k] for k in son])
    recs = pd.DataFrame({
        "unique_id": ["vendor:VDAD", "crm:CNODOB"], "person_key": ["PKdad", "PKnodob"],
        "first_name": ["brian", "brian"], "last_name": ["johnson", "johnson"], "house": ["12", "12"],
        "street": ["MAIN ST", "MAIN ST"], "zip": ["10025", "10025"], "dob": ["1947-09-04", None],
        "phone": [None, None]})                       # identity_records always carries a phone column
    con.register("r", recs)
    con.execute("CREATE TABLE identity_records AS SELECT * FROM r")
    con.execute("""CREATE TABLE identity_map AS SELECT * FROM (VALUES
        ('vendor', 'VDAD', 'PKdad'), ('crm', 'CNODOB', 'PKnodob')) t(source, record_id, person_key)""")
    s = expand_deletions(con, CONTRACTS)
    left = {r for (r,) in con.execute("SELECT record_id FROM identity_map").fetchall()}
    assert left == {"VDAD"} and s["persons_deleted"] == 1, (left, s)


def test_rerunning_does_not_queue_the_same_review_case_twice(copy):
    """Regression: each nightly run appended the same review cases again (7, 14, 21 rows for the
    same 7 persons), so the queue reviewers worked from never shrank. The case is planted: a deleted
    row that matches one current person on name and zip only, which must go to review, once."""
    _, wh = copy
    with duckdb.connect(str(wh)) as con:
        f, l, z = con.execute("""SELECT first_name, last_name, zip FROM identity_records
                                 WHERE first_name IS NOT NULL AND last_name IS NOT NULL AND zip IS NOT NULL
                                 ORDER BY unique_id LIMIT 1""").fetchone()
        key = tombstones.keys_from_parts(f, l, None, None, z, None)
        assert len(key) == 1 and key[0].startswith("fz:")
        con.execute("INSERT INTO deletion_tombstones VALUES ('planted', ?, 'test', 'R9999999', 'strong', now())", key)
        q = "SELECT count(*), count(DISTINCT (tombstone_id, person_key, reason)) FROM deletion_review WHERE tombstone_id = 'planted'"
        for _ in range(3):
            expand_deletions(con, CONTRACTS)
        n, distinct = con.execute(q).fetchone()
    assert n == distinct >= 1, (n, distinct)


def _phone_case(request_phone: str | None):
    """The son asked to be deleted (request R1, his phone 5550000001). Nobody has a date of birth.
    PKdad: same name and address (two keys), his own phone.  PKnophone: same two keys, no phone.
    PKname: same name and zip only (one key), his own phone.  PKname2: one key, no phone."""
    from plumbline.ingest.contracts import phone_hash
    from plumbline.ingest.engine import ensure_schema
    import pandas as pd
    con = duckdb.connect()
    ensure_schema(con, CONTRACTS)
    con.execute("INSERT INTO suppression VALUES ('R1', NULL, ?, current_date, 'd1')",
                [phone_hash(request_phone) if request_phone else None])
    son = tombstones.keys_from_parts("brian", "johnson", "12", "MAIN ST", "10025", None)
    con.executemany("INSERT INTO deletion_tombstones VALUES ('t1', ?, 'purged:crm', 'R1', 'strong', now())", [[k] for k in son])
    recs = pd.DataFrame({
        "unique_id": ["crm:DAD", "crm:NOPHONE", "crm:NAME", "crm:NAME2"],
        "person_key": ["PKdad", "PKnophone", "PKname", "PKname2"],
        "first_name": ["brian"] * 4, "last_name": ["johnson"] * 4,
        "house": ["12", "12", "40", "41"], "street": ["MAIN ST", "MAIN ST", "OAK AVE", "ELM ST"],
        "zip": ["10025"] * 4, "dob": [None] * 4,
        "phone": ["5550000002", None, "5550000003", None]})
    con.register("r", recs)
    con.execute("CREATE TABLE identity_records AS SELECT * FROM r")
    con.execute("""CREATE TABLE identity_map AS SELECT * FROM (VALUES ('crm', 'DAD', 'PKdad'), ('crm', 'NOPHONE', 'PKnophone'),
                   ('crm', 'NAME', 'PKname'), ('crm', 'NAME2', 'PKname2')) t(source, record_id, person_key)""")
    s = expand_deletions(con, CONTRACTS)
    left = {r for (r,) in con.execute("SELECT person_key FROM identity_map").fetchall()}
    queued = dict(con.execute("SELECT person_key, reason FROM deletion_review").fetchall())
    return s, left, queued


def test_a_person_with_their_own_other_phone_is_not_deleted_for_someone_else():
    """Regression for scale: at 300,000 people, 45 records of people who never asked were deleted, and
    the review queue grew from 7 persons (100,000) to 1,097 (1,000,000). Each wrongly deleted person
    shared a name and a home with a requester and had no date of birth to tell them apart. Every record
    carrying the requester's phone is purged at load, so a person who still has a phone of their own is
    someone else, or the requester with a new number: a person decides, not the pipeline."""
    s, left, queued = _phone_case("5550000001")
    assert "PKdad" in left and queued.get("PKdad") == "keys_match_but_phone_differs"    # two keys, own phone: review
    assert "PKnophone" not in left                                                        # two keys, nothing to tell apart
    assert "PKname" in left and "PKname" not in queued                                    # one key, own phone: a namesake
    assert "PKname2" in left and queued.get("PKname2") == "one_key_matches"               # one key, no phone: review
    assert s["persons_deleted"] == 1


def test_a_request_without_a_phone_decides_nothing_about_phones():
    """Without a phone on the request the rule cannot apply: two persons match one tombstone on two
    keys, and the older rule holds them both for a person instead of guessing which one asked."""
    s, left, queued = _phone_case(None)
    assert {"PKdad", "PKnophone"} <= left and s["persons_deleted"] == 0
    assert queued.get("PKdad") == queued.get("PKnophone") == "tombstone_matches_several_persons"
    assert queued.get("PKname") == "one_key_matches"
