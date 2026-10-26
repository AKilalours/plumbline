"""Module E: the operations agent. Answers with sources or refuses, reads through read-only tools,
writes only after a named person with the right role approves, and audits every step."""
from __future__ import annotations

import io
import json
import re
import shutil
import urllib.error
from pathlib import Path

import duckdb
import pytest

from plumbline.agents import approvals, audit, evals
from plumbline.agents.agent import Context, handle
from plumbline.agents.answer import REFUSAL, answer
from plumbline.agents.knowledge import Index
from plumbline.agents.llm import ChatModel, Completion, LLMError
from plumbline.agents.skills import SkillError, load as load_skills
from plumbline.agents.tools import TOOLS
from plumbline.identity.pipeline import resolve
from plumbline.ingest.connectors.store_api import StoreClient, sync_orders
from plumbline.ingest.contracts import load_contracts
from plumbline.ingest.engine import ingest
from plumbline.ml.reference import load_reference_tables
from plumbline.privacy import tombstones
from plumbline.privacy.person_deletion import expand_deletions
from plumbline.products.build import build
from plumbline.store.db import build_store
from plumbline.store.server import MockStore
from plumbline.synth.config import SynthConfig
from plumbline.synth.generate import generate
from plumbline.synth.reference import load_reference

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = load_contracts(ROOT / "sources")
ROLES = {"U_OPS": {"data-platform", "docs"}, "U_PRIV": {"privacy"}, "U_PRIV2": {"privacy"}, "U_DOCS": {"docs"}}


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    d = tmp_path_factory.mktemp("agent")
    run = d / "run"
    generate(load_reference(ROOT / "tests" / "fixtures" / "reference"), SynthConfig(seed=5, n_people=2000), run)
    build_store(run)
    with MockStore.from_run(run, "t") as s:
        sync_orders(StoreClient(s.url, "t", sleep=lambda x: None), run / "sources" / "store")
    wh = d / "w.duckdb"
    ingest(run, wh, CONTRACTS)
    with duckdb.connect(str(wh)) as con:
        resolve(con)
        expand_deletions(con, CONTRACTS)
        # plant one review case: a deleted row matching a current person on name and zip only
        f, l, z, pk = con.execute("""SELECT first_name, last_name, zip, person_key FROM identity_records
                                     WHERE first_name IS NOT NULL AND zip IS NOT NULL ORDER BY unique_id LIMIT 1""").fetchone()
        con.execute("INSERT INTO deletion_tombstones VALUES ('planted', ?, 'test', 'R9999999', 'strong', now())",
                    tombstones.keys_from_parts(f, l, None, None, z, None))
        expand_deletions(con, CONTRACTS)
        load_reference_tables(con, ROOT / "tests" / "fixtures" / "reference")
    build(wh)
    return {"run": run, "wh": wh, "review_pk": pk}


