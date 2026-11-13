"""The interactive visuals: each draws in a real browser with real numbers, without a script error or a NaN."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from plumbline.app import visual_data as VD
from plumbline.app import visuals as V

ROOT = Path(__file__).resolve().parents[1]
RES = ROOT / "docs" / "results"


_OPS = {}


def _ops() -> dict:
    """The operations visuals, from real kit runs and run lines shaped like the history branch's."""
    if not _OPS:
        from plumbline.app import kit_demo, ops_visuals as OV
        kd = kit_demo.playground()
        kd["code"], kd["code_lines"] = OV.kit_code()
        runs = [{"run_at": "2026-11-07T07:17:28Z", "ok": False}, {"run_at": "2026-11-07T07:59:40Z", "ok": True, "retries": 3},
                {"run_at": "2026-11-12T18:45:28Z", "ok": True, "trigger": "workflow_dispatch",
                 "lake": {"snapshot_id": 1, "rows": 68811, "appended": 0, "seeded": 68811, "snapshots": 1}},
                {"run_at": "2026-11-13T02:10:00Z", "ok": True, "lake": {"snapshot_id": 2, "rows": 68830, "appended": 19, "seeded": 0, "snapshots": 2}}]
        entries = [{"feed": "dob_now_permits", "started": "2026-11-07T07:17:28Z", "recovered": "2026-11-07T07:59:40Z", "did": "pinned duckdb",
                    "postmortem": "docs/postmortems/2026-11-07-motherduck-duckdb.md"}]
        from plumbline.ingest.cost import job_record
        _OPS.update({"kit": kd, "polite": kit_demo.scrape(),
                     "incidents": OV.incidents({"NYC": ("dob_now_permits", runs)}, entries),
                     "cost": OV.cost([job_record(0, 331.7), job_record(0, 170)], {}),
                     "snapshots": OV.snapshots(runs)})
    return _OPS


def _data() -> dict:
    return {**_base(), **_ops()}


def _base() -> dict:
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
            "people": {"rule": "r", "people": [{"id": "P0001", "kind": "merged", "name": "Ann Lee", "keys": ["PK1"],
                        "records": [{"source": "crm", "label": "CRM", "id": "C1", "name": "Ann Lee", "email": "a@x.test", "phone": "", "address": "1 MAIN ST, 11211", "person_key": "PK1"}],
                        "strangers": [{"source": "vendor", "really": "Ann Lee", "person_key": "PK1"}],
                        "fields": [{"person_key": "PK1", "attr": "first_name", "value": "ann", "observations": 2, "sources": 2, "source": "crm",
                                    "first": "2024-01-01", "last": "2025-01-01", "won": True},
                                   {"person_key": "PK1", "attr": "first_name", "value": "an", "observations": 1, "sources": 1, "source": "vendor",
                                    "first": "2024-01-01", "last": "2024-02-01", "won": False}]}]},
            "agent": {"roles": {}, "scenes": [{"question": "q", "steps": [
                {"who": "U1", "roles": ["admin"], "asked": "q", "skill": "access_admin", "tool": "grant_role", "sources": [], "refused": False,
                 "proposals": ["P1"], "text": "Proposed P1"}, {"who": "U1", "roles": ["admin"], "asked": "approve P1", "skill": "approvals",
                 "tool": None, "sources": [], "refused": True, "proposals": [], "text": "Not done: the person who asked cannot approve"}]}]},
            "lineage": {**json.loads((RES / "lineage.json").read_text()), "start": "dim_person", "hint": "h"},
            "history": {"runs": runs_, "summary": OD.summary(runs_), "title": "t"},
            "stream": {"runs": [{"run_at": "2026-11-07T11:30:00Z", "ok": True, "events": 3900, "rows": 3880, "rows_loaded": 3880,
                                 "rows_quarantined": 0, "wikis": 212, "duplicates": 0, "events_per_second": 32.4, "lag_p50": 1.2, "lag_p95": 3.9,
                                 "resume_check": {"new": 200, "repeated": 0}, "published": None, "reconnects": 1,
                                 "reconnects_by_cause": {"planned": 1}, "drift": [], "disconnect_second": 60,
                                 "timeline": [[i, 15, 14, 1, 1, 0] for i in range(120)]},
                                {"run_at": "2026-11-08T11:30:00Z", "ok": False, "error": "StreamError: gave up after 50 reconnects"}],
                       "local": {"ok": True, "events": 1238, "rows": 1200, "rows_loaded": 1200, "rows_quarantined": 0, "wikis": 4,
                                 "duplicates": 25, "events_per_second": 220.0, "lag_p50": None, "lag_p95": None,
                                 "resume_check": {"new": 175, "repeated": 25}, "published": {"rows_after": 1200, "rows_new": 1200},
                                 "reconnects": 3, "reconnects_by_cause": {"http_429": 2, "planned": 1}, "drift": [], "tick": 0.1,
                                 "disconnect_second": 20, "timeline": [[i, 12, 11, 1, 1, 0] for i in range(40)]}},
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


