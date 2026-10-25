"""Module B data products (dbt): golden person, history, life events, audience. Each test is a promise."""
from __future__ import annotations

import shutil
from pathlib import Path

import duckdb
import pytest

from plumbline.identity.pipeline import resolve
from plumbline.ingest.connectors.store_api import StoreClient, sync_orders
from plumbline.ingest.contracts import load_contracts
from plumbline.ingest.engine import ingest
from plumbline.privacy.person_deletion import expand_deletions
from plumbline.products.build import DBT_DIR, DbtFailed, build
from plumbline.products.evaluate import score_products
from plumbline.store.db import build_store
from plumbline.store.server import MockStore
from plumbline.synth.config import SynthConfig
from plumbline.synth.generate import generate
from plumbline.synth.reference import load_reference

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = load_contracts(ROOT / "sources")


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    d = tmp_path_factory.mktemp("prod")
    run = d / "run"
    generate(load_reference(ROOT / "tests" / "fixtures" / "reference"), SynthConfig(seed=11, n_people=4000), run)
    build_store(run)
    with MockStore.from_run(run, "t") as s:
        sync_orders(StoreClient(s.url, "t", sleep=lambda x: None), run / "sources" / "store")
    wh = d / "w.duckdb"
    ingest(run, wh, CONTRACTS)
    with duckdb.connect(str(wh)) as con:
        resolve(con)
        expand_deletions(con, CONTRACTS)
    stats = build(wh)
    with duckdb.connect(str(wh), read_only=True) as con:
        score = score_products(con, run)
    return run, wh, stats, score


@pytest.fixture
def wh_copy(world, tmp_path):
    w = tmp_path / "w.duckdb"
    shutil.copy(world[1], w)
    return w


def test_every_dbt_test_passes(world):
    _, _, stats, _ = world
    assert stats["tests"] == stats["tests_passed"] >= 20


def test_golden_values_are_accurate(world):
    a = world[3]["attributes"]
    assert a["phone"]["correct_when_filled"] > 0.98 and a["dob"]["correct_when_filled"] > 0.98
    assert a["current_address"]["correct_when_filled"] > 0.85
    # most of the remaining address error is unknowable: the true address was never reported
    assert world[3]["address_ceiling"] - a["current_address"]["correct_when_filled"] < 0.03


def test_work_and_personal_identities_are_linked(world):
    assert world[3]["b2b2c"]["linked_and_both_correct_of_linkable"] > 0.95


def test_moves_used_for_marketing_are_mostly_real(world):
    m = world[3]["moves"]
    assert m["used_for_segments"]["precision"] > 0.75
    assert m["used_for_segments"]["precision"] > m["all_including_vendor_only"]["precision"]


def test_point_in_time_build_uses_only_what_was_known(wh_copy):
    """As of an earlier date, the golden person may not use any later observation."""
    build(wh_copy, as_of="2024-12-31")
    with duckdb.connect(str(wh_copy), read_only=True) as con:
        late = con.execute("""SELECT count(*) FROM products.int_observations_typed
                              WHERE observed_at >= TIMESTAMP '2025-01-01'""").fetchone()[0]
        n = con.execute("SELECT count(*) FROM products.dim_person").fetchone()[0]
        last = con.execute("SELECT max(last_order_at) FROM products.dim_person").fetchone()[0]
    assert late == 0 and n > 0 and str(last) < "2025-01-01"


def test_events_late_on_the_as_of_day_count(wh_copy):
    """Regression: comparing to the bare as_of date (midnight) silently dropped every event on that
    day; it once let 15 unsubscribes through. 'As of a date' means the end of that day."""
    with duckdb.connect(str(wh_copy), read_only=True) as con:
        order_id, ts = con.execute("""SELECT order_id, created_at FROM current_store
                                      WHERE hour(created_at) >= 12 ORDER BY created_at DESC LIMIT 1 OFFSET 5""").fetchone()
        person = con.execute("SELECT person_key FROM identity_map WHERE source = 'store_order' AND record_id = ?", [order_id]).fetchone()[0]
    build(wh_copy, as_of=ts.strftime("%Y-%m-%d"))
    with duckdb.connect(str(wh_copy), read_only=True) as con:
        last = con.execute("SELECT last_order_at FROM products.dim_person WHERE person_key = ?", [person]).fetchone()[0]
    assert last >= ts


def test_consent_is_always_current_even_for_a_past_audience(wh_copy):
    """An audience rebuilt as of an earlier date still honours unsubscribes that came later."""
    build(wh_copy, as_of="2024-06-30")
    with duckdb.connect(str(wh_copy), read_only=True) as con:
        bad = con.execute("""SELECT count(*) FROM products.mart_activation_audience a
                             JOIN products.dim_person p USING (person_key)
                             WHERE a.email_ok AND p.personal_email IN
                                   (SELECT email FROM current_email WHERE event_type = 'unsubscribe')""").fetchone()[0]
    assert bad == 0


def test_compliance_test_fails_when_the_rule_is_broken(wh_copy, tmp_path):
    """The audience compliance test must actually bite: drop the unsubscribe rule and dbt must fail."""
    proj = tmp_path / "dbt"
    shutil.copytree(DBT_DIR, proj, ignore=shutil.ignore_patterns("target", "logs"))
    m = proj / "models" / "marts" / "mart_activation_audience.sql"
    m.write_text(m.read_text().replace("and p.personal_email not in (select email from unsubscribed) as email_ok", "as email_ok"))
    import plumbline.products.build as b
    orig = b.DBT_DIR
    b.DBT_DIR = proj
    try:
        with pytest.raises(DbtFailed, match="audience_never_emails"):
            build(wh_copy)
    finally:
        b.DBT_DIR = orig
