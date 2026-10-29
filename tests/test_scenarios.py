"""Scenario Lab: each injected incident is detected by a real check (never the answer key), fixed only
after a second person with the right role approves, verified independently, and reported from the
run record alone."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from plumbline.app import demo as D
from plumbline.app import scenarios as S

ROLES = {u: set(r) for u, r in __import__("yaml").safe_load((D.REPO / "ops" / "roles.example.yaml").read_text())["users"].items()}
APPROVER = {"admin": "U0ADM0001", "data-platform": "U0OPS0001", "privacy": "U0PRIV001"}


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    return D.build(tmp_path_factory.mktemp("sc"), n_people=2500, seed=11, progress=lambda s: None)


def _no_answer_key(monkeypatch):
    real = pd.read_parquet

    def guarded(path, *a, **k):
        assert "truth" not in Path(str(path)).parts, f"detection read the answer key: {path}"
        return real(path, *a, **k)
    monkeypatch.setattr(pd, "read_parquet", guarded)


@pytest.mark.parametrize("key", list(S.SCENARIOS))
def test_each_incident_is_detected_without_the_answer_key_fixed_after_approval_and_verified(key, world, tmp_path, monkeypatch):
    runs = tmp_path / "runs"
    r = S.start(key, world, runs)
    with monkeypatch.context() as m:
        _no_answer_key(m)
        S.investigate(r, world, runs)
    assert r.status == "proposed" and r.proposal and any(e["phase"] == "detect" for e in r.events)
    detected = r.findings.get("new") or r.findings.get("missing") or r.findings.get("rows_back") or r.findings.get("values_from_the_future")
    assert detected, r.findings
    with pytest.raises(S.NotAllowed):
        S.fix_and_verify(r, world, runs)                        # no approval yet: nothing runs
    S.approve(r, APPROVER[r.proposal["role"]], ROLES)
    S.fix_and_verify(r, world, runs)
    assert r.verification["passed"], r.verification
    again = S.Run.load(runs, r.id)
    assert S.report(again) == S.report(r) and "#### Verification" in S.report(r)


def test_approval_needs_a_second_person_with_the_role_and_runs_once(world, tmp_path):
    runs = tmp_path / "runs"
    r = S.investigate(S.start("interrupted_sync", world, runs), world, runs)
    with pytest.raises(S.NotAllowed, match="never approves its own"):
        S.approve(r, S.AGENT, ROLES | {S.AGENT: {"data-platform"}})       # even if it held the role
    with pytest.raises(S.NotAllowed, match="role"):
        S.approve(r, "U0PRIV001", ROLES)
    S.approve(r, "U0OPS0001", ROLES)
    with pytest.raises(S.NotAllowed, match="already"):
        S.approve(r, "U0OPS0002", ROLES)
    S.fix_and_verify(r, world, runs)
    n = len(r.events)
    S.fix_and_verify(r, world, runs)                            # the same approval never runs twice
    assert len(r.events) == n


def test_a_clean_city_has_no_deletion_violations(world):
    """Regression: the first verification matched any request hash and flagged family members who share
    an inbox with a requester, rows the pipeline's single erasure rule keeps on purpose."""
    assert len(S.StaleRestore._violations(world.warehouse, world.run)) == 0


def test_the_100k_leak_numbers_are_the_readme_numbers():
    import json
    leak = json.loads((D.REPO / "docs" / "results" / "leak_demo.json").read_text())
    readme = (D.REPO / "README.md").read_text()
    assert f"{leak['leaky_auc']:.3f}" in readme and f"{leak['ceiling_auc']:.3f}" in readme and f"{leak['honest_auc']:.3f}" in readme


def test_every_incident_has_someone_who_can_approve_its_fix():
    """The page once crashed: the only data-platform holder was also the proposer, so nobody could approve."""
    import inspect
    proposer = inspect.signature(S.investigate).parameters["proposed_by"].default
    for sc in S.SCENARIOS.values():
        assert any(sc.role in r for u, r in ROLES.items() if u != proposer), sc.key


def test_finished_and_abandoned_runs_do_not_fill_the_disk(world, tmp_path):
    runs = tmp_path / "runs"
    done = S.investigate(S.start("interrupted_sync", world, runs), world, runs)
    S.approve(done, APPROVER[done.proposal["role"]], ROLES)
    S.fix_and_verify(done, world, runs)
    assert not Path(done.work).exists() and (runs / f"{done.id}.json").exists()
    left = S.start("interrupted_sync", world, runs)                    # abandoned after inject
    assert Path(left.work).exists()
    import time
    assert S.prune(runs, now=time.time() + S.STALE_WORK_S + 1) == 1 and not Path(left.work).exists()
