"""Income estimates and the home-seller assessment: promises about who gets a number, and when a
model is not allowed to publish one."""
from __future__ import annotations

from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import pytest

from plumbline.identity.pipeline import resolve
from plumbline.ingest.connectors.store_api import StoreClient, sync_orders
from plumbline.ingest.contracts import load_contracts
from plumbline.ingest.engine import ingest
from plumbline.ml import income, seller
from plumbline.ml.features import build_snapshots, matrix
from plumbline.ml.labels import person_owner
from plumbline.ml.reference import load_reference_tables
from plumbline.ml.train import ALL_CUTOFFS
from plumbline.privacy.person_deletion import expand_deletions
from plumbline.products.build import build
from plumbline.store.db import build_store
from plumbline.store.server import MockStore
from plumbline.synth.config import SynthConfig
from plumbline.synth.generate import generate
from plumbline.synth.reference import load_reference

ROOT = Path(__file__).resolve().parents[1]
REF = ROOT / "tests" / "fixtures" / "reference"
CONTRACTS = load_contracts(ROOT / "sources")


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    d = tmp_path_factory.mktemp("more")
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
    snaps = build_snapshots(wh, ALL_CUTOFFS)
    with duckdb.connect(str(wh), read_only=True) as con:
        owner = person_owner(con, run)
    return {"run": run, "wh": wh, "snaps": snaps, "owner": owner}


# ----------------------------------------------------------------- income
def test_income_is_estimated_only_for_people_the_vendor_does_not_cover(world):
    out = income.run(world["wh"], world["run"])
    income.publish(world["wh"], out)
    with duckdb.connect(str(world["wh"]), read_only=True) as con:
        est = set(con.execute("SELECT person_key FROM ml.income_estimates").df().person_key)
        snap = con.execute("SELECT person_key, income_band, first_party FROM ml.feature_snapshots WHERE as_of = DATE '2025-12-31'").df()
    covered = set(snap[snap.income_band.notna()].person_key)
    assert est and not est & covered, "a person with a vendor band must not get an estimate in its place"
    assert est <= set(snap[snap.first_party.fillna(False).astype(bool)].person_key)


def test_income_features_exclude_what_the_target_people_lack(world):
    snap = world["snaps"]
    X = income._features(snap[pd.to_datetime(snap.as_of) == pd.Timestamp("2025-12-31")], [])
    assert not set(income.VENDOR_COLUMNS) & set(X.columns)


def test_income_train_and_test_never_share_a_household(world, monkeypatch):
    """People in one household share one income; splitting by person would leak it."""
    seen = {}
    real = income.GroupShuffleSplit.split

    def spy(self, X, y=None, groups=None):
        for tr, te in real(self, X, y, groups):
            seen.setdefault("pairs", []).append((set(np.asarray(groups)[tr]), set(np.asarray(groups)[te])))
            yield tr, te
    monkeypatch.setattr(income.GroupShuffleSplit, "split", spy)
    income.run(world["wh"], world["run"])
    assert seen["pairs"] and all(not (a & b) for a, b in seen["pairs"])


def test_failed_income_gates_publish_nothing(world, monkeypatch, tmp_path):
    import shutil
    wh = tmp_path / "w.duckdb"
    shutil.copy(world["wh"], wh)
    with duckdb.connect(str(wh)) as con:
        con.execute("DROP TABLE IF EXISTS ml.income_estimates")
    out = income.run(wh, world["run"])
    monkeypatch.setattr(income, "check", lambda o: [{"name": "forced", "value": 0, "passed": False, "rule": "test"}])
    with pytest.raises(income.IncomeGateFailed):
        income.publish(wh, out)
    with duckdb.connect(str(wh), read_only=True) as con:
        assert not con.execute("SELECT count(*) FROM information_schema.tables WHERE table_name = 'income_estimates'").fetchone()[0]
        assert con.execute("SELECT count(*) FROM ml.model_runs_income WHERE NOT published").fetchone()[0] == 1