@pytest.fixture
def ctx(world, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    for p in ("README.md", "docs", "skills", "evals", "sources"):
        (shutil.copytree if (ROOT / p).is_dir() else shutil.copy)(ROOT / p, repo / p)
    wh = tmp_path / "w.duckdb"
    shutil.copy(world["wh"], wh)
    make_runbook_stale(repo)
    c = Context(wh, repo, repo / "sources", tmp_path / "agent.duckdb", roles=ROLES)
    yield c
    if c.agent_con is not None:
        c.agent_con.close()


def make_runbook_stale(repo: Path) -> None:
    """Put the old, wrong passage back into this copy of the docs, so the test does not depend on
    whether the real runbook is currently stale (the agent fixed the real one)."""
    import yaml
    c = next(c for c in yaml.safe_load((repo / "docs" / "claims.yaml").read_text())["claims"]
             if c["id"] == "deletion_is_exact_match_only")
    p = repo / c["doc"]
    text = p.read_text()
    if c["text"] not in text:
        assert text.count(c["replacement"]) == 1
        p.write_text(text.replace(c["replacement"], c["text"]))


# ----------------------------------------------------------------- knowledge
def test_knowledge_eval_on_the_held_out_half():
    """Measured on the test half only; the refusal threshold was chosen on the dev half."""
    ix, items = Index.from_repo(ROOT), evals.load(ROOT / "evals" / "knowledge.yaml")
    r = evals.run(ix, items, split="test")
    right, total = map(int, r["answered_correctly"].split("/"))
    refused, should = map(int, r["refused_when_it_should"].split("/"))
    # Measured floor, not a goal: two held-out questions the documents cannot answer still get an
    # answer, because they share words with a section (a 401(k) "match", an "AWS account" in the lake
    # section). Lexical refusal weakens as documents grow; model mode is the fix, measured separately.
    assert should >= 9 and refused >= 7, r["failures"]
    assert total >= 24 and right / total >= 0.75, r["failures"]


def test_the_refusal_threshold_is_the_one_the_dev_half_chooses():
    from plumbline.agents.answer import MIN_COVERAGE
    best, _ = evals.choose_threshold(Index.from_repo(ROOT), evals.load(ROOT / "evals" / "knowledge.yaml"))
    assert best == MIN_COVERAGE


def test_the_documents_never_quote_the_eval_questions():
    """Regression: a README draft quoted two unanswerable eval questions as examples; the agent then
    found them in the README and stopped refusing them. A test set printed in the textbook is no test."""
    corpus = " ".join(c.text.lower() for c in Index.from_repo(ROOT).chunks)
    leaked = [i["q"] for i in evals.load(ROOT / "evals" / "knowledge.yaml")
              if i.get("refuse") and i["q"].lower().rstrip("?") in corpus]
    assert not leaked, leaked


def test_retrieval_finds_the_right_section():
    ix = Index.from_repo(ROOT)
    items = [i for i in evals.load(ROOT / "evals" / "knowledge.yaml") if not i.get("refuse")]
    hit = sum(any(c.source.startswith(e) for c, _, _ in ix.search(i["q"], 3) for e in i["source"]) for i in items)
    assert hit >= 20, f"right section in the top 3 for only {hit} of {len(items)}"


def test_words_the_documents_never_use_count_against_coverage():
    """Regression: unseen words were dropped, so 'parental leave policy' looked fully covered."""
    ix = Index.from_repo(ROOT)
    assert ix.search("what is our parental leave policy", 1)[0][2] < 0.5
    assert answer("what is our parental leave policy", ix).refused


def test_routing():
    r = evals.run_routing(load_skills(ROOT), ROOT / "evals" / "routing.yaml")
    assert not r["failures"], r["failures"]


class FakeModel(ChatModel):
    def __init__(self, reply):
        self.reply, self.calls = reply, 0

    def complete(self, system, user, max_tokens=500):
        self.calls += 1
        return Completion(self.reply, 100, 20, 0.001)


@pytest.mark.parametrize("reply,mode,why", [
    ("Rerun the same command [1]. Nothing partial is left behind [1].", "model", None),
    ("Rerun the same command. It is safe [1].", "quoted", "without a citation"),
    ("Rerun it [7].", "quoted", "not given"),
    ("NOT_IN_SOURCES", "refused", None),
])
def test_model_answers_are_checked_before_anyone_sees_them(reply, mode, why):
    a = answer("A load crashed halfway through. How do I recover?", Index.from_repo(ROOT), model=FakeModel(reply))
    assert a.mode == mode
    if why:
        assert why in a.note and "[docs/runbooks" in a.text      # the quoted, cited answer was shown instead
    if mode == "model":
        assert "[docs/runbooks/ingestion.md#4-a-load-crashed-halfway]" in a.text


def test_the_model_is_never_asked_when_the_documents_have_nothing():
    m = FakeModel("Parental leave is 12 weeks [1].")
    a = answer("What is our parental leave policy?", Index.from_repo(ROOT), model=m)
    assert a.refused and m.calls == 0


def _http_error(code, headers=None):
    return urllib.error.HTTPError("u", code, "x", headers or {}, io.BytesIO(b""))


def test_model_client_retries_throttling_but_not_a_bad_key():
    calls, sleeps = [], []

    def opener(req, timeout):
        calls.append(req)
        if len(calls) == 1:
            raise _http_error(429, {"Retry-After": "3"})
        return io.BytesIO(json.dumps({"choices": [{"message": {"content": "ok [1]."}}],
                                      "usage": {"prompt_tokens": 10, "completion_tokens": 2}}).encode())
    m = ChatModel("m", "secret-key-123", opener=opener, sleep=sleeps.append, cost_in=1.0, cost_out=2.0)
    c = m.complete("s", "u")
    assert c.text == "ok [1]." and sleeps == [3.0] and c.cost == pytest.approx(0.014)
    assert "secret-key-123" not in repr(m)
    bad = ChatModel("m", "k", opener=lambda r, timeout: (_ for _ in ()).throw(_http_error(401)), sleep=sleeps.append)
    with pytest.raises(LLMError, match="401"):
        bad.complete("s", "u")


# ----------------------------------------------------------------- skills
def test_skill_library_refuses_bad_skills(tmp_path):
    shutil.copytree(ROOT / "skills", tmp_path / "skills")
    shutil.copytree(ROOT / "evals", tmp_path / "evals")
    load_skills(tmp_path)
    bad = {"no owner": "name: x\ndescription: d\ntools: []\neval: evals/routing.yaml\n",
           "unknown tool": "name: x\nowner: o\ndescription: d\ntools: [drop_table]\neval: evals/routing.yaml\n",
           "no eval": "name: x\nowner: o\ndescription: d\ntools: []\neval: evals/missing.yaml\n",
           "intent outside its tools": "name: x\nowner: o\ndescription: d\ntools: [ingest_status]\neval: evals/routing.yaml\n"
                                       "intents:\n  - tool: resolve_deletion_review\n    when: ['x']\n"}
    for why, text in bad.items():
        (tmp_path / "skills" / "zz.yaml").write_text(text)
        with pytest.raises(SkillError):
            load_skills(tmp_path)


# ----------------------------------------------------------------- tools, approvals, audit
def test_read_tools_return_no_personal_data(ctx, world):
    ctx.open()
    with duckdb.connect(str(world["wh"]), read_only=True) as con:
        names = {r[0] for r in con.execute("SELECT DISTINCT lower(last_name) FROM bronze_crm WHERE length(last_name) > 4").fetchall()}
    for name in ("ingest_status", "deletion_review_queue", "model_status", "data_products_status", "check_doc_claims"):
        out = json.dumps(TOOLS[name].run(ctx, {}), default=str).lower()
        assert "@" not in out and not re.search(r"555\d{7}", out), name
        assert not [n for n in names if re.search(rf"\b{re.escape(n)}\b", out)][:1], name


def test_read_tools_cannot_write(ctx):
    """They open the warehouse read-only, so even a bug in a read tool cannot change data."""
    with pytest.raises(duckdb.InvalidInputException):
        from plumbline.agents.tools import _ro
        with _ro(ctx) as con:
            con.execute("DELETE FROM ingest_ledger")


def test_a_write_needs_a_second_person_with_the_role(ctx, world):
    pk = world["review_pk"]
    r = handle(ctx, "U_PRIV", f"keep {pk}, it is a namesake")
    assert r.proposals and r.tool == "resolve_deletion_review"
    pid = r.proposals[0]
    assert handle(ctx, "U_PRIV", f"approve {pid}").refused                 # cannot approve your own request
    assert handle(ctx, "U_DOCS", f"approve {pid}").refused                 # wrong role
    done = handle(ctx, "U_PRIV2", f"approve {pid}")
    assert "approved and done" in done.text
    again = handle(ctx, "U_PRIV2", f"approve {pid}")
    assert "already executed" in again.text                                # runs once
    queue = TOOLS["deletion_review_queue"].run(ctx, {})
    assert pk not in {i["person_key"] for i in queue["items"]}
    outcomes = [o for (o,) in ctx.agent_con.execute(
        "SELECT outcome FROM ops.audit WHERE proposal_id = ? ORDER BY logged_at", [pid]).fetchall()]
    assert outcomes == ["pending", "refused", "refused", "executed", "already_executed"]


def test_an_approved_deletion_removes_the_person_everywhere(ctx, world):
    pk = world["review_pk"]
    with duckdb.connect(str(ctx.warehouse), read_only=True) as con:
        before = con.execute("SELECT count(*) FROM identity_map WHERE person_key = ?", [pk]).fetchone()[0]
    assert before > 0
    pid = handle(ctx, "U_PRIV", f"delete {pk}, the store order confirms it").proposals[0]
    assert "approved and done" in handle(ctx, "U_PRIV2", f"approve {pid}").text
    with duckdb.connect(str(ctx.warehouse), read_only=True) as con:
        assert con.execute("SELECT count(*) FROM identity_map WHERE person_key = ?", [pk]).fetchone()[0] == 0


def test_a_rejected_proposal_changes_nothing(ctx, world):
    pk = world["review_pk"]
    with duckdb.connect(str(ctx.warehouse), read_only=True) as con:
        before = con.execute("SELECT count(*) FROM identity_map").fetchone()[0]
    pid = handle(ctx, "U_PRIV", f"delete {pk}").proposals[0]
    assert "rejected" in handle(ctx, "U_PRIV2", f"reject {pid}").text
    assert "already rejected" in handle(ctx, "U_PRIV2", f"approve {pid}").text
    with duckdb.connect(str(ctx.warehouse), read_only=True) as con:
        assert con.execute("SELECT count(*) FROM identity_map").fetchone()[0] == before


def test_the_audit_log_keeps_no_arguments_in_clear(ctx, world):
    pk = world["review_pk"]
    handle(ctx, "U_PRIV", f"keep {pk}")
    rows = ctx.agent_con.execute("SELECT * FROM ops.audit").df()
    assert pk not in rows.drop(columns=["proposal_id"]).to_json(date_format="iso")
    assert rows.args_sha256.dropna().str.fullmatch(r"[0-9a-f]{64}").all()


def test_stale_docs_are_found_proposed_and_fixed_only_after_approval(ctx):
    runbook = ctx.repo / "docs" / "runbooks" / "ingestion.md"
    original = runbook.read_text()
    r = handle(ctx, "U_OPS", "are the docs still accurate?")
    assert "deletion_is_exact_match_only" in r.text
    p = handle(ctx, "U_OPS", "fix the stale docs")
    assert p.proposals and runbook.read_text() == original                 # proposed, not applied
    pid = _proposal_for(p, "deletion_is_exact_match_only")
    assert handle(ctx, "U_OPS", f"approve {pid}").refused                  # own request
    assert "approved and done" in handle(ctx, "U_DOCS", f"approve {pid}").text
    assert "person-level" in runbook.read_text() and "Phase 2 identity matching closes this" not in runbook.read_text()
    assert "deletion_is_exact_match_only" not in handle(ctx, "U_OPS", "are the docs still accurate?").text


def _proposal_for(reply, claim_id: str) -> str:
    return re.search(rf"Proposed (P[0-9a-f]{{10}}): [^\n]*\({claim_id}\)", reply.text).group(1)


def test_a_doc_edit_refuses_if_the_passage_changed_meanwhile(ctx):
    pid = _proposal_for(handle(ctx, "U_OPS", "fix the stale docs"), "deletion_is_exact_match_only")
    runbook = ctx.repo / "docs" / "runbooks" / "ingestion.md"
    runbook.write_text(runbook.read_text().replace("Known limit:", "Known limitation:"))   # someone edited it
    r = handle(ctx, "U_DOCS", f"approve {pid}")
    assert "failed and changed nothing" in r.text


def test_doc_edits_stay_inside_the_repository(ctx):
    with pytest.raises(ValueError, match="inside the repository"):
        TOOLS["update_doc"].run(ctx, {"path": "../../etc/passwd.md", "old": "a", "new": "b"})
    with pytest.raises(ValueError, match="Markdown"):
        TOOLS["update_doc"].run(ctx, {"path": "pyproject.toml", "old": "a", "new": "b"})


def test_a_wrong_number_in_the_docs_is_corrected_from_the_warehouse(ctx):
    runbook = ctx.repo / "docs" / "runbooks" / "ingestion.md"
    with duckdb.connect(str(ctx.warehouse), read_only=True) as con:
        n = con.execute("SELECT count(DISTINCT person_key) FROM deletion_review").fetchone()[0]
    claims = ctx.repo / "docs" / "claims.yaml"
    wrong = n + 5
    runbook.write_text(runbook.read_text().replace("Seed 7 baseline: 2 persons queued", f"Seed 7 baseline: {wrong} persons queued"))
    claims.write_text(claims.read_text().replace("Seed 7 baseline: 2 persons queued", f"Seed 7 baseline: {wrong} persons queued"))
    stale = {s["id"]: s for s in TOOLS["check_doc_claims"].run(ctx, {})["stale"]}
    assert stale["review_queue_baseline"]["edit"]["new"].startswith(f"Seed 7 baseline: {n} persons queued")


def test_the_grader_ignores_line_breaks_and_refuses_terms_yaml_turned_into_numbers(tmp_path):
    """Regressions: a right answer whose phrase wrapped across a line was graded wrong, and YAML read
    `off` as false and `[33,287]` as two numbers, so those terms could never match."""
    from types import SimpleNamespace
    a = SimpleNamespace(refused=False, sources=["docs/x.md#y"], text="the corrected\nre-send loads normally")
    assert evals.grade({"source": ["docs/x.md"], "terms": ["corrected re-send"]}, a)["ok"]
    bad = tmp_path / "e.yaml"
    bad.write_text("items:\n  - q: is it on?\n    source: [docs/x.md]\n    terms: [off]\n")
    with pytest.raises(ValueError, match="quote the terms"):
        evals.load(bad)


def test_the_readme_reports_the_eval_numbers_the_documents_give_today():
    """The README is one of the documents the agent searches, so editing it moves the eval. This
    keeps its table from going stale: a first draft said 19 of 24 after an edit had made it 18."""
    from plumbline.agents import questionnaire as Q
    readme = (ROOT / "README.md").read_text()
    kix, k = Index.from_repo(ROOT), evals.load(ROOT / "evals" / "knowledge.yaml")
    qix, q = Q.index(ROOT), evals.load(ROOT / "evals" / "questionnaire.yaml")
    kd, kt = evals.run(kix, k, split="dev"), evals.run(kix, k, split="test")
    qd, qt = (evals.run(qix, q, min_coverage=Q.MIN_COVERAGE, split=s) for s in ("dev", "test"))
    ans = [i for i in k if not i.get("refuse")]
    top3 = sum(any(c.source.startswith(e) for c, *_ in kix.search(i["q"], 3) for e in i["source"]) for i in ans)

    def of(x):
        return x.replace("/", " of ")
    expected = [f"| Right section in the top 3 results | {top3} of {len(ans)} answerable questions |",
                f"{of(kd['answered_correctly'])} (dev half), **{of(kt['answered_correctly'])} (test half)**",
                f"{of(kd['refused_when_it_should'])} (dev), **{of(kt['refused_when_it_should'])} (test)**",
                f"{of(qd['answered_correctly'])} (dev half), **{of(qt['answered_correctly'])} (test half)**",
                f"{of(qd['refused_when_it_should'])} (dev), **{of(qt['refused_when_it_should'])} (test)**"]
    stale = [e for e in expected if e not in readme]
    assert not stale, stale