def test_a_count_up_never_shows_a_negative_number():
    """A frame whose timestamp is before the count-up started drew "-336 rows" and "-0 personal columns"; in a
    collapsed section the browser draws no further frames, so the live app kept showing them."""
    pw = pytest.importorskip("playwright.sync_api")
    with pw.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as e:
            pytest.skip(f"no browser: {e}")
        pg = b.new_page()
        # one frame, stamped 50 ms before the animation starts, then none: what a hidden frame gets
        pg.add_init_script("window.requestAnimationFrame = cb => { const t = performance.now() - 50; "
                           "if (!window.__once) { window.__once = 1; setTimeout(() => cb(t), 0); } return 1; };")
        html = V.page("onboard", _data()["onboard"])     # served, not set_content, so the init script runs first
        pg.route("http://visual.test/", lambda r: r.fulfill(body=html, content_type="text/html"))
        pg.goto("http://visual.test/")
        pg.wait_for_timeout(300)
        assert pg.evaluate("window.__once") == 1                  # the stand-in frame clock was used
        values = pg.eval_on_selector_all(".tile .v", "es => es.map(e => e.textContent)")
        b.close()
    assert values and not [v for v in values if v.startswith("-")], values


@pytest.mark.parametrize("width", [1100, 375])
def test_no_onboarding_step_shows_a_cut_off_name(width):
    """With load taking most of the time, "draft" and "validate" were drawn as "draf" and "vali"."""
    pw = pytest.importorskip("playwright.sync_api")
    run = {**json.loads((RES / "onboarding_time.json").read_text()),
           "seconds": {"sync": 0.35, "draft": 0.02, "validate": 0.01, "load": 1.52}, "total_seconds": 1.90}
    with pw.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as e:
            pytest.skip(f"no browser: {e}")
        pg = b.new_page(viewport={"width": width, "height": 800})
        pg.set_content(V.page("onboard", {"run": run}))
        pg.wait_for_timeout(900)
        segs = pg.eval_on_selector_all('div[title$=" s"]', "es => es.map(e => [e.textContent, e.scrollWidth > e.clientWidth])")
        b.close()
    assert len(segs) == 4 and not [t for t, cut in segs if t and cut], segs
    assert "load" in [t for t, _ in segs]                                   # a step with room keeps its name


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


def test_a_source_the_model_does_not_use_is_said_so_not_priced_at_zero():
    """Email engagement has no inputs in this model; it showed "AUC +0.0000 [+0.0000, +0.0000]" and "$0.000",
    which reads as measured and worthless."""
    pw = pytest.importorskip("playwright.sync_api")
    with pw.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as e:
            pytest.skip(f"no browser: {e}")
        pg = b.new_page()
        pg.set_content(V.page("worth", _data()["worth"]))
        pg.wait_for_timeout(1100)
        rows = pg.eval_on_selector_all(".b", "es => es.map(e => [e.querySelector('.lab').innerText, e.querySelector('.val').innerText])")
        b.close()
    email = next(r for r in rows if r[0].startswith("Email"))
    assert "not an input to this model" in email[0] and email[1] == "n/a", email
    prop = next(r for r in rows if r[0].startswith("Property"))
    assert "without it, AUC -0.0496" in prop[0] and prop[1] == "$2.045", prop


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


def _browser():
    pw = pytest.importorskip("playwright.sync_api")
    p = pw.sync_playwright().start()
    try:
        b = p.chromium.launch()
    except Exception as e:
        p.stop()
        pytest.skip(f"no browser: {e}")
    return p, b


def test_the_kit_playground_replays_the_careless_cursor_losing_real_rows():
    p, b = _browser()
    try:
        pg = b.new_page(viewport={"width": 1100, "height": 900})
        errors = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.set_content(V.page("kit", _ops()["kit"]))
        pg.wait_for_timeout(5000)
        assert "600 of 600 held" in pg.inner_text("#app")
        pg.click("text=The careless cursor")
        pg.wait_for_timeout(6000)
        text = pg.inner_text("#app")
        lost = _ops()["kit"]["lost"]
        assert lost > 0 and f"{lost:,} lost for good" in text and "FAIL" in text
        assert not errors, errors
    finally:
        b.close()
        p.stop()


def test_the_scraper_timeline_shows_the_request_robots_txt_stopped():
    p, b = _browser()
    try:
        pg = b.new_page()
        pg.set_content(V.page("polite", _ops()["polite"]))
        pg.wait_for_timeout(3500)
        assert "/login not asked" in pg.inner_text("#app")
    finally:
        b.close()
        p.stop()


def test_clicking_an_incident_shows_what_was_done_and_links_its_postmortem():
    p, b = _browser()
    try:
        pg = b.new_page()
        pg.set_content(V.page("incidents", _ops()["incidents"]))
        pg.wait_for_timeout(500)
        text = pg.inner_text("#app")
        assert "pinned duckdb" in text and "hurt 42 min" in text.lower()
        assert pg.get_attribute("a.btn", "href").endswith("docs/postmortems/2026-11-07-motherduck-duckdb.md")
    finally:
        b.close()
        p.stop()


def test_the_cost_slider_and_private_toggle_reprice_from_the_measured_average():
    p, b = _browser()
    try:
        pg = b.new_page()
        pg.set_content(V.page("cost", _ops()["cost"]))
        pg.wait_for_timeout(300)
        avg = _ops()["cost"]["avg_minutes"]                    # (6 + 3) / 2
        assert pg.inner_text(".lp") == f"${avg * 0.006 * 8 * 30:.2f}" and pg.inner_text(".ch") == "$0.00"
        pg.fill(".rpd", "24")
        pg.dispatch_event(".rpd", "input")
        pg.click("text=Private repo")
        assert pg.inner_text(".ch") == f"${avg * 0.006 * 24 * 30:.2f}"
    finally:
        b.close()
        p.stop()