def test_a_constant_prediction_has_no_rank_correlation():
    assert income.band_metrics([1, 2, 3, 4], [2, 2, 2, 2])["spearman"] is None


# ----------------------------------------------------------------- seller
def test_only_homeowners_can_be_sellers(world):
    lab = seller.attach(world["snaps"], world["owner"], world["run"])
    assert (lab[lab.label == 1].owns_at_cutoff).all()
    assert lab.label.sum() > 0


def test_the_seller_ceiling_is_the_generating_rule(world):
    """No honest model beats the rule that generated the sales; the ceiling uses it directly."""
    lab = seller.attach(world["snaps"], world["owner"], world["run"])
    t = lab[(pd.to_datetime(lab.as_of) == pd.Timestamp("2025-06-30")) & lab.owns_at_cutoff & ~lab.moved_before]
    o = seller.oracle(t, world["run"])
    assert ((o >= 0) & (o <= 1)).all() and o.std() > 0


WEAK = {"algorithm": "m", "selection_on_validation": {"m": {"auc": 0.47}}, "test": {"auc": 0.54, "lift_top_decile": 0.78},
        "ceiling_from_answer_key": {"auc": 0.56}}


def test_a_seller_model_that_cannot_beat_chance_is_not_published(world, tmp_path, monkeypatch):
    """The full-size world's result (validation AUC 0.47, lift 0.78, ceiling 0.56) must publish nothing,
    record why, and remove scores left over from an earlier run so they cannot look current."""
    import shutil
    wh = tmp_path / "w.duckdb"
    shutil.copy(world["wh"], wh)
    with duckdb.connect(str(wh)) as con:
        con.execute("CREATE SCHEMA IF NOT EXISTS ml")
        con.execute("CREATE OR REPLACE TABLE ml.home_seller_scores AS SELECT 'old' AS person_key")
    monkeypatch.setattr(seller, "run", lambda *a, **k: dict(WEAK))
    r = seller.assess_and_record(wh, world["snaps"], world["owner"], world["run"], min_test_sales=0)
    assert r["published"] is False and set(r["failed_gates"]) == {"beats_chance_on_validation", "top_decile_lift", "ceiling_leaves_room"}
    with duckdb.connect(str(wh), read_only=True) as con:
        assert con.execute("SELECT count(*) FROM information_schema.tables WHERE table_name = 'home_seller_scores'").fetchone()[0] == 0
        assert con.execute("SELECT count(*) FROM ml.model_runs_seller WHERE NOT published").fetchone()[0] == 1


def test_seller_gates_pass_a_model_with_real_signal():
    good = {"algorithm": "m", "selection_on_validation": {"m": {"auc": 0.7}}, "test": {"lift_top_decile": 2.0},
            "ceiling_from_answer_key": {"auc": 0.8}}
    assert all(g["passed"] for g in seller.gates(good))
    assert not any(g["passed"] for g in seller.gates(WEAK))


def test_seller_labels_do_not_depend_on_the_time_resolution(tmp_path):
    """Regression: on pandas 2 the truth dates came back as microseconds and the cutoffs as
    nanoseconds, and merge_asof refused to join them. Feed both resolutions on purpose."""
    import pandas as pd
    from plumbline.ml import seller
    (tmp_path / "truth").mkdir()
    hist = pd.DataFrame({"person_id": ["a", "a", "b"],
                         "valid_from": pd.to_datetime(["2023-01-01", "2025-05-01", "2023-01-01"]).astype("datetime64[us]"),
                         "owner": [True, False, True]})
    hist.to_parquet(tmp_path / "truth" / "address_history.parquet")
    snaps = pd.DataFrame({"person_key": ["PKa", "PKb"], "as_of": ["2025-03-31", "2025-03-31"]})
    out = seller.attach(snaps, {"PKa": "a", "PKb": "b"}, tmp_path)
    assert out.owns_at_cutoff.tolist() == [True, True]
    assert out.label.tolist() == [True, False]          # a sells 31 days later; b never moves
