"""The demo app: what each page shows is checked against the answer key, the live world is never
changed by a viewer, and every page draws without an error."""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

from plumbline.app import demo as D
from plumbline.app import queries as Q
from plumbline.privacy.evaluate import snapshot

ROOT = Path(__file__).resolve().parents[1]
N = 2500


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    root = tmp_path_factory.mktemp("app")
    stages = []
    d = D.build(root, n_people=N, seed=11, progress=stages.append)
    return d, stages


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def test_the_world_is_built_by_the_real_pipeline_and_reused(world):
    d, stages = world
    assert stages[:4] == ["generate", "store_build", "store_sync", "ingest"] and "products" in stages
    assert d.summary["products"]["tests_passed"] == d.summary["products"]["tests"]
    before = _sha(d.warehouse)
    again = D.build(d.root, n_people=N, seed=11, progress=lambda s: pytest.fail(f"rebuilt: {s}"))
    assert again.summary == d.summary and _sha(d.warehouse) == before


def test_a_different_size_is_a_new_world_not_the_old_one_relabelled(world, tmp_path):
    d, _ = world
    root = tmp_path / "w"
    import shutil
    shutil.copytree(d.root, root)
    class Started(Exception):
        pass

    def stop(stage):
        raise Started(stage)
    with pytest.raises(Started, match="generate"):
        D.build(root, n_people=N + 1, seed=11, progress=stop)


def test_identity_verdicts_agree_with_the_answer_key(world):
    d, _ = world
    with Q.connect(d) as con:
        o = Q.identity_outcomes(con, d.run)
        assert (o.verdict == "correct").mean() > 0.97
        links = Q.links(d.run)
        for pid in Q.showcase(o)["correct"][:3]:
            recs = Q.person_records(con, d.run, pid)
            mine = set(links.loc[links.person_id == pid, "record_id"])
            assert len(recs) and set(recs.record_id) <= mine and recs.person_key.nunique() == 1
        for pid in Q.showcase(o)["merged"][:2]:
            row = o.set_index("person_id").loc[pid]
            s = Q.strangers_in(con, d.run, row["keys"], pid)
            assert len(s) and not (links.set_index("record_id").loc[s.record_id, "person_id"] == pid).any()


def test_households_are_one_real_home(world):
    d, _ = world
    with Q.connect(d) as con:
        h = Q.household_outcomes(con, d.run)
        assert (h.real_households == 1).mean() > 0.99
        k = h[h.members > 1].household_key.iloc[0]
        m = Q.household_members(con, d.run, k)
    assert len(m) == h.set_index("household_key").at[k, "members"]


def test_the_deletion_sandbox_removes_the_person_and_nobody_else_and_never_touches_the_world(world):
    d, _ = world
    before = _sha(d.warehouse)
    with Q.connect(d) as con:
        cands = Q.deletion_candidates(con, d.run, Q.identity_outcomes(con, d.run))
        held = snapshot(con)
    assert cands
    r = Q.deletion_sandbox(d, cands[0])
    assert r["removed"] > 0 and r["left_behind"] == 0 and r["innocent"] == 0
    assert set(r["records"].belongs_to) == {"the requester"}
    assert _sha(d.warehouse) == before
    with Q.connect(d) as con:
        assert snapshot(con) == held


def test_the_agent_answers_works_on_its_own_copy_and_never_calls_a_model_unless_told(world, tmp_path, monkeypatch):
    d, _ = world
    monkeypatch.setenv("PLUMBLINE_LLM_MODEL", "some-model")
    monkeypatch.setenv("PLUMBLINE_LLM_API_KEY", "sk-test-not-a-real-key")
    from plumbline.agents.agent import handle
    ctx = Q.agent_context(d, tmp_path / "a")
    assert ctx.model is None and ctx.warehouse != d.warehouse
    r = handle(ctx, "U0OPS0001", "is the ingest pipeline healthy?", channel="app")
    assert r.tool == "ingest_status" and "Invariant violations: none" in r.text
    assert handle(ctx, "U0OPS0001", "What is our parental leave policy?", channel="app").refused
    p = handle(ctx, "U0ADM0001", "give U0NEW0001 the privacy role", channel="app")
    assert p.proposals and handle(ctx, "U0ADM0001", f"approve {p.proposals[0]}", channel="app").refused   # not your own
    assert "approved and done" in handle(ctx, "U0ADM0002", f"approve {p.proposals[0]}", channel="app").text


def test_saved_results_are_the_numbers_in_the_readme():
    res = Q.saved_results()["runs"]
    readme = (ROOT / "docs" / "details.md").read_text()
    assert [r["people"] for r in res] == [20_000, 100_000, 300_000, 1_000_000]
    for r in res:
        assert f"{r['households_f1']:.3f}" in readme, r["people"]
        assert f"{r['deletion']['people_with_data_left']} of {r['deletion']['requested']:,}" in readme, r["people"]
        assert f"{100 * r['identity']['precision']:.1f}% / {100 * r['identity']['recall']:.1f}%" in readme, r["people"]


DESTINATIONS = ["overview", "workspace_page", "sources_page", "identity_page", "audiences_page", "models_page", "agent_page", "activity_page"]
VIEWS = ["scale", "identity", "failure_lab", "deletion", "households_page", "audiences", "experiment", "agent",
         "activity", "scorecard", "ask_data_page", "onboarding_page", "release_page", "reasons_page",
         "worth_buying", "open_data", "matcher_game", "tune_matcher"]
