"""Module B contract: records become persons accurately, without peeking at the answer key."""
from __future__ import annotations

import ast
import re
import shutil
from pathlib import Path

import duckdb
import pandas as pd
import pytest

from plumbline.identity.cluster import constrained_clusters, first_names_compatible, jaro_winkler
from plumbline.identity.evaluate import score_warehouse
from plumbline.identity.pipeline import resolve
from plumbline.identity.standardize import split_full_name, standardize_street
from plumbline.ingest.contracts import load_contracts
from plumbline.ingest.engine import ingest
from plumbline.ingest.connectors.store_api import StoreClient, sync_orders
from plumbline.store.db import build_store
from plumbline.store.server import MockStore
from plumbline.synth.config import SynthConfig
from plumbline.synth.generate import generate
from plumbline.synth.reference import load_reference

ROOT = Path(__file__).resolve().parents[1]
MATCHER = ["standardize", "prepare", "match", "cluster", "pipeline"]


@pytest.fixture(scope="module")
def warehouse(tmp_path_factory):
    d = tmp_path_factory.mktemp("id")
    run = d / "run"
    generate(load_reference(ROOT / "tests" / "fixtures" / "reference"), SynthConfig(seed=21, n_people=3000), run)
    build_store(run)
    with MockStore.from_run(run, "t") as s:
        sync_orders(StoreClient(s.url, "t", sleep=lambda x: None), run / "sources" / "store")
    wh = d / "w.duckdb"
    ingest(run, wh, load_contracts(ROOT / "sources"))
    with duckdb.connect(str(wh)) as con:
        stats = resolve(con)
    return run, wh, stats


def test_street_and_name_standardization():
    assert standardize_street("1090 EAST 18 STREET") == standardize_street("1090 E 18TH ST") == ("1090", "E 18 ST", None)
    assert standardize_street("75-23 113 STREET APT 38") == ("75-23", "113 ST", "38")
    assert split_full_name("SMITH, CANDACE") == split_full_name("Candace J. Smith") == ("candace", "smith")


def test_nickname_and_typo_compatibility():
    assert first_names_compatible("jon", "jonathan") and first_names_compatible("jon", "john")
    assert first_names_compatible("bob", "robert") and first_names_compatible("katelyn", "katelin")
    assert not first_names_compatible("katelyn", "christina")
    assert abs(jaro_winkler("martha", "marhta") - 0.9611) < 1e-3   # textbook value


