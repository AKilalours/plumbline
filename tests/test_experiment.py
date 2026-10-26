"""The campaign experiment: promises about assignment, attribution, the statistics, and what the
uplift model may learn from."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import pytest

from plumbline.experiments import design as D
from plumbline.experiments import readout as R
from plumbline.experiments.run import run_experiment
from plumbline.identity.pipeline import resolve
from plumbline.ingest.connectors.store_api import StoreClient, sync_orders
from plumbline.ingest.contracts import load_contracts
from plumbline.ingest.engine import ingest
from plumbline.ml.features import build_snapshots
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
    d = tmp_path_factory.mktemp("exp")
    run = d / "run"
    generate(load_reference(REF), SynthConfig(seed=17, n_people=6000), run)
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
    build_snapshots(wh, ALL_CUTOFFS)
    r = run_experiment(wh, run)
    return {"run": run, "wh": wh, "readout": r}


@pytest.fixture
def copy(world, tmp_path):
    """A private warehouse and run, for tests that tamper with them."""
    wh, run = tmp_path / "w.duckdb", tmp_path / "run"
    shutil.copy(world["wh"], wh)
    shutil.copytree(world["run"], run)
    return wh, run


# ----------------------------------------------------------------- assignment
def test_everyone_at_one_inbox_shares_an_arm_and_gets_one_email(world):
    with duckdb.connect(str(world["wh"]), read_only=True) as con:
        mixed = con.execute("SELECT count(*) FROM (SELECT email FROM exp.assignments GROUP BY 1 HAVING count(DISTINCT arm) > 1)").fetchone()[0]
        sends = D.send_list(con)
        shared = con.execute("SELECT count(*) FROM (SELECT email FROM exp.assignments GROUP BY 1 HAVING count(*) > 1)").fetchone()[0]
    assert mixed == 0
    assert sends.email.is_unique
    assert shared > 0, "the test world must contain shared inboxes, or this test proves nothing"


def test_assignment_is_reproducible_and_ignores_list_order():
    emails = [f"person{i}@inbox.test" for i in range(2000)]
    a = {e: D.arm(e) for e in emails}
    b = {e: D.arm(e) for e in reversed(emails)}
    assert a == b
    share = np.mean([v == "treatment" for v in a.values()])
    assert 0.45 < share < 0.55
    assert {D.arm(e, salt="another-campaign") for e in emails[:200]} == {"treatment", "holdout"}


def test_the_plan_is_written_before_the_readout(world):
    with duckdb.connect(str(world["wh"]), read_only=True) as con:
        plan_at = con.execute("SELECT written_at FROM exp.plan").fetchone()[0]
        read_at = con.execute("SELECT read_at FROM exp.readouts").fetchone()[0]
        plan = json.loads(con.execute("SELECT plan FROM exp.plan").fetchone()[0])
    assert plan_at <= read_at
    assert plan["unit"] == "inbox" and plan["detectable_effect_at_80pct_power"] > 0


# ----------------------------------------------------------------- checks before believing it
def test_a_broken_split_is_caught_and_the_readout_marked_untrustworthy(copy):
    wh, run = copy
    with duckdb.connect(str(wh)) as con:
        # a logging bug: a fifth of the holdout recorded as treatment
        con.execute("""UPDATE exp.assignments SET arm = 'treatment' WHERE arm = 'holdout'
                       AND email IN (SELECT email FROM exp.assignments WHERE arm = 'holdout' ORDER BY email LIMIT
                                     (SELECT count(*) / 5 FROM exp.assignments WHERE arm = 'holdout'))""")
        r = R.readout(con, run)
    assert not r["sample_ratio"]["ok"] and not r["trustworthy"]


def test_orders_with_another_known_address_count_and_mistyped_ones_do_not(copy):
    wh, run = copy
    with duckdb.connect(str(wh)) as con:
        email, work = con.execute("""
            SELECT a.email, lower(p.work_email) FROM exp.assignments a JOIN products.dim_person p USING (person_key)
            WHERE p.work_email IS NOT NULL
              AND lower(p.work_email) NOT IN (SELECT email FROM exp.assignments)
              AND a.email NOT IN (SELECT lower(customer_email) FROM exp.campaign_orders)
            ORDER BY a.email LIMIT 1""").fetchone()
    orders = pd.read_csv(run / "campaign" / "orders.csv")
    extra = pd.DataFrame([{"order_id": "KCTEST001", "customer_email": work, "first_name": "x", "last_name": "y",
                           "created_at": "2026-01-10 12:00:00", "total_price": 50.0, "currency": "USD"},
                          {"order_id": "KCTEST002", "customer_email": "zz" + email, "first_name": "x", "last_name": "y",
                           "created_at": "2026-01-10 12:00:00", "total_price": 50.0, "currency": "USD"}])
    pd.concat([orders, extra]).to_csv(run / "campaign" / "orders.csv", index=False)
    with duckdb.connect(str(wh)) as con:
        R.load_orders(con, run)
        df, att = R.outcomes(con)
    assert df.set_index("email").loc[email, "bought"] == 1          # the work-address order found its inbox
    assert att["orders_in_window"] - att["attributed_to_an_inbox"] >= 1   # the mistyped one did not


def test_orders_after_the_window_do_not_count(copy):
    """Late orders come from inboxes that did not buy in the window: if they counted, the number of
    buyers would rise. (A first version used the buyers' own late orders and could not fail.)"""
    wh, run = copy
    orders = pd.read_csv(run / "campaign" / "orders.csv")
    with duckdb.connect(str(wh)) as con:
        R.load_orders(con, run)
        df = R.outcomes(con)[0]
    quiet = df.loc[df.bought == 0, "email"].head(25)
    late = pd.DataFrame({"order_id": [f"KCLATE{i:03d}" for i in range(len(quiet))], "customer_email": quiet.to_numpy(),
                         "first_name": "x", "last_name": "y", "created_at": "2026-03-01 09:00:00",
                         "total_price": 40.0, "currency": "USD"})
    early = late.assign(order_id=late.order_id.str.replace("LATE", "EARLY"), created_at="2025-12-20 09:00:00")
    pd.concat([orders, late, early]).to_csv(run / "campaign" / "orders.csv", index=False)
    with duckdb.connect(str(wh)) as con:
        R.load_orders(con, run)
        assert R.outcomes(con)[0].bought.sum() == df.bought.sum()


# ----------------------------------------------------------------- statistics
def test_cuped_keeps_the_effect_and_narrows_the_interval_by_the_squared_correlation():
    """Variance falls by corr(history, outcome) squared. For spend, which history predicts, that is a
    lot; for a rare yes/no purchase it is about nothing, which is what the real readout shows."""
    rng = np.random.default_rng(0)
    n = 40_000
    x = rng.gamma(2.0, 20.0, n)
    t = rng.integers(0, 2, n)
    y = 0.8 * x + rng.normal(0, 20, n) + 3.0 * t
    raw, adj = R.diff(y, t), R.cuped(y, x, t)
    rho2 = np.corrcoef(x, y)[0, 1] ** 2
    assert abs(adj["effect"] - raw["effect"]) < raw["se"]
    assert adj["ci95"][0] < 3.0 < adj["ci95"][1]
    assert (adj["se"] / raw["se"]) ** 2 == pytest.approx(1 - rho2, abs=0.02)
    b = (rng.random(n) < 0.03 + 0.002 * (x > 60) + 0.01 * t).astype(float)    # rare purchase
    assert R.cuped(b, x, t)["se"] / R.diff(b, t)["se"] > 0.99


def test_holm_adjustment():
    assert R.holm([0.01, 0.04, 0.03]) == pytest.approx([0.03, 0.06, 0.06])
    assert R.holm([0.5]) == [0.5]


def test_qini_rewards_putting_responders_first():
    rng = np.random.default_rng(1)
    n = 20_000
    t = rng.integers(0, 2, n)
    tau = rng.choice([0.0, 0.2], n)
    y = (rng.random(n) < 0.05 + tau * t).astype(float)
    good, bad = R.qini_auc(tau, y, t), R.qini_auc(-tau, y, t)
    rand = R.qini_auc(rng.random(n), y, t)
    assert good > abs(rand) * 3 and bad < 0


def test_the_answer_key_lies_inside_the_corrected_interval(world):
    ak = world["readout"]["answer_key"]
    assert ak["inside_corrected_ci"], world["readout"]["corrected_for_unattributed_orders"]


def test_the_correction_is_the_raw_effect_over_the_attributed_share(world):
    r = world["readout"]
    share = r["attribution"]["attributed_share"]
    assert r["corrected_for_unattributed_orders"]["effect"] == pytest.approx(r["bought_within_30_days"]["effect"] / share, abs=1e-4)


# ----------------------------------------------------------------- the uplift model
def test_uplift_features_are_fixed_before_the_send(copy):
    """Rewriting what happened after the send must not change a single feature value."""
    wh, run = copy
    with duckdb.connect(str(wh)) as con:
        R.load_orders(con, run)
        before = R._X(R.outcomes(con)[0])
    pd.read_csv(run / "campaign" / "orders.csv").head(0).to_csv(run / "campaign" / "orders.csv", index=False)
    with duckdb.connect(str(wh)) as con:
        R.load_orders(con, run)
        after = R._X(R.outcomes(con)[0])
    pd.testing.assert_frame_equal(before, after)
    assert not {"bought", "orders_after", "revenue_after", "true_tau", "t", "arm"} & set(R.FEATURES)


def test_the_uplift_model_is_judged_on_inboxes_it_never_saw(world):
    r = world["readout"]["uplift"]
    assert set(r["validation_qini"]) == {"logistic", "gradient_boosting"}
    assert r["chosen"] == max(r["validation_qini"], key=r["validation_qini"].get)


def test_a_changed_audience_gets_new_responses(copy):
    wh, run = copy
    man_path = run / "campaign" / "campaign_manifest.json"
    before = json.loads(man_path.read_text())
    with duckdb.connect(str(wh)) as con:   # some people unsubscribe: the audience, and so the list, changes
        con.execute("""UPDATE products.mart_activation_audience SET email_ok = false
                       WHERE person_key IN (SELECT person_key FROM products.mart_activation_audience WHERE email_ok
                                            ORDER BY person_key LIMIT 50)""")
    run_experiment(wh, run)
    after = json.loads(man_path.read_text())
    assert after["send_list_sha"] != before["send_list_sha"]
    assert after["inboxes"] < before["inboxes"]


def test_an_empty_orders_file_loads_with_the_right_types(copy):
    """Regression: on pandas 2 an empty file gave an untyped email column that DuckDB read as INTEGER,
    and the readout failed comparing it with addresses. Only the Python 3.10 environment showed it."""
    wh, run = copy
    pd.read_csv(run / "campaign" / "orders.csv").head(0).to_csv(run / "campaign" / "orders.csv", index=False)
    with duckdb.connect(str(wh)) as con:
        R.load_orders(con, run)
        types = dict(con.execute("SELECT column_name, data_type FROM information_schema.columns "
                                 "WHERE table_schema = 'exp' AND table_name = 'campaign_orders'").fetchall())
        df, att = R.outcomes(con)
    assert types["customer_email"] == "VARCHAR" and types["created_ts"].startswith("TIMESTAMP")
    assert df.bought.sum() == 0 and att["orders_in_window"] == 0