PAGES = DESTINATIONS + VIEWS


def _app(world, monkeypatch, page):
    st_testing = pytest.importorskip("streamlit.testing.v1")
    d, _ = world
    monkeypatch.setenv("PLUMBLINE_DEMO_DIR", str(d.root))
    monkeypatch.setenv("PLUMBLINE_DEMO_PEOPLE", str(N))
    monkeypatch.syspath_prepend(str(ROOT))
    sys.modules.pop("streamlit_app", None)
    at = st_testing.AppTest.from_string(f"import streamlit_app\nstreamlit_app.{page}()", default_timeout=300)
    at.run()
    assert not at.exception, at.exception
    return at


def _button(at, test):
    return next(b for b in at.button if test(b.label))


def test_every_page_in_the_menu_has_a_test():
    import ast
    tree = ast.parse((ROOT / "streamlit_app.py").read_text())
    pages = {c.args[0].id for c in ast.walk(tree)
             if isinstance(c, ast.Call) and getattr(c.func, "attr", "") == "Page" and c.args}
    assert pages == set(DESTINATIONS)


@pytest.mark.parametrize("page", PAGES)
def test_every_page_draws_without_an_error(world, page, monkeypatch):
    _app(world, monkeypatch, page)


def test_forget_them_then_replay_brings_nobody_back(world, monkeypatch):
    at = _app(world, monkeypatch, "deletion")
    _button(at, lambda l: l == "Forget them").click().run()
    assert not at.exception, at.exception
    assert any("0 of anyone else" in m.value for m in at.markdown)
    _button(at, lambda l: l == "Re-send every original file").click().run()
    assert not at.exception, at.exception
    assert any("0 of their records came back" in m.value for m in at.markdown)


def test_the_failure_lab_runs_a_real_sync_and_every_order_arrives_once(world, monkeypatch):
    at = _app(world, monkeypatch, "failure_lab")
    _button(at, lambda l: l == "Run the sync").click().run()
    assert not at.exception, at.exception
    r = at.session_state["lab"]
    assert r["resumed"] and r["missing"] == 0 and r["copies"] == r["received"] == r["expected"] and r["retries"] > 0


def test_a_split_rescores_and_undo_puts_the_numbers_back(world, monkeypatch):
    at = _app(world, monkeypatch, "identity")
    at.segmented_control(key="id_view").set_value("Fix it").run()
    first = [m.value for m in at.metric]
    _button(at, lambda l: l.startswith("Split out")).click().run()
    assert not at.exception, at.exception
    assert [m.value for m in at.metric] != first and len(at.session_state["edits"].history) == 1
    _button(at, lambda l: l == "Undo last change").click().run()
    assert [m.value for m in at.metric] == first


def test_a_preset_audience_counts_people_and_respects_consent(world, monkeypatch):
    at = _app(world, monkeypatch, "audiences")
    _button(at, lambda l: l == "Lapsed big spenders").click().run()
    assert not at.exception, at.exception
    assert at.text_input(key="aud_text").value
    _button(at, lambda l: l == "Save this audience").click().run()
    assert not at.exception, at.exception
    from plumbline.app.ops import ActivityLog
    d, _ = world
    saved = ActivityLog(d.root.parent / f"{d.root.name}-activity.jsonl").recent(1).iloc[0]
    assert saved.action == "audience: saved" and saved.result["rules"]["channel"] == "email_ok"
    assert 0 < saved.result["reachable"] <= saved.result["segment"]


def test_the_agent_needs_a_second_admin_to_approve(world, monkeypatch):
    at = _app(world, monkeypatch, "agent")
    at.chat_input[0].set_value("Give U0NEW0001 the privacy role").run()
    approve = [b for b in at.button if b.label.startswith("Approve P")]
    assert approve and "U0ADM0002" in approve[0].label          # a second admin, never the asker
    approve[0].click().run()
    assert not at.exception, at.exception
    assert any("approved and done" in m.value for m in at.markdown)


def test_a_cold_start_then_a_second_viewer_both_draw(tmp_path, monkeypatch):
    """Deploy regression: the first version built the world inside st.cache_resource and wrote its
    progress from there, so Streamlit tried to replay those writes for the next viewer and every page
    after the first visit crashed (KeyError in replay_cached_messages)."""
    st_testing = pytest.importorskip("streamlit.testing.v1")
    monkeypatch.setenv("PLUMBLINE_DEMO_DIR", str(tmp_path / "demo"))
    monkeypatch.setenv("PLUMBLINE_DEMO_PEOPLE", "1500")
    monkeypatch.syspath_prepend(str(ROOT))
    for page in ("overview", "identity"):
        sys.modules.pop("streamlit_app", None)
        at = st_testing.AppTest.from_string(f"import streamlit_app\nstreamlit_app.{page}()", default_timeout=600)
        at.run()
        assert not at.exception, at.exception


# --- the app refuses to start on the wrong Python ---------------------------------------------

def test_every_dependency_has_a_module_to_check():
    from plumbline.app import preflight
    assert set(preflight.required()) <= set(preflight.IMPORT_NAME), "add the new dependency to IMPORT_NAME"
    assert {"faker", "duckdb", "splink", "dbt-core", "streamlit"} <= set(preflight.required())


