"""Module C: the home-buyer model. Most tests guard against the ways models quietly lie:
seeing the future, being scored on people unlike the ones they learned from, and drift checks
that fire on nothing."""
from __future__ import annotations

from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import pytest
import yaml

from plumbline.identity.pipeline import resolve
from plumbline.ingest.connectors.store_api import StoreClient, sync_orders
from plumbline.ingest.contracts import load_contracts
from plumbline.ingest.engine import ingest
from plumbline.ml.guardrails import GuardrailFailed, check_batch, psi, publish
from plumbline.ml.pipeline import train_and_score
from plumbline.ml.reference import load_reference_tables
from plumbline.ml.train import (ALL_CUTOFFS, FINAL_TRAIN, SELECT_TRAIN, SELECT_VAL, TEST, LeakError,
                                check_no_label_leak, check_not_above_ceiling)
from plumbline.privacy.person_deletion import expand_deletions
from plumbline.products.build import build
from plumbline.store.db import build_store
from plumbline.store.server import MockStore
from plumbline.synth.config import SynthConfig
from plumbline.synth.generate import generate
from plumbline.synth.reference import load_reference

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = load_contracts(ROOT / "sources")
REF = ROOT / "tests" / "fixtures" / "reference"


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    d = tmp_path_factory.mktemp("ml")
    run = d / "run"
    generate(load_reference(REF), SynthConfig(seed=13, n_people=6000), run)
    build_store(run)
    with MockStore.from_run(run, "t") as s:
        sync_orders(StoreClient(s.url, "t", sleep=lambda x: None), run / "sources" / "store")
    wh = d / "w.duckdb"
    ingest(run, wh, CONTRACTS)
    with duckdb.connect(str(wh)) as con:
        resolve(con)
        expand_deletions(con, CONTRACTS)
        load_reference_tables(con, REF)
    build(wh)
    out = train_and_score(wh, run, d / "models")
    return run, wh, out


# ------------------------------------------------------------- guards against seeing the future
def test_label_windows_must_close_before_evaluation():
    check_no_label_leak(SELECT_TRAIN, SELECT_VAL)
    check_no_label_leak(FINAL_TRAIN, TEST)
    # regression: a 182-day horizon let the last training snapshot see one day past the test date
    with pytest.raises(LeakError, match="2025-07-01"):
        check_no_label_leak(FINAL_TRAIN, TEST, horizon=182)


def test_feature_windows_fit_inside_every_snapshots_history():
    """A 365-day window at a cutoff 90 days after the data starts is a 90-day window in disguise."""
    window = yaml.safe_load((ROOT / "dbt" / "dbt_project.yml").read_text())["vars"]["feature_window_days"]
    first = min(ALL_CUTOFFS)
    assert (pd.Timestamp(first) - pd.Timestamp(SynthConfig().window_start)).days >= window


def test_too_few_outcomes_is_refused_not_reported():
    from plumbline.ml.train import NotEnoughOutcomes, check_enough_outcomes
    with pytest.raises(NotEnoughOutcomes, match="test"):
        check_enough_outcomes({"test": pd.DataFrame({"label": [1, 0, 0, 1]})})


def test_a_backtest_above_the_ceiling_is_rejected():
    with pytest.raises(LeakError, match="beats the best possible"):
        check_not_above_ceiling({"auc": 0.84}, {"auc": 0.81})
    check_not_above_ceiling({"auc": 0.66}, {"auc": 0.81})


def test_snapshots_use_nothing_from_after_their_cutoff(world):
    run, wh, _ = world
    with duckdb.connect(str(wh), read_only=True) as con:
        s = con.execute("SELECT * FROM ml.feature_snapshots WHERE as_of = DATE '2024-06-30'").df()
        first_vendor = con.execute("SELECT min(delivered_at) FROM ingest_ledger WHERE source = 'vendor'").fetchone()[0]
        # independent recount of one feature straight from the orders
        o = con.execute("""SELECT m.person_key, count(*) n FROM current_store o
                           JOIN identity_map m ON m.source = 'store_order' AND m.record_id = o.order_id
                           WHERE o.created_at >= TIMESTAMP '2024-07-01' - INTERVAL 90 DAY AND o.created_at < TIMESTAMP '2024-07-01'
                           GROUP BY 1""").df().set_index("person_key").n
    assert str(first_vendor) > "2024-07-01", "fixture assumption: the vendor feed starts later"
    assert s.vendor_homeowner.isna().all() and s.income_band.isna().all(), "vendor data delivered later leaked back"
    got = s.set_index("person_key").orders_window
    assert (got.reindex(o.index).fillna(0) == o).all() and (got.drop(o.index, errors="ignore") == 0).all()


# ------------------------------------------------------------- guards on publishing scores
def test_drift_index_is_zero_for_the_same_data_and_large_for_a_shift():
    r = np.random.default_rng(0)
    a = pd.Series(r.normal(size=5000), index=range(100, 5100))
    assert psi(a, pd.Series(r.normal(size=4000))) < 0.02
    assert psi(a, pd.Series(r.normal(size=4000) + 1)) > 0.25


