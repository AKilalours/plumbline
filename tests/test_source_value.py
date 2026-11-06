"""Should we buy this data, and how the pipeline grows: the saved studies, checked."""
from __future__ import annotations

import json
from pathlib import Path

from plumbline.ml import scale_plan as SP
from plumbline.ml import source_value as SV
from plumbline.ml.features import BOOLEAN, CATEGORICAL, NUMERIC

ROOT = Path(__file__).resolve().parents[1]
RES = ROOT / "docs" / "results"


def test_every_model_input_belongs_to_exactly_one_source_or_is_shared():
    """A new feature with no source would make "remove the vendor" quietly keep vendor data."""
    shared = {"has_unit", "moves_window"}            # from every source's addresses, not one source's
    owned = {c: g for g, v in SV.GROUPS.items() for c in v["cols"]}
    for c in NUMERIC + BOOLEAN:
        assert (c in owned) != (c in shared), c
    assert CATEGORICAL == ["bldg_family"] and SV.GROUPS["property"]["prefix"] == "bldg_"


def test_the_source_study_and_the_release_gate_measure_the_same_thing_the_same_way():
    """Two numbers for "without property data" on one page would contradict each other."""
    sv = json.loads((RES / "source_value.json").read_text())["model"]
    rg = json.loads((RES / "release_gate.json").read_text())
    assert sv["auc"] == rg["auc"]["champion"] and sv["without"]["property"]["auc"] == rg["auc"]["no_property_data"]
    assert sv["test_people"] == rg["test_rows"] and sv["test_buyers"] == rg["test_buyers"]


def test_a_loss_is_only_claimed_when_its_interval_excludes_zero():
    m = json.loads((RES / "source_value.json").read_text())["model"]["without"]
    lo, hi = m["property"]["ci95"]
    assert hi < 0                                    # property data measurably helps
    lo, hi = m["vendor"]["ci95"]
    assert lo <= 0 <= hi                             # the vendor's model inputs do not, measurably


def test_the_vendor_homeowner_flag_is_checked_against_the_answer_key():
    v = json.loads((RES / "source_value.json").read_text())["vendor_vs_answer_key"]["homeowner"]
    assert 0 < v["said_owner_but_rents"] < v["said_owner"] <= v["covered"]


def test_breakeven_price_is_buyers_gained_times_value_per_record():
    assert SV.breakeven_price(31, 2_000) == 6.2      # 31 more buyers per 10,000 at $2,000 each: $6.20 a record
    assert SV.breakeven_price(-3, 2_000) == 0.0


def test_the_scale_plan_uses_one_machine_and_ranks_by_marginal_cost_not_startup_cost():
    runs = json.loads((RES / "scale.json").read_text())["runs"]
    g = SP.growth(runs)
    assert g["people"] == [20_000, 100_000, 300_000]                  # the 1M laptop run is on another clock
    ident = g["stages"]["identity"]
    assert ident["marginal_per_100k"][1] > ident["marginal_per_100k"][0]   # identity gets dearer per person
    p = SP.project(g, 300_000)
    assert all(abs(p[st] - g["stages"][st]["seconds"][300_000]) < 1e-6 for st in p)   # passes through the last run
    saved = json.loads((RES / "scale_plan.json").read_text())
    assert saved["order_to_move"][0]["minutes_at_1tb_one_machine"] >= saved["order_to_move"][-1]["minutes_at_1tb_one_machine"]