def test_version_ranges_with_commas_are_read_as_one_requirement(tmp_path):
    """Regression: splitting the list on commas turned "streamlit>=1.50,<2" into "streamlit" and "<2",
    and the check itself crashed on a dependency named ''."""
    from plumbline.app import preflight
    f = tmp_path / "pyproject.toml"
    f.write_text('[project]\ndependencies = [\n  "splink>=4.0.6,<5",\n  "pyyaml>=6",\n]\n'
                 '[project.optional-dependencies]\napp = ["streamlit>=1.65,<2"]\n')
    assert preflight.required(f) == ["splink", "pyyaml", "streamlit"]


def test_a_python_without_the_projects_libraries_gets_one_clear_message(monkeypatch):
    """Regression: run by Anaconda's streamlit instead of the project's, every page crashed with
    "No module named 'faker'" deep in the generator. Now the app stops at the door and says why."""
    st_testing = pytest.importorskip("streamlit.testing.v1")
    from plumbline.app import preflight
    monkeypatch.setattr(preflight, "missing", lambda *a, **k: ["faker"])
    monkeypatch.syspath_prepend(str(ROOT))
    sys.modules.pop("streamlit_app", None)
    at = st_testing.AppTest.from_string("import streamlit_app", default_timeout=60)
    at.run()
    assert not at.exception, at.exception
    assert "faker" in at.error[0].value and "python -m streamlit run" in at.code[0].value


def test_graphs_put_each_record_with_its_real_owner(world):
    d, _ = world
    with Q.connect(d) as con:
        o = Q.identity_outcomes(con, d.run)
        pid = Q.showcase(o)["merged"][0]
        row = o.set_index("person_id").loc[pid]
        recs, strangers = Q.person_records(con, d.run, pid), Q.strangers_in(con, d.run, row["keys"], pid)
        dot = Q.person_dot(recs, strangers, Q.person_truth(d.run, pid)["name"])
        h = Q.household_outcomes(con, d.run)
        m = Q.household_members(con, d.run, h[h.members > 1].household_key.iloc[0])
    body = dot.split("subgraph")
    assert "truth [" in body[0] and all("truth [" not in b for b in body[1:])     # the answer key sits outside every cluster
    assert dot.count("someone else") == len(strangers) and dot.count("-> truth") >= 1
    assert Q.household_dot(m, "1 TEST ST").count("home -> m") == len(m)


def test_the_sql_guardrail_page_blocks_an_attack_and_runs_a_fair_question(world, monkeypatch):
    at = _app(world, monkeypatch, "ask_data_page")
    at.segmented_control(key="ask_view").set_value("Try to break it").run()
    assert any("Blocked" in m.value and "contact data" in m.value for m in at.markdown)
    at.pills(key="attack").set_value("A fair question").run()
    assert not at.exception and any("Allowed" in m.value for m in at.markdown)


def test_sql_that_passes_the_guard_but_fails_in_the_database_is_reported_not_a_crash(world, monkeypatch):
    """An ambiguous column (or the 10-second limit) raised a database error the page did not catch."""
    at = _app(world, monkeypatch, "ask_data_page")
    at.segmented_control(key="ask_view").set_value("Try to break it").run()
    at.pills(key="attack").set_value("A fair question").run()
    box = next(t for t in at.text_area if t.key.startswith("atk_"))
    box.set_value("SELECT person_key FROM products.dim_person, products.dim_person AS b").run()
    assert not at.exception, at.exception
    assert any("the query failed" in m.value and "Ambiguous" in m.value for m in at.markdown)


def test_unselecting_the_attack_shows_a_prompt_not_a_wrong_verdict(world, monkeypatch):
    """Clicking the selected pill unselects it; the empty SQL then read "Blocked: exactly one statement
    is allowed", which looked like the guard giving the phone-number attempt the wrong reason."""
    at = _app(world, monkeypatch, "ask_data_page")
    at.segmented_control(key="ask_view").set_value("Try to break it").run()
    at.pills(key="attack").set_value(None).run()
    assert not at.exception and not any("Blocked" in m.value for m in at.markdown)
    assert any("Pick an attempt" in m.value for m in at.markdown)


def test_the_release_gate_page_holds_a_distorted_challenger(world, monkeypatch):
    at = _app(world, monkeypatch, "release_page")
    assert any("held" in m.value and "calibrated" in m.value for m in at.markdown)
    at.slider(key="rg_bend").set_value(1.0).run()
    assert not at.exception and any("not shown better" in m.value for m in at.markdown)


def test_a_dependency_the_table_does_not_know_is_checked_by_name_not_a_crash(tmp_path):
    from plumbline.app import preflight
    pp = tmp_path / "pyproject.toml"
    pp.write_text('[project]\ndependencies = ["numpy>=1", "surely-not-installed-pkg>=1"]\n')
    assert preflight.missing(pp) == ["surely-not-installed-pkg"]


def test_a_recorded_paraphrase_is_routed_by_the_model_on_the_public_page(world, monkeypatch):
    """No key on the page: the model's recorded tool choice replays, and a write is still a proposal."""
    at = _app(world, monkeypatch, "agent")
    at.pills(key="agent_pill").set_value("U0NEW0001 joined the privacy team, set them up").run()
    assert not at.exception, at.exception
    assert any("tool chosen by gpt-4.1-mini" in c.value for c in at.caption)
    assert any("Nothing has changed yet" in m.value for m in at.markdown)