def test_matcher_never_imports_the_generator_or_answer_key():
    """The scores mean nothing if the matcher can see how the data was damaged."""
    for mod in MATCHER:
        tree = ast.parse((ROOT / "src" / "plumbline" / "identity" / f"{mod}.py").read_text())
        names = [n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
        names += [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
        assert not [n for n in names if "synth" in n or "evaluate" in n], mod
        src = (ROOT / "src" / "plumbline" / "identity" / f"{mod}.py").read_text()
        assert not re.search(r"""["'/]truth["'/]""", src), mod   # no truth/ paths or "truth" keys


def test_accuracy_beats_exact_match_baseline(warehouse):
    run, wh, _ = warehouse
    with duckdb.connect(str(wh)) as con:
        s = score_warehouse(con, run)
    p = s["pairwise"]
    assert p["precision"] >= 0.95 and p["recall"] >= 0.95 and p["f1"] >= 0.96, p
    assert s["email_events"]["resolved_correct"] >= 0.97, s["email_events"]


def test_cannot_link_keeps_spouses_apart():
    """Same address, last name, zip and shared inbox, different first names: two people."""
    recs = pd.DataFrame({"unique_id": ["a", "b", "c"], "first_name": ["katelyn", None, "christina"],
                         "birth_year": [None, None, None]})
    pairs = pd.DataFrame({"unique_id_l": ["a", "b"], "unique_id_r": ["b", "c"], "match_probability": [0.999, 0.998]})
    c = constrained_clusters(recs, pairs, 0.9).set_index("unique_id")["cluster_id"]
    assert c["a"] == c["b"] != c["c"], "the blank-name record must not chain two people together"


def test_every_current_record_maps_exactly_once(warehouse):
    _, wh, _ = warehouse
    with duckdb.connect(str(wh)) as con:
        for src, view, key in [("crm", "current_crm", "crm_id"), ("vendor", "current_vendor", "vendor_record_id"),
                               ("store_order", "current_store", "order_id"), ("email", "current_email", "event_id")]:
            n, mapped, dup = con.execute(f"""
                SELECT (SELECT count(*) FROM {view}),
                       (SELECT count(*) FROM {view} v JOIN identity_map m ON m.source = '{src}' AND m.record_id = v.{key}),
                       (SELECT count(*) - count(DISTINCT record_id) FROM identity_map WHERE source = '{src}')""").fetchone()
            assert n == mapped and dup == 0, src


def test_shared_inbox_events_are_left_unresolved(warehouse):
    _, wh, stats = warehouse
    with duckdb.connect(str(wh)) as con:
        bad = con.execute("""
            SELECT count(*) FROM identity_map m JOIN current_email e ON m.record_id = e.event_id
            WHERE m.source = 'email' AND m.person_key IS NOT NULL
              AND e.email IN (SELECT email FROM identity_records GROUP BY email HAVING count(DISTINCT person_key) > 1)""").fetchone()[0]
    assert bad == 0 and stats["email_events_shared_inbox"] > 0


def test_input_row_order_does_not_change_persons(warehouse):
    """Regression: cluster names once depended on row order, which DuckDB does not fix."""
    recs = pd.DataFrame({"unique_id": ["x2", "x1", "y1"], "first_name": ["ann", "ann", "bo"], "birth_year": [None] * 3})
    pairs = pd.DataFrame({"unique_id_l": ["x1"], "unique_id_r": ["x2"], "match_probability": [0.99]})
    a = constrained_clusters(recs, pairs, 0.9).set_index("unique_id").cluster_id.sort_index()
    b = constrained_clusters(recs.iloc[::-1], pairs, 0.9).set_index("unique_id").cluster_id.sort_index()
    assert a.equals(b) and a["x2"] == "x1"


def test_resolution_is_deterministic(warehouse, tmp_path):
    _, wh, _ = warehouse
    copy = tmp_path / "w2.duckdb"
    shutil.copy(wh, copy)
    with duckdb.connect(str(wh)) as a, duckdb.connect(str(copy)) as b:
        resolve(b)
        q = "SELECT source, record_id, person_key FROM identity_map ORDER BY ALL"
        assert a.execute(q).fetchall() == b.execute(q).fetchall()


def test_scoring_works_without_a_store(tmp_path):
    """Regression: scoring crashed when the optional store was never built."""
    run = tmp_path / "run"
    generate(load_reference(ROOT / "tests" / "fixtures" / "reference"), SynthConfig(seed=8, n_people=800), run)
    wh = tmp_path / "w.duckdb"
    ingest(run, wh, load_contracts(ROOT / "sources"))
    with duckdb.connect(str(wh)) as con:
        resolve(con)
        s = score_warehouse(con, run)
    assert s["pairwise"]["f1"] > 0.9


def test_store_orders_without_their_answer_key_explain_the_fix(warehouse, tmp_path):
    run, wh, _ = warehouse
    r = tmp_path / "run"
    shutil.copytree(run, r)
    (r / "store" / "record_links.parquet").unlink()
    with duckdb.connect(str(wh)) as con, pytest.raises(FileNotFoundError, match="plumbline.store build"):
        score_warehouse(con, r)


@pytest.mark.parametrize("a,b,typo", [
    ("john.smith@inbox.test", "jonn.smith@inbox.test", True),            # one letter changed
    ("john.smith@inbox.test", "jhn.smith@inbox.test", True),             # one letter dropped
    ("scott.williams@inbox.test", "scott_williams@inbox.test", False),   # namesakes: separator
    ("ryan.green36@inbox.test", "ryan.green32@inbox.test", False),       # namesakes: digit
    ("linda.evans@inbox.test", "linda.evans88@inbox.test", False),       # namesakes: two digits
    ("john.smith@inbox.test", "john.smith@inbox.tesx", False),           # another provider
])
def test_only_a_letter_typo_counts_as_a_mistyped_email(a, b, typo):
    """Regression: "within 2 edits" joined namesakes, whose addresses are built from their names,
    and precision fell from 97.4% to 93.5% as the world grew from 100,000 to 300,000 people."""
    import duckdb
    from plumbline.identity.match import EMAIL_TYPO_SQL
    with duckdb.connect() as con:
        got = con.execute(f"SELECT {EMAIL_TYPO_SQL} FROM (SELECT ? AS email_l, ? AS email_r)", [a, b]).fetchone()[0]
    assert got is typo


def test_the_match_model_uses_the_narrow_typo_rule_and_no_wider_one():
    from plumbline.identity.match import EMAIL_TYPO_SQL, settings
    email = next(c for c in settings().comparisons if getattr(c, "_output_column_name", None) == "email")
    kinds = [type(lv).__name__ for lv in email._comparison_levels]
    assert "LevenshteinLevel" not in kinds and "JaroWinklerLevel" not in kinds
    assert [lv.sql_condition for lv in email._comparison_levels if type(lv).__name__ == "CustomLevel"] == [EMAIL_TYPO_SQL]
