"""A contract change that would break its consumers fails, unless the contract's version goes up."""
from __future__ import annotations

import subprocess
from pathlib import Path

import yaml

from plumbline.ingest import contract_check as CC

BASE = {"name": "crm", "key": ["crm_id"], "latest_by": ["updated_at"], "columns": {
    "crm_id": {"type": "string", "required": True}, "email": {"type": "string", "optional": True},
    "zip": {"type": "string", "pattern": "^\\d{5}$"}}}


def _with(**changes):
    c = yaml.safe_load(yaml.safe_dump(BASE))
    for k, v in changes.items():
        c[k] = v
    return c


def test_a_new_optional_column_or_a_looser_rule_is_compatible():
    new = _with(columns={**BASE["columns"], "plan": {"type": "string", "optional": True},
                         "zip": {"type": "string", "pattern": "^\\d{5}(-\\d{4})?$"}})
    assert CC.check(BASE, new) == ([], True)


def test_each_kind_of_breaking_change_is_named():
    cases = {
        "column email removed": _with(columns={k: v for k, v in BASE["columns"].items() if k != "email"}),
        "column zip type string -> int": _with(columns={**BASE["columns"], "zip": {"type": "int"}}),
        "column email made required": _with(columns={**BASE["columns"], "email": {"type": "string", "optional": True, "required": True}}),
        "column email no longer optional": _with(columns={**BASE["columns"], "email": {"type": "string"}}),
        "new column plan must be in every delivery": _with(columns={**BASE["columns"], "plan": {"type": "string"}}),
        "key": _with(key=["email"]),
        "latest_by": _with(latest_by=["crm_id"]),
        "deleted_when": _with(deleted_when={"column": "_op", "equals": "d"}),
    }
    for want, new in cases.items():
        found, ok = CC.check(BASE, new)
        assert not ok and any(f.startswith(want) for f in found), (want, found)


def test_a_breaking_change_passes_when_the_version_goes_up():
    new = _with(version=2, columns={k: v for k, v in BASE["columns"].items() if k != "email"})
    assert CC.check(BASE, new) == (["column email removed"], True)


def test_a_removed_contract_is_breaking():
    assert CC.check(BASE, None) == (["the contract was removed"], False)


def test_against_git_a_breaking_edit_fails_and_the_untouched_contracts_pass(tmp_path, monkeypatch):
    repo = tmp_path / "r"
    (repo / "sources").mkdir(parents=True)
    run = lambda *a: subprocess.run(["git", *a], cwd=repo, check=True, capture_output=True)
    run("init", "-q")
    run("config", "user.email", "t@x.test")
    run("config", "user.name", "t")
    (repo / "sources" / "crm.yaml").write_text(yaml.safe_dump(BASE))
    (repo / "sources" / "store.yaml").write_text(yaml.safe_dump({**BASE, "name": "store"}))
    run("add", ".")
    run("commit", "-qm", "base")
    monkeypatch.setattr(CC, "ROOT", repo)
    assert CC.main(["--base", "HEAD"]) == 0
    (repo / "sources" / "crm.yaml").write_text(yaml.safe_dump(_with(columns={"crm_id": BASE["columns"]["crm_id"]})))
    assert CC.main(["--base", "HEAD"]) == 1
    (repo / "sources" / "crm.yaml").write_text(yaml.safe_dump(_with(version=2, columns={"crm_id": BASE["columns"]["crm_id"]})))
    assert CC.main(["--base", "HEAD"]) == 0