def test_an_incident_runs_end_to_end_on_the_page_and_the_agent_cannot_approve_itself(world, monkeypatch):
    at = _app(world, monkeypatch, "agent_page")
    at.segmented_control(key="agent_dest").set_value("Incidents").run()     # "Watch it work" opens first
    at.segmented_control(key="inc_pick").set_value("interrupted_sync").run()
    _button(at, lambda l: l == "Inject incident").click().run()
    _button(at, lambda l: l == "Investigate").click().run()
    assert not at.exception, at.exception
    _button(at, lambda l: l.startswith("Let the agent")).click().run()
    assert any("never approves its own work" in e.value for e in at.error)
    _button(at, lambda l: l.startswith("Approve as")).click().run()
    assert not at.exception, at.exception
    assert any("verified" in m.value for m in at.markdown)


def test_the_suite_stops_with_one_clear_message_on_the_wrong_python(tmp_path):
    """Run from Anaconda's base Python, the suite used to fail with twelve unrelated import errors."""
    import subprocess
    code = ("import sys, importlib.util as u; real = u.find_spec\n"
            "def fs(n, *a, **k):\n    return None if n == 'splink' else real(n, *a, **k)\n"
            "u.find_spec = fs\nimport pytest; sys.exit(pytest.main(['-q', '--co', '-p', 'no:cacheprovider']))")
    r = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=120)
    out = r.stdout + r.stderr
    assert r.returncode == 2, out[-800:]
    assert "missing: splink" in out and "source .venv/bin/activate" in out


def test_the_footer_names_the_author_and_links_the_code_but_no_email():
    st_testing = pytest.importorskip("streamlit.testing.v1")
    at = st_testing.AppTest.from_string("import streamlit_app\nstreamlit_app.footer()", default_timeout=60).run()
    text = next(m.value for m in at.markdown if "Built by" in m.value)
    assert "Akila Lourdes Miriyala Francis" in text and "github.com/AKilalours/plumbline" in text
    assert "@" not in text
    from plumbline.app.version import fingerprint
    assert f"code {fingerprint()}" in text                      # what the live check compares with the pushed code


def test_the_activity_log_shows_a_visitor_only_their_own_actions(world, monkeypatch):
    """The log is one file for the whole public app and holds what visitors typed (agent questions,
    SQL); the page showed everyone's, so one stranger's text reached the next visitor."""
    import json as _json
    d, _ = world
    log = d.root.parent / f"{d.root.name}-activity.jsonl"
    with open(log, "a") as f:
        f.write(_json.dumps({"at": "2026-01-01 00:00:00 UTC", "who": "visitor-zzzz", "action": "agent: answer",
                             "result": {"asked": "SOMEONE ELSE TYPED THIS"}}) + "\n")
    at = _app(world, monkeypatch, "activity")
    shown = " ".join(str(df.value.to_dict()) for df in at.dataframe)
    assert "SOMEONE ELSE TYPED THIS" not in shown and "visitor-zzzz" not in shown


def test_changed_code_under_a_running_app_is_loaded_fresh_all_together(tmp_path):
    """Live, after a push the page called O.unused_words while the old ops module, imported before the
    push, was still loaded: AttributeError. Changed files must replace every plumbline module at once.
    Run in its own process: reloading plumbline here would leave this session's tests holding old modules."""
    import os
    import subprocess
    pytest.importorskip("streamlit")
    script = f"""
import os, sys, pathlib
sys.path.insert(0, {str(ROOT)!r})
import streamlit_app
import plumbline.app.ops as before
d = pathlib.Path({str(tmp_path)!r}); f = d / "x.py"; f.write_text("")
streamlit_app.fresh_code(d)                       # first look at d: records its stamp
print("unchanged", streamlit_app.fresh_code(d))
os.utime(f, ns=(1, 10**19))                        # the push: a file changed on disk
print("changed", streamlit_app.fresh_code(d))
import plumbline.app.ops as after
print("same_module", after is before, "has_new", hasattr(after, "unused_words"))
"""
    r = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=120,
                       env={**os.environ, "PLUMBLINE_DEMO_DIR": str(tmp_path / "demo")})
    out = r.stdout
    assert "unchanged False" in out and "changed True" in out and "same_module False has_new True" in out, r.stderr[-2000:]


def test_a_changed_page_script_alone_reloads_and_drops_streamlits_compiled_copy(tmp_path):
    """Live on 2026-11-07, after a push the hosted app ran the old page script with the new ops module: an old
    button beside a new option list. A change to the page script must count as changed code, and Streamlit's
    compiled copy of it must be dropped. Own process, as above."""
    import os
    import subprocess
    pytest.importorskip("streamlit")
    script = f"""
import os, sys, pathlib
sys.path.insert(0, {str(ROOT)!r})
import streamlit_app
from streamlit import runtime
cleared = []
class Cache:
    def clear(self): cleared.append(1)
class R:
    _script_cache = Cache()
runtime.Runtime.instance = staticmethod(lambda: R())
d = pathlib.Path({str(tmp_path)!r}); (d / "x.py").write_text(""); m = d / "page.py"; m.write_text("old")
streamlit_app.fresh_code(d, m)
print("unchanged", streamlit_app.fresh_code(d, m))
os.utime(m, ns=(1, 10**19))                         # the push changed only the page script
print("changed", streamlit_app.fresh_code(d, m), "cache_cleared", bool(cleared))
"""
    r = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=120,
                       env={**os.environ, "PLUMBLINE_DEMO_DIR": str(tmp_path / "demo")})
    assert "unchanged False" in r.stdout and "changed True cache_cleared True" in r.stdout, r.stderr[-2000:]


