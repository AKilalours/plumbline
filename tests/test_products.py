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
        from plumbline.ml.reference import load_reference_tables
        load_reference_tables(con, ROOT / "tests" / "fixtures" / "reference")
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


def test_money_is_summed_exactly(world):
    """Regression: revenue summed as floating point differed in the last bit between machines (the
    order DuckDB's threads add values in depends on the CPU count), which changed the model's data
    fingerprint for identical data. Summed as exact cents, the order cannot matter."""
    _, wh, *_ = world
    with duckdb.connect(str(wh), read_only=True) as con:
        types = dict(con.execute("""SELECT table_name || '.' || column_name, data_type FROM information_schema.columns
                                    WHERE table_schema = 'products' AND column_name IN ('revenue', 'revenue_window', 'household_revenue')""").fetchall())
        assert types and all(t.startswith("DECIMAL") for t in types.values()), types
        # the same prices added in two different orders give the same sum
        a, b = con.execute("""SELECT sum(cast(total_price AS DECIMAL(14, 2)) ORDER BY order_id),
                                     sum(cast(total_price AS DECIMAL(14, 2)) ORDER BY order_id DESC) FROM current_store""").fetchone()
        assert a == b


def _render(sql: str, tables: dict[str, str]) -> str:
    import re
    return re.sub(r"\{\{\s*ref\('([a-z_]+)'\)\s*\}\}", lambda m: tables[m.group(1)], sql)


def test_a_chain_of_look_alike_spellings_still_gets_one_canonical():
    """Regression: "alike" is not transitive. A ~ B and B ~ C but not A ~ C made B the canonical of A
    while B's canonical was C, and the 1,000,000-person build failed every_address_spelling_has_one_canonical."""
    import duckdb
    dbt = Path(__file__).resolve().parents[1] / "dbt"
    model = (dbt / "models" / "intermediate" / "int_address_canonical.sql").read_text()
    check = (dbt / "tests" / "every_address_spelling_has_one_canonical.sql").read_text()
    a, b, c = "1901 DORCHESTERROAD APT 26|11226", "1901 DORCHESTER ROAD APT 26|11226", "1901 DORCHESTER AVE|11226"
    with duckdb.connect() as con:
        sim = lambda x, y: con.execute("SELECT jaro_winkler_similarity(?, ?)", [x.split("|")[0], y.split("|")[0]]).fetchone()[0]
        assert sim(a, b) >= 0.9 and sim(b, c) >= 0.9 and sim(a, c) < 0.9          # the chain is real
        con.execute("CREATE TABLE obs (person_key VARCHAR, attr VARCHAR, value VARCHAR)")
        con.executemany("INSERT INTO obs VALUES ('PK1', 'address', ?)", [[a]] + [[b]] * 2 + [[c]] * 3)
        con.execute(f"CREATE TABLE canon AS {_render(model, {'int_observations_typed': 'obs'})}")
        got = dict(con.execute("SELECT value, canonical FROM canon").fetchall())
        assert got == {a: c, b: c, c: c}
        assert con.execute(_render(check, {"int_address_canonical": "canon"})).fetchall() == []