def test_drift_index_handles_flags_with_different_row_labels():
    """Regression: mixing two pandas indexes reported a drift of 18 on flags that had not moved."""
    r = np.random.default_rng(1)
    f = pd.Series((r.random(5000) < 0.3).astype(float), index=range(7, 5007))
    g = pd.Series((r.random(3000) < 0.3).astype(float))
    assert psi(f, g) < 0.02 and psi(f, pd.Series((r.random(3000) < 0.7).astype(float))) > 0.25


def test_failed_checks_publish_nothing():
    con = duckdb.connect()
    X = pd.DataFrame({"a": np.arange(500.0)})
    checks = check_batch(X, X + 10_000, np.full(500, 0.02), 0.02)   # inputs moved far away
    assert not all(c["passed"] for c in checks)
    with pytest.raises(GuardrailFailed, match="input_drift_psi"):
        publish(con, pd.DataFrame({"person_key": ["x"], "as_of": ["2025-12-31"], "score": [0.1], "decile": [1],
                                   "model_version": ["v"]}), "v", "2025-12-31", checks, pd.DataFrame({"person_key": []}))
    assert con.execute("SELECT count(*) FROM ml.home_buyer_scores").fetchone()[0] == 0
    assert con.execute("SELECT published FROM ml.model_runs").fetchone()[0] is False


def test_people_the_model_never_learned_about_are_not_scored(world):
    _, wh, out = world
    with duckdb.connect(str(wh), read_only=True) as con:
        scored = set(con.execute("SELECT person_key FROM ml.home_buyer_scores").df().person_key)
        skipped = con.execute("SELECT * FROM ml.home_buyer_not_scored").df()
        first_party = set(con.execute("""SELECT person_key FROM ml.feature_snapshots
                                         WHERE as_of = DATE '2025-12-31' AND first_party""").df().person_key)
    assert out["published"] and scored == first_party and not scored & set(skipped.person_key)
    assert len(skipped) > 0 and skipped.reason.str.contains("third-party").all()


def test_deleted_people_are_never_scored(world):
    _, wh, _ = world
    with duckdb.connect(str(wh), read_only=True) as con:
        orphan = con.execute("""SELECT count(*) FROM ml.home_buyer_scores s
                                WHERE NOT EXISTS (SELECT 1 FROM identity_map m WHERE m.person_key = s.person_key)""").fetchone()[0]
    assert orphan == 0


def test_the_model_stays_below_the_ceiling(world):
    """Accuracy is claimed at full scale (README). The test world has 4 ZIP codes and 80 buildings,
    so the enrichment signal cannot exist here; only the ceiling holds at any size."""
    out = world[2]
    assert out["test_auc"] <= out["ceiling_auc"] + 0.01


def test_a_failed_snapshot_leaves_the_live_products_untouched(world, tmp_path, monkeypatch):
    """Regression: snapshots once rebuilt the live golden person for each past date. A failure on
    the third one left it at a quarter of its people, frozen in mid-2024."""
    import shutil
    import plumbline.ml.features as F
    wh = tmp_path / "w.duckdb"
    shutil.copy(world[1], wh)
    q = "SELECT count(*), max(last_order_at), sum(orders) FROM products.dim_person"
    with duckdb.connect(str(wh), read_only=True) as con:
        before = con.execute(q).fetchone()
    real, calls = F.build, {"n": 0}

    def flaky(w, as_of=None, select=None):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("simulated failure")
        return real(w, as_of=as_of, select=select)
    monkeypatch.setattr(F, "build", flaky)
    with pytest.raises(RuntimeError, match="simulated"):
        F.build_snapshots(wh, SELECT_TRAIN)
    with duckdb.connect(str(wh), read_only=True) as con:
        assert con.execute(q).fetchone() == before


def test_gradient_boosting_is_a_seed_ensemble_that_repeats_exactly():
    """One gradient-boosting fit moved a reported AUC by 0.01 between machines on identical data.
    The ensemble averages its members, and two fits on the same data give the same scores."""
    from plumbline.ml.train import SeedEnsemble
    rng = np.random.default_rng(0)
    X = pd.DataFrame(rng.normal(size=(3000, 4)), columns=list("abcd"))
    y = (X.a + 0.5 * X.b + rng.normal(size=3000) > 1.2).astype(int)
    e1 = SeedEnsemble(seeds=(1, 2, 3), max_iter=50).fit(X, y)
    e2 = SeedEnsemble(seeds=(1, 2, 3), max_iter=50).fit(X, y)
    p = e1.predict_proba(X)[:, 1]
    assert np.array_equal(p, e2.predict_proba(X)[:, 1])
    assert np.allclose(p, np.mean([m.predict_proba(X)[:, 1] for m in e1.members_], axis=0))
    s = e1.member_spread(X, y)
    assert s["member_auc_min"] <= s["member_auc_mean"] <= s["member_auc_max"] and s["seeds"] == [1, 2, 3]


def test_every_gradient_boosting_number_reports_its_seed_spread(world):
    """A gradient-boosting score is a range, not a point: the card must say how wide."""
    _, wh, _ = world
    with duckdb.connect(str(wh), read_only=True) as con:
        card = __import__("json").loads(con.execute("SELECT card FROM ml.training_summary").fetchone()[0])
    assert "seed_spread" in card["selection"]["results"]["gradient_boosting"]
    assert "seed_spread" in card["test"]["ablations"]["with_an_on_time_vendor_feed_what_if"]
    assert card["selection"]["results"]["logistic"].get("seed_spread") is None   # deterministic, no spread to report