def test_clicking_the_open_tab_again_keeps_it_open(world, monkeypatch):
    """Streamlit unselects the active option on a second click; every tabbed page then jumped back to its
    first view with nothing highlighted (the Agent's Ask view lost its nine sample questions)."""
    at = _app(world, monkeypatch, "agent_page")
    at.segmented_control(key="agent_dest").set_value("Ask").run()
    at.segmented_control(key="agent_dest").set_value(None).run()           # the second click
    assert not at.exception
    assert at.segmented_control(key="agent_dest").value == "Ask" and len(at.pills(key="agent_pill").options) == 9


def test_worth_buying_prices_a_record_only_where_the_loss_is_measured():
    import json
    from plumbline.app import visual_data as VD
    sv = json.loads((ROOT / "docs/results/source_value.json").read_text())
    w = {x["label"].split()[0]: x for x in VD.worth(sv)["sources"]}
    m = sv["model"]
    assert w["Property"]["lost_per_person"] * 2_000 == pytest.approx(31 / m["test_people"] * 2_000)   # $2.045 a person
    assert w["Data"]["lost_per_person"] == 0 and not w["Data"]["measured"]       # the vendor's interval spans zero
    assert all(x["hi"] < 0 for x in w.values() if x["measured"])


def test_open_data_onboards_a_test_dataset_through_failures_and_drops_people(world, monkeypatch):
    at = _app(world, monkeypatch, "open_data")
    at.slider(key="od_429").set_value(25).run()
    _button(at, lambda l: l == "Onboard it now").click().run()
    assert not at.exception, at.exception
    r = at.session_state["od_last"]["r"]
    assert r["rows_landed"] >= 5_000 and r["rows_quarantined"] == 0 and r["retries"] > 0
    assert r["rows_loaded"] == 5_000                                   # corrected rows kept once
    assert "owner_s_first_name" in r["dropped_personal"] and "filing_status" in r["drift"]
    assert "owner" not in at.session_state["od_last"]["draft"]


def test_the_onboarding_demo_says_it_runs_on_a_test_server_not_nyc(world, monkeypatch):
    """The demo's result read like a real NYC pull, and its draft named a real NYC dataset (ipu4-2q9a), while every row
    came from the local test server. A visitor could not tell; now the result and the draft both say so."""
    at = _app(world, monkeypatch, "open_data")
    _button(at, lambda l: l == "Onboard it now").click().run()
    assert not at.exception, at.exception
    said = [m.value for m in at.markdown] + [c.value for c in at.caption]
    assert any("local test server" in t and "current rows" in t for t in said), "the result must say where its rows came from"
    draft = at.session_state["od_last"]["draft"]
    assert "ipu4-2q9a" not in draft and "local-test-permits" in draft


def test_the_scale_projection_offers_only_stages_it_can_project():
    """The picker drew "NaN h" for a stage the plan has no projection for."""
    import json
    from plumbline.app import visual_data as VD
    plan = json.loads((ROOT / "docs/results/scale_plan.json").read_text())
    d = VD.scale(plan, {})
    for size in d["targets"]:
        assert set(d["projected"][size]) <= {s["key"] for s in d["stages"]}
    assert {s["key"] for s in d["stages"]} - set(d["projected"]["1 TB"])     # some stage has none: the browser must skip it


def test_the_flows_account_for_every_row_and_every_record(world):
    from plumbline.app import visual_data as VD
    d, _ = world
    with Q.connect(d) as con:
        rows, ppl = VD.row_flow(con), VD.people_flow(con)
        sent = con.execute("SELECT sum(rows_in) FROM ingest_ledger").fetchone()[0]
        recs, persons = con.execute("SELECT count(*), count(DISTINCT person_key) FROM identity_records").fetchone()
    for f in (rows, ppl):
        for n in f["left"]:
            assert sum(k["value"] for k in f["links"] if k["from"] == n["key"]) == n["value"]
        for n in f["right"]:
            assert sum(k["value"] for k in f["links"] if k["to"] == n["key"]) == n["value"]
    assert sum(n["value"] for n in rows["left"]) == sum(n["value"] for n in rows["right"]) == sent
    assert sum(n["value"] for n in ppl["left"]) == recs and ppl["people"] == persons


def test_the_matcher_game_scores_against_the_answer_key(world):
    from plumbline.app import visual_data as VD
    d, _ = world
    with Q.connect(d) as con:
        g = VD.game(con, d.run)
    assert len(g["pairs"]) == 12 and any(p["truth"] != p["matcher"] for p in g["pairs"])
    for p in g["pairs"]:
        assert set(p["agree"]) == set(g["fields"]) and 0 <= p["p"] <= 1
        if p["truth"] != p["matcher"]:
            assert p["why"]


