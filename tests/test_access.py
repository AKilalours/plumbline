"""Access provisioning, the security questionnaire and hours retired. None of these touch the
warehouse, so they run on a copy of the repository and an empty agent database."""
from __future__ import annotations

import csv
import re
import shutil
from pathlib import Path

import pytest
import yaml

from plumbline.agents import access, evals, questionnaire, usage
from plumbline.agents.agent import Context, handle
from plumbline.agents.answer import answer

ROOT = Path(__file__).resolve().parents[1]
ROLES = {"U0ADM0001": {"admin"}, "U0ADM0002": {"admin"}, "U0OPS0001": {"data-platform", "docs"}, "U0PRIV001": {"privacy"}}


@pytest.fixture
def ctx(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    for p in ("README.md", "docs", "skills", "evals", "sources"):
        (shutil.copytree if (ROOT / p).is_dir() else shutil.copy)(ROOT / p, repo / p)
    c = Context(tmp_path / "no_warehouse.duckdb", repo, repo / "sources", tmp_path / "agent.duckdb", roles=ROLES)
    yield c
    if c.agent_con is not None:
        c.agent_con.close()


def _pid(reply) -> str:
    assert reply.proposals, reply.text
    return reply.proposals[0]


def test_a_grant_needs_an_admin_who_is_neither_the_requester_nor_the_person(ctx):
    pid = _pid(handle(ctx, "U0ADM0001", "give U0NEW0001 the privacy role"))
    assert handle(ctx, "U0ADM0001", f"approve {pid}").refused                  # own request
    assert handle(ctx, "U0OPS0001", f"approve {pid}").refused                  # not an admin
    assert "approved and done" in handle(ctx, "U0ADM0002", f"approve {pid}").text
    assert ctx.current_roles()["U0NEW0001"] == {"privacy"}


def test_nobody_approves_a_change_to_their_own_access(ctx):
    pid = _pid(handle(ctx, "U0OPS0001", "give U0ADM0002 the docs role"))
    r = handle(ctx, "U0ADM0002", f"approve {pid}")
    assert r.refused and "own access" in r.text
    assert "docs" not in ctx.current_roles()["U0ADM0002"]


def test_the_last_admin_cannot_be_removed(ctx):
    pid = _pid(handle(ctx, "U0OPS0001", "revoke the admin role from U0ADM0002"))
    assert "approved and done" in handle(ctx, "U0ADM0001", f"approve {pid}").text
    pid = _pid(handle(ctx, "U0OPS0001", "revoke the admin role from U0ADM0001"))
    # U0ADM0001 is now the only admin; U0ADM0001 approving is refused as their own access, and no one else can
    assert handle(ctx, "U0ADM0001", f"approve {pid}").refused
    with pytest.raises(ValueError, match="last admin"):
        access.revoke(ctx.agent_con, "U0ADM0001", ["admin"], "U0XXX0001", None)
    assert "admin" in ctx.current_roles()["U0ADM0001"]


def test_offboarding_removes_every_role_and_closes_open_requests(ctx):
    open_pid = _pid(handle(ctx, "U0OPS0001", "give U0NEW0001 the docs role"))     # U0OPS0001's own pending request
    pid = _pid(handle(ctx, "U0ADM0001", "offboard U0OPS0001"))
    r = handle(ctx, "U0ADM0002", f"approve {pid}")
    assert "approved and done" in r.text
    assert "U0OPS0001" not in ctx.current_roles()
    status = ctx.agent_con.execute("SELECT status FROM ops.proposals WHERE id = ?", [open_pid]).fetchone()[0]
    assert status == "rejected"
    assert "already rejected" in handle(ctx, "U0ADM0002", f"approve {open_pid}").text


def test_the_access_record_writes_itself(ctx):
    g = _pid(handle(ctx, "U0ADM0001", "give U0NEW0001 the privacy role"))
    handle(ctx, "U0ADM0002", f"approve {g}")
    r = _pid(handle(ctx, "U0ADM0001", "revoke the privacy role from U0NEW0001"))
    handle(ctx, "U0ADM0002", f"approve {r}")
    row = ctx.agent_con.execute("""SELECT granted_by, via_proposal, revoked_by, revoked_via FROM ops.access
                                   WHERE user_id = 'U0NEW0001'""").fetchall()
    assert row == [("U0ADM0002", g, "U0ADM0002", r)]
    seeded = ctx.agent_con.execute("SELECT DISTINCT granted_by FROM ops.access WHERE user_id = 'U0OPS0001'").fetchall()
    assert seeded == [("bootstrap",)]


def test_a_role_granted_by_approval_counts_in_later_approvals(ctx):
    g = _pid(handle(ctx, "U0ADM0001", "give U0NEW0001 the privacy role"))
    handle(ctx, "U0ADM0002", f"approve {g}")
    pid = _pid(handle(ctx, "U0ADM0001", "give U0OTH0001 the docs role"))
    assert handle(ctx, "U0NEW0001", f"approve {pid}").refused                  # privacy is not admin
    g2 = _pid(handle(ctx, "U0ADM0001", "give U0NEW0001 the admin role"))
    handle(ctx, "U0ADM0002", f"approve {g2}")
    assert "approved and done" in handle(ctx, "U0NEW0001", f"approve {pid}").text


def test_the_yaml_only_seeds_an_empty_record(ctx, tmp_path):
    ctx.open()
    g = _pid(handle(ctx, "U0ADM0001", "revoke the privacy role from U0PRIV001"))
    handle(ctx, "U0ADM0002", f"approve {g}")
    ctx.agent_con.close()
    again = Context(ctx.warehouse, ctx.repo, ctx.contracts, ctx.agent_db, roles=ROLES)   # yaml still lists U0PRIV001
    assert "U0PRIV001" not in again.current_roles()
    again.agent_con.close()
    ctx.agent_con = None


# --- security questionnaire -------------------------------------------------------------------

QITEMS = evals.load(ROOT / "evals" / "questionnaire.yaml")


def test_questionnaire_threshold_is_chosen_on_the_dev_half():
    ix = questionnaire.index(ROOT)
    best, _ = evals.choose_threshold(ix, QITEMS, grid=questionnaire.GRID)
    assert best == questionnaire.MIN_COVERAGE


def test_questionnaire_on_the_held_out_half():
    r = evals.run(questionnaire.index(ROOT), QITEMS, min_coverage=questionnaire.MIN_COVERAGE, split="test")
    refuse = sum(1 for i in QITEMS if i["split"] == "test" and i.get("refuse"))
    assert r["refused_when_it_should"] == f"{refuse}/{refuse}"
    n, d = map(int, r["answered_correctly"].split("/"))
    assert n / d >= 0.7, r


def test_the_controls_page_never_quotes_the_questionnaire_eval():
    text = " ".join(p.read_text().lower() for p in [ROOT / "docs" / "security.md", *(ROOT / "docs" / "runbooks").glob("*.md")])
    for it in QITEMS:
        assert it["q"].lower().rstrip("?") not in text, it["q"]


def test_a_control_we_do_not_have_is_left_for_a_person():
    a = answer("Do you hold an ISO 27001 certificate, and when was it last audited?", questionnaire.index(ROOT),
               min_coverage=questionnaire.MIN_COVERAGE)
    assert a.refused


def test_questionnaire_csv_in_and_out(ctx, tmp_path):
    src, dst = tmp_path / "in.csv", tmp_path / "out.csv"
    with open(src, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["question"])
        w.writerow(["How do you handle a request to delete a person's data?"])
        w.writerow(["Do you hold an ISO 27001 certificate, and when was it last audited?"])
    out = questionnaire.run_file(ctx, src, dst, "U0OPS0001")
    assert out["questions"] == 2 and out["drafted"] == 1 and out["needs_a_person"] == 1
    rows = list(csv.DictReader(open(dst)))
    assert rows[0]["status"] == "drafted" and "docs/security.md#" in rows[0]["sources"]
    assert rows[1]["status"] == "needs a person" and rows[1]["answer"] == ""
    logged = ctx.agent_con.execute("SELECT count(*) FROM ops.audit WHERE skill = 'security_questionnaire'").fetchone()[0]
    assert logged == 2


# --- hours retired ----------------------------------------------------------------------------

def test_hours_retired_counts_only_successful_uses(ctx):
    handle(ctx, "U0OPS0001", "who has the privacy role?")                     # read tool, ok: 10 min
    handle(ctx, "U0OPS0001", "What is our parental leave policy?")            # refused: nothing
    pid = _pid(handle(ctx, "U0ADM0001", "give U0NEW0001 the docs role"))          # proposal alone: nothing
    handle(ctx, "U0ADM0002", f"approve {pid}")                               # executed: 10 min
    r = usage.report(ctx.agent_con, ctx.skills)
    h = {x["skill"]: x for x in r["hours_retired"]["by_skill"]}
    assert h["access_admin"]["successful_uses"] == 2
    assert h["access_admin"]["hours_retired"] == round(2 * 10 / 60, 2)
    assert "knowledge_qa" not in h
    assert r["hours_retired"]["total"] == h["access_admin"]["hours_retired"]


def test_every_skill_states_what_its_baseline_measures():
    for p in (ROOT / "skills").glob("*.yaml"):
        d = yaml.safe_load(p.read_text())
        assert d.get("baseline_minutes") and d.get("baseline_note"), p.name


def test_a_decision_with_a_bad_id_is_refused_not_answered_from_the_docs(ctx):
    """Regression: `approve P<id from above>` was routed to the docs and answered with the runbook's
    approval bullets, which reads like it worked."""
    pid = _pid(handle(ctx, "U0OPS0001", "give U0NEW0001 the privacy role"))
    r = handle(ctx, "U0ADM0001", "approve P<id from above>")
    assert r.refused and r.skill == "approvals" and "not a proposal id" in r.text and not r.sources
    status = ctx.agent_con.execute("SELECT status FROM ops.proposals WHERE id = ?", [pid]).fetchone()[0]
    assert status == "pending"


def test_the_usage_report_is_valid_json_with_pending_proposals(ctx):
    """Regression: a proposal nobody has decided gave a median of NaN, printed as NaN, which is not JSON."""
    import json
    handle(ctx, "U0OPS0001", "give U0NEW0001 the privacy role")
    json.dumps(usage.report(ctx.agent_con, ctx.skills), allow_nan=False, default=str)
