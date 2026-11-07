"""The interactive visuals: each draws in a real browser with real numbers, without a script error or a NaN."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from plumbline.app import visual_data as VD
from plumbline.app import visuals as V

ROOT = Path(__file__).resolve().parents[1]
RES = ROOT / "docs" / "results"


def _data() -> dict:
    sv = json.loads((RES / "source_value.json").read_text())
    plan = json.loads((RES / "scale_plan.json").read_text())
    rg = json.loads((RES / "release_gate.json").read_text())
    runs = json.loads((RES / "scale.json").read_text())["runs"]
    od = json.loads((RES / "onboarding_time.json").read_text())
    flow = {"name": "x", "unit": "rows", "hint": "", "left": [{"key": "a", "label": "A", "value": 5, "color": "#000"}],
            "right": [{"key": "b", "label": "B", "value": 5, "color": "#111"}], "links": [{"from": "a", "to": "b", "value": 5}]}
    from plumbline.ingest import open_data_daily as OD
    runs_ = [{"run_at": "2026-11-07T11:23:00Z", "ok": True, "rows": 2155, "pages": 3, "retries": 2, "retries_by_cause": {"503": 2},
              "seconds": 14.2, "rows_loaded": 2155, "rows_quarantined": 0, "hours_behind_source": 16.6, "drift": [], "bulk_rewrite": None},
             {"run_at": "2026-11-08T11:23:00Z", "ok": False, "error": "StoreAPIError: gave up after 8 retries (last: 503)", "seconds": 90.0}]
    pair = {"a": {"source": "crm", "First name": "ann"}, "b": {"source": "vendor", "First name": "ann"}, "agree": {"First name": "agree"},
            "p": 0.7, "truth": True, "matcher": False, "kind": "k", "why": "w"}
    return {"tiles": {"items": [{"label": "x", "value": 3, "fmt": "int"}]}, "flow": {"views": [flow, {**flow, "name": "y"}]},
            "sizes": VD.sizes_band(runs), "worth": VD.worth(sv), "scale": VD.scale(plan, {}),
            "game": {"pairs": [pair, pair], "fields": ["First name"]},
            "gate": VD.gate(rg, {"other_algorithm": "a", "no_property_data": "b", "balanced_classes": "c"}, lambda g: "d"),
            "onboard": {"run": od},
            "threshold": {"edges": [-4, -2, 0, 2, 3.17, 6], "same": [1, 2, 5, 9, 40], "diff": [9, 7, 6, 4, 2], "prod": 3.17, "prod_p": 0.9,
                          "pairs": 85, "same_total": 57, "diff_total": 28, "hint": "h",
                          "rules": {"score_says_join_two_people": 2, "joined_two_people": 1, "same_person_kept_apart": 3, "same_person_pairs": 57}},
            "funnel": {"steps": [{"label": "Everyone", "value": 100, "tone": "info"}, {"label": "Match", "value": 60, "tone": "info", "note": "40 do not"},
                                 {"label": "minus: unsubscribed", "value": 50, "tone": "fail", "note": "10 kept out"},
                                 {"label": "Reachable by email", "value": 50, "tone": "pass"}]},
            "waterfall": {"walk": json.loads((RES / "reason_codes.json").read_text())["walk"], "base_rate": 0.0218, "hint": "h"},
            "timeline": {"events": [{"at": "2026-11-06 10:00:00", "action": "sync: ran", "kind": "sync", "result": "{}"},
                                    {"at": "2026-11-06 10:05:00", "action": "audience: saved", "kind": "audience", "result": "{\"segment\": 3}"}]},
            "trace": {"events": [{"phase": "inject", "label": "Merged two people", "detail": "PK1 into PK2", "seconds": 1.7},
                                 {"phase": "detect", "label": "Checked identity assertions", "detail": "1 failing", "seconds": 0.8}]},
            "history": {"runs": runs_, "summary": OD.summary(runs_), "title": "t"},
            "city": {"totals": {"rows_in": {"crm": 10, "vendor": 12, "privacy": 2}, "loaded": 20, "quarantined": 1, "suppressed": 1,
                                "already": 0, "records": 15, "people": 9, "households": 6, "audience": 8},
                     "reasons": [{"source": "crm", "reason": "bad_zip", "rows": 1}],
                     "sources": {"crm": {"label": "CRM", "color": "#2941B9"}, "vendor": {"label": "Data vendor", "color": "#E5B74D"},
                                 "privacy": {"label": "Privacy requests", "color": "#9A9A9A"}},
                     "people": [{"name": "Ann Lee", "mixed": i == 0, "records": [{"source": "crm", "name": "ann lee", "email": "a@x.test",
                                 "address": "1 MAIN ST 11211"}, {"source": "vendor", "name": "ann lee", "email": "", "address": "1 MAIN ST"}]}
                                for i in range(9)]}}


def test_every_kind_has_data_here():
    assert set(_data()) == set(V.KINDS)


def test_data_cannot_end_its_script_tag():
    html = V.page("tiles", {"items": [{"label": "</script><script>alert(1)</script>", "value": 1}]})
    assert "</script><script>alert" not in html


def test_an_unknown_visual_is_refused():
    with pytest.raises(ValueError):
        V.page("pie", {})


@pytest.mark.parametrize("width", [1100, 375])
def test_every_visual_draws_in_a_browser_without_an_error_or_a_nan(width):
    pw = pytest.importorskip("playwright.sync_api")
    with pw.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as e:                    # a machine without the browser installed
            pytest.skip(f"no browser: {e}")
        pg = b.new_page(viewport={"width": width, "height": 800})
        errors = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        for kind, data in _data().items():
            pg.set_content(V.page(kind, data))
            pg.wait_for_timeout(1100)            # count-ups finish
            text = pg.inner_text("#app")
            assert text.strip() and "NaN" not in text and "undefined" not in text, (kind, text[:200])
            assert pg.evaluate("document.documentElement.scrollWidth") <= width + 1, kind      # nothing wider than the screen
        b.close()
    assert not errors, errors


def test_the_worth_slider_recomputes_in_the_browser():
    pw = pytest.importorskip("playwright.sync_api")
    with pw.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as e:
            pytest.skip(f"no browser: {e}")
        pg = b.new_page()
        pg.set_content(V.page("worth", _data()["worth"]))
        pg.wait_for_timeout(1000)
        first = pg.locator(".val").first.inner_text()
        pg.eval_on_selector("#val", "e => { e.value = 4000; e.dispatchEvent(new Event('input')); }")
        pg.wait_for_timeout(1100)
        assert first == "$2.045" and pg.locator(".val").first.inner_text() == "$4.089"   # 31 / 30,323 x $4,000, not double the rounded $2.045
        b.close()


def test_every_saved_waterfall_adds_up_to_its_own_score():
    """The waterfall's steps start at an average person and must end at the model's score, or it is a picture of nothing."""
    import math
    r = json.loads((RES / "reason_codes.json").read_text())
    assert len(r["walk"]) == 24 and sum(p["top_decile"] for p in r["walk"]) == 12
    for p in r["walk"]:
        z = p["start"] + sum(v for _, v in p["steps"])
        assert abs(1 / (1 + math.exp(-z)) - p["score"]) < 1e-4, p["person_key"]


def test_the_funnel_steps_subtract_exactly_the_people_each_rule_names():
    import pandas as pd
    a = {"population": 100, "segment": 60, "reachable": 45,
         "excluded": pd.DataFrame({"reason": ["no email on file"] * 10 + ["unsubscribed or asked to be deleted"] * 5})}
    st = VD.funnel(a, "email")["steps"]
    assert [x["value"] for x in st] == [100, 60, 50, 45, 45] and st[-1]["label"] == "Reachable by email"