def test_the_live_city_counts_the_whole_warehouse_and_marks_the_matchers_mistakes(world):
    from plumbline.app import visual_data as VD
    d, _ = world
    with Q.connect(d) as con:
        c = VD.city(con, d.run)
        sent = con.execute("SELECT sum(rows_in) FROM ingest_ledger").fetchone()[0]
        people = con.execute("SELECT count(DISTINCT person_key) FROM identity_records").fetchone()[0]
    t = c["totals"]
    assert sum(t["rows_in"].values()) == sent == t["loaded"] + t["quarantined"] + t["suppressed"] + t["already"]
    assert t["people"] == people and 0 < len(c["people"]) <= 140
    assert any(p["mixed"] for p in c["people"])          # mistakes are shown, not filtered out
    assert sum(p["borough"] is not None for p in c["people"]) > 0.9 * len(c["people"])   # placed by their real ZIP
    assert abs(sum(c["borough_share"].values()) - 1) < 0.02 and len(c["held"]) == min(40, t["quarantined"])


def test_the_threshold_view_counts_at_production_exactly_what_its_cards_count(world):
    """The histogram once had no edge at 0.9, so the tiles said 1,157 wrong joins and the card below said 1,194."""
    from plumbline.app import visual_data as VD
    from plumbline.identity.match import MATCH_THRESHOLD
    d, _ = world
    with Q.connect(d) as con:
        t = VD.threshold(con, d.run, MATCH_THRESHOLD)
        over = con.execute("SELECT count(*) FROM identity_pairs WHERE match_probability >= ?", [MATCH_THRESHOLD]).fetchone()[0]
    k = next(i for i, e in enumerate(t["edges"]) if abs(e - t["prod"]) < 1e-9)
    assert sum(t["diff"][k:]) == t["rules"]["score_says_join_two_people"]
    assert sum(t["same"][k:]) + sum(t["diff"][k:]) <= over and sum(t["same"]) + sum(t["diff"]) == t["pairs"]
    assert t["rules"]["joined_two_people"] < t["rules"]["score_says_join_two_people"]


def test_the_people_explorer_shows_real_records_and_who_won_each_field(world):
    from plumbline.app import visual_data as VD
    d, _ = world
    with Q.connect(d) as con:
        o = Q.identity_outcomes(con, d.run)
        x = VD.people_explorer(con, d.run, o, k=5)
    kinds = {p["kind"] for p in x["people"]}
    assert "correct" in kinds and x["people"]
    for p in x["people"]:
        assert p["records"] and all(r["source"] in ("crm", "vendor", "store") for r in p["records"])
        won = {}
        for f in p["fields"]:
            won.setdefault((f["person_key"], f["attr"]), []).append(f["won"])
        assert all(sum(v) == 1 for v in won.values())           # exactly one winner per field
        if p["kind"] == "merged":
            assert p["strangers"]


def test_the_agent_scenes_are_real_runs_and_a_change_needs_a_second_person(world):
    from plumbline.app import visual_data as VD
    d, _ = world
    sc = VD.agent_scenes(d)
    by = {s["question"] + s["steps"][0]["who"]: s for s in sc["scenes"]}
    health = next(s for s in sc["scenes"] if s["question"] == "Is the pipeline healthy?")
    assert health["steps"][0]["tool"] and not health["steps"][0]["refused"]
    leave = next(s for s in sc["scenes"] if "parental leave" in s["question"])
    assert leave["steps"][0]["refused"]
    for s in sc["scenes"]:
        if s["steps"][0]["proposals"]:
            assert s["steps"][1]["refused"] and not s["steps"][2]["refused"]      # the asker cannot; a second admin can
            assert s["steps"][2]["who"] != s["steps"][0]["who"]
    assert by


def test_the_live_stream_view_runs_the_real_connector_against_a_misbehaving_stream(world, monkeypatch):
    at = _app(world, monkeypatch, "sources_page")
    at.segmented_control(key="src_view").set_value("Live stream").run()
    assert not at.exception, at.exception
    _button(at, lambda l: l == "Run the connector now").click().run(timeout=120)
    assert not at.exception, at.exception
    r = at.session_state.st_last
    assert r["rows_loaded"] == r["rows"] and r["rows_quarantined"] == 0
    assert r["duplicates"] == r["resume_check"]["repeated"] == 25 and r["reconnects_by_cause"]["http_429"] == 2


def test_feed_health_renders_from_a_run_history_with_a_failure_in_it():
    st_testing = pytest.importorskip("streamlit.testing.v1")
    code = ("import streamlit_app\nfrom datetime import datetime, timedelta, timezone\n"
            "now = datetime.now(timezone.utc)\n"
            "runs = [{'run_at': (now - timedelta(days=d, hours=1)).strftime('%Y-%m-%dT%H:%M:%SZ'), 'ok': d != 2,"
            " 'error': None if d != 2 else 'StoreAPIError: HTTP 503'} for d in range(6, -1, -1)]\n"
            "streamlit_app.feed_health(runs)\nstreamlit_app.feed_health([])\n")
    at = st_testing.AppTest.from_string(code, default_timeout=60).run()
    assert not at.exception, at.exception


def test_the_gate_lab_holds_a_truncated_vendor_file_and_loads_it_once_released(world, monkeypatch):
    at = _app(world, monkeypatch, "sources_page")
    at.segmented_control(key="src_view").set_value("Failure lab").run()
    at.segmented_control(key="gate_fault").set_value("truncated").run()
    _button(at, lambda l: l == "Send the file").click().run(timeout=180)
    assert not at.exception, at.exception
    r = at.session_state.gate_last
    assert r["status"] == "held" and r["rows_stored"] == 0 and any("rows against a usual" in x for x in r["reasons"])
    assert any(c["outside"] and c["check"] == "Rows in the file" for c in r["checks"])
    at.text_input(key="gate_reason").input("vendor confirmed a smaller file this week").run()
    _button(at, lambda l: l == "Release it").click().run(timeout=180)
    assert not at.exception, at.exception
    r = at.session_state.gate_last
    assert r["status"] == "loaded" and r["rows_stored"] == r["rows_in_file"]
    assert r["reviews"][0] == ("data ops (you)", "vendor confirmed a smaller file this week")


