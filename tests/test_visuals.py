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
