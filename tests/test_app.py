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
    readme = (ROOT / "README.md").read_text()
    assert [r["people"] for r in res] == [20_000, 100_000, 300_000, 1_000_000]
    for r in res:
        assert f"{r['households_f1']:.3f}" in readme, r["people"]
        assert f"{r['deletion']['people_with_data_left']} of {r['deletion']['requested']:,}" in readme, r["people"]
        assert f"{100 * r['identity']['precision']:.1f}% / {100 * r['identity']['recall']:.1f}%" in readme, r["people"]


DESTINATIONS = ["overview", "sources_page", "identity_page", "audiences_page", "models_page", "agent_page", "activity_page"]
VIEWS = ["scale", "identity", "failure_lab", "deletion", "households_page", "audiences", "experiment", "agent",
         "activity", "scorecard", "ask_data_page", "onboarding_page", "release_page"]
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
    at.segmented_control(key="inc_pick").set_value("interrupted_sync").run()
    _button(at, lambda l: l == "Inject incident").click().run()
    _button(at, lambda l: l == "Investigate").click().run()
    assert not at.exception, at.exception
    _button(at, lambda l: l.startswith("Let the agent")).click().run()
    assert any("never approves its own work" in e.value for e in at.error)
    _button(at, lambda l: l.startswith("Approve as")).click().run()
    assert not at.exception, at.exception
    assert any("verified" in m.value for m in at.markdown)