def test_the_gate_lab_control_file_loads_without_a_hold(world):
    from plumbline.app import ops as O
    r = O.gate_lab(world[0], "none")
    assert r["status"] == "loaded" and r["held_total"] == 0
    for fault in ("blank_email", "zip_format"):
        assert O.gate_lab(world[0], fault)["status"] == "held", fault


def test_every_overview_card_says_what_its_number_counts(world, monkeypatch):
    """A card read "CLEAN 7 / 12,439": 7 rows held back, which a reader took as 7 clean rows."""
    import streamlit_app as A
    at = _app(world, monkeypatch, "overview")
    assert not at.exception, at.exception
    html = " ".join(m.value for m in at.markdown)
    for unit in A.CARD_UNIT.values():
        assert unit.split(",")[0] in html, unit
    assert "Clean</" not in html and ">Current<" not in html


def test_an_uploaded_vendor_file_is_rejected_held_or_loaded_by_the_real_loader(world):
    """The gate lab takes a file a visitor edited: unchanged it loads; cut short it is held; without a required
    column the contract rejects it before the gate ever sees it."""
    from plumbline.app import ops as O
    d = world[0]
    name, data = O.gate_lab_file(d)
    assert name.startswith("vendor_week_") and data.count(b"\n") > 50
    assert O.gate_lab(d, "upload", upload=data)["status"] == "loaded"
    lines = data.decode().splitlines()
    cut = ("\n".join(lines[: len(lines) // 3]) + "\n").encode()
    held = O.gate_lab(d, "upload", upload=cut)
    assert held["status"] == "held" and any(c["outside"] for c in held["checks"])
    header = lines[0].split(",")
    i = header.index("full_name")
    no_name = ("\n".join(",".join(x for j, x in enumerate(l.split(",")) if j != i) for l in lines) + "\n").encode()
    rej = O.gate_lab(d, "upload", upload=no_name)
    assert rej["status"] == "rejected" and "full_name" in rej["note"]
    with pytest.raises(ValueError, match="choose a CSV"):
        O.gate_lab(d, "upload")


def test_a_drafted_column_says_what_was_wrong_in_words():
    """The table showed the answer key as "address street" and an empty Check column."""
    from plumbline.app import ops as A
    gold = {"type": "date", "privacy": None, "identity": None}
    bad = A.draft_row({"name": "JoinDate", "type": "string", "privacy": None, "identity": None, "unsure": ""}, gold, "2024-01-01")
    assert bad["Right?"] == "type should be date" and bad["Answer key"] == "date"
    ok = A.draft_row({"name": "Email", "type": "email", "privacy": "email", "identity": None, "unsure": ""},
                     {"type": "email", "privacy": "email", "identity": None}, "a@b.test")
    assert ok["Right?"] == "✓" and ok["Drafted"] == "email · contact: email"
    missed = A.draft_row({"name": "Cell", "type": "string", "privacy": None, "identity": None, "unsure": ""},
                         {"type": "phone", "privacy": "phone", "identity": None}, "555")
    assert "missed contact: phone" in missed["Right?"]


def test_the_onboarding_page_opens_on_a_held_out_file(world, monkeypatch):
    """It opened on a practice file both drafters get fully right, which flattered them."""
    at = _app(world, monkeypatch, "onboarding_page")
    assert not at.exception, at.exception
    if not at.session_state["ob_pick"]:
        pytest.skip("no recorded model run")
    import json
    rows = json.loads((ROOT / "docs/results/ai/summary.json").read_text())["onboarding"]["rows"]
    assert next(r for r in rows if r["partner"] == at.session_state["ob_pick"])["split"] == "test"
    assert any("held-out files" in m.value for m in at.markdown)


def test_the_overview_says_why_its_cards_count_fewer_people_than_the_animation(world, monkeypatch):
    """The cards said "of 2,762" and the animation "2,958 people" on one page, with nothing saying why."""
    import yaml
    at = _app(world, monkeypatch, "overview")
    as_of = str(yaml.safe_load((ROOT / "dbt/dbt_project.yml").read_text())["vars"]["as_of"])
    assert any(f"snapshot as of {as_of}" in m.value for m in at.markdown)


def test_a_wrongly_joined_namesake_is_called_another_person_with_the_same_name():
    """Two Cynthia Glovers: the graph labelled the other one's record "Cynthia Glover, someone else" beside an answer
    key also reading "Cynthia Glover", which looked like a contradiction."""
    import pandas as pd
    from plumbline.app import queries as Q
    recs = pd.DataFrame([{"kind": "CRM", "record_id": "C1", "name": "Cynthia Glover", "person_key": "PK1"}])
    strangers = pd.DataFrame([{"source": "vendor", "record_id": "V9", "really": "Cynthia Glover", "person_key": "PK1"},
                              {"source": "store", "record_id": "O3", "really": "Ann Lee", "person_key": "PK1"}])
    dot = Q.person_dot(recs, strangers, "Cynthia Glover")
    assert "Cynthia Glover\\nanother person, same name" in dot and "Ann Lee\\nsomeone else" in dot


def test_a_visitor_runs_the_sample_files_end_to_end_and_gets_a_checked_result(world, monkeypatch):
    at = _app(world, monkeypatch, "workspace_page")
    _button(at, lambda l: "sample files" in l).click().run()
    assert not at.exception, at.exception
    _button(at, lambda l: l == "Clean and match").click().run()
    assert not at.exception, at.exception
    ws = at.session_state["ws"]
    s, chk = ws["result"].stats(), ws["check"]
    assert s["files"] == 3 and s["people"] < s["records"] and chk["precision"] > 0.9 and chk["recall"] > 0.9
    assert any("answer key" in m.value for m in at.markdown)
    for view in ("Why they were joined", "Ask", "Held back", "Customers"):
        at.segmented_control(key="ws_view").set_value(view).run()
        assert not at.exception, (view, at.exception)


def test_a_shared_link_shows_counts_and_a_forged_one_shows_nothing(world, monkeypatch):
    from plumbline import workspace as W
    a = W.read("x.csv", b"name,email\nAnn Lee,ann@x.test\nAnn Lee,ann@x.test\nBo Kim,bo@x.test\n")
    tok = W.share_token(W.run([a], [W.detect(a)]))
    st_testing = pytest.importorskip("streamlit.testing.v1")
    d, _ = world
    monkeypatch.setenv("PLUMBLINE_DEMO_DIR", str(d.root))
    monkeypatch.setenv("PLUMBLINE_DEMO_PEOPLE", str(N))
    monkeypatch.syspath_prepend(str(ROOT))
    for token, shown in ((tok, True), ("not-a-real-token", False)):
        sys.modules.pop("streamlit_app", None)
        at = st_testing.AppTest.from_string("import streamlit_app\nstreamlit_app.workspace_page()", default_timeout=300)
        at.query_params["r"] = token
        at.run()
        assert not at.exception, at.exception
        assert any("Shared result: 3 rows" in m.value for m in at.markdown) is shown


def test_the_database_view_replays_the_recorded_change_log(world, monkeypatch):
    at = _app(world, monkeypatch, "sources_page")
    at.segmented_control(key="src_view").set_value("Database").run()
    assert not at.exception, at.exception
    for step in (0, 3, 11):
        at.slider(key="cdc_step").set_value(step).run()
        assert not at.exception, (step, at.exception)


def test_the_reconcile_tiles_add_up_the_runs(monkeypatch):
    from streamlit.testing.v1 import AppTest
    runs = [{"run_at": "2026-11-12T00:00:00Z", "ok": True},
            {"run_at": "2026-11-12T03:00:00Z", "ok": True, "reconcile": {"checked": 2000, "same": 1990, "changed": 8, "gone": 2,
             "fields": {"apt_condo_no_s": 8}, "held": 68000}},
            {"run_at": "2026-11-12T06:00:00Z", "ok": True, "reconcile": {"checked": 2000, "same": 2000, "changed": 0, "gone": 0,
             "fields": {}, "held": 68000}}]
    at = AppTest.from_string(f"import streamlit_app\nstreamlit_app.reconcile_tiles({runs!r})", default_timeout=120)
    monkeypatch.syspath_prepend(str(ROOT))
    at.run()
    assert not at.exception, at.exception


def test_the_health_view_shows_how_often_each_feed_needed_a_person(world, monkeypatch):
    at = _app(world, monkeypatch, "sources_page")
    at.segmented_control(key="src_view").set_value("Health").run()
    assert not at.exception, at.exception
    assert any("How often a person had to step in" in m.value for m in at.markdown)


def test_the_recovery_panel_renders_from_a_history_with_a_failure_and_a_rerun_by_hand():
    st_testing = pytest.importorskip("streamlit.testing.v1")
    code = ("import streamlit_app\nfrom datetime import datetime, timedelta, timezone\n"
            "now = datetime.now(timezone.utc)\n"
            "f = lambda h: (now - timedelta(hours=h)).strftime('%Y-%m-%dT%H:%M:%SZ')\n"
            "runs = [{'run_at': f(9), 'ok': True, 'retries': 2}, {'run_at': f(6), 'ok': False},"
            " {'run_at': f(3), 'ok': True, 'trigger': 'workflow_dispatch'}]\n"
            "streamlit_app.open_data_runs = lambda: runs\nstreamlit_app.stream_runs = lambda: []\n"
            "streamlit_app.recovery_panel()\n")
    at = st_testing.AppTest.from_string(code, default_timeout=60).run()
    assert not at.exception, at.exception


def test_the_cost_panel_renders_from_measured_jobs_and_from_steps_only():
    st_testing = pytest.importorskip("streamlit.testing.v1")
    for jobs in ("[plumbline.ingest.cost.job_record(0, 170)]", "[]"):
        code = ("import streamlit_app, plumbline.ingest.cost\n"
                f"plumbline.ingest.cost.jobs = lambda *a, **k: {jobs}\n"
                "streamlit_app.open_data_runs = lambda: [{'run_at': '2026-11-12T00:00:00Z', 'ok': True, 'seconds': 120}]\n"
                "streamlit_app.stream_runs = lambda: []\nstreamlit_app.cost_panel()\n")
        at = st_testing.AppTest.from_string(code, default_timeout=60).run()
        assert not at.exception, at.exception
        assert any("What a run costs" in m.value for m in at.markdown)
