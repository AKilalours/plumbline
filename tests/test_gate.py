"""The delivery quality gate: whole deliveries that break their source's normal shape are held, with the
reason, and load only after a person releases them. Each test breaks one vendor file the way real vendor
files break, and checks nothing from it reaches the warehouse."""
from __future__ import annotations

import csv
import io
import re
import shutil
from pathlib import Path

import duckdb
import pytest

from plumbline.ingest import gate
from plumbline.ingest.contracts import ContractError, load_contract, load_contracts
from plumbline.ingest.engine import ingest
from plumbline.synth.config import SynthConfig
from plumbline.synth.generate import generate
from plumbline.synth.reference import load_reference

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = load_contracts(ROOT / "sources")
LAST = "sources/vendor/vendor_week_08_2025-12-31.csv"


@pytest.fixture(scope="module")
def base_run(tmp_path_factory):
    d = tmp_path_factory.mktemp("synth") / "run"
    generate(load_reference(ROOT / "tests" / "fixtures" / "reference"), SynthConfig(seed=21, n_people=1500), d)
    return d


@pytest.fixture
def run(base_run, tmp_path):
    d = tmp_path / "run"
    shutil.copytree(base_run, d)
    return d


def _rewrite(path: Path, change) -> None:
    rows = list(csv.DictReader(io.StringIO(path.read_text())))
    fields = list(rows[0].keys())
    rows = change(rows)
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=fields, lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    path.write_text(buf.getvalue())


def _status(wh, path):
    with duckdb.connect(str(wh), read_only=True) as con:
        return con.execute("SELECT status, note, delivery_id FROM ingest_ledger WHERE path = ?", [path]).fetchone()


def test_the_three_file_sources_are_gated_and_the_normal_world_holds_nothing(run, tmp_path):
    assert {n for n, c in CONTRACTS.items() if c.gate} == {"crm", "vendor", "email"}
    r = ingest(run, tmp_path / "w.duckdb", CONTRACTS)
    assert r.totals()["deliveries_held"] == 0 and r.totals()["deliveries_loaded"] > 20


def test_a_truncated_vendor_file_is_held_and_nothing_from_it_is_stored(run, tmp_path):
    assert (run / LAST).exists()
    _rewrite(run / LAST, lambda rows: rows[: len(rows) * 2 // 5])        # the export stopped at 40%
    wh = tmp_path / "w.duckdb"
    ingest(run, wh, CONTRACTS)
    status, note, did = _status(wh, LAST)
    assert status == "held" and re.search(r"rows against a usual [\d,]+ \(0\.4\dx, allowed 0\.5x to 2x\)", note), note
    with duckdb.connect(str(wh), read_only=True) as con:
        assert con.execute("SELECT count(*) FROM delivery_rows WHERE delivery_id = ?", [did]).fetchone()[0] == 0
        assert con.execute("SELECT count(*) FROM quarantine WHERE delivery_id = ?", [did]).fetchone()[0] == 0
        assert con.execute("SELECT count(*) FROM bronze_vendor WHERE _first_delivery_id = ?", [did]).fetchone()[0] == 0
        (h,) = gate.held(con)
    assert h["path"] == LAST and h["reasons"]
    assert ingest(run, wh, CONTRACTS).totals()["deliveries_skipped_seen_before"] > 0        # a rerun does not retry it
    assert _status(wh, LAST)[0] == "held"


def test_a_released_delivery_loads_on_the_next_run_and_the_release_is_recorded(run, tmp_path):
    _rewrite(run / LAST, lambda rows: rows[: len(rows) * 2 // 5])
    wh = tmp_path / "w.duckdb"
    ingest(run, wh, CONTRACTS)
    did = _status(wh, LAST)[2]
    with pytest.raises(ValueError, match="who"):
        gate.release(str(wh), did, by="", reason="looks fine")
    gate.release(str(wh), did, by="Ana (data ops)", reason="vendor confirmed a smaller file this week")
    ingest(run, wh, CONTRACTS)
    assert _status(wh, LAST)[0] == "loaded"
    with duckdb.connect(str(wh), read_only=True) as con:
        assert con.execute("SELECT by_whom, reason FROM delivery_reviews WHERE delivery_id = ?", [did]).fetchone() == \
            ("Ana (data ops)", "vendor confirmed a smaller file this week")
        assert con.execute("SELECT count(*) FROM delivery_rows WHERE delivery_id = ?", [did]).fetchone()[0] > 0
    with pytest.raises(ValueError, match="not a held delivery"):
        gate.release(str(wh), did, by="Ana", reason="again")


def test_a_column_that_suddenly_arrives_empty_is_named(run, tmp_path):
    def blank(rows):
        for i, r in enumerate(rows):
            if i % 20 < 9:                     # 45% of emails gone: a broken join upstream
                r["email"] = ""
        return rows
    _rewrite(run / LAST, blank)
    wh = tmp_path / "w.duckdb"
    ingest(run, wh, CONTRACTS)
    status, note, _ = _status(wh, LAST)
    assert status == "held" and re.search(r"email empty in (4|5)\d% of rows against a usual \d%", note) and "x, allowed" not in note, note


def test_a_spike_in_rows_failing_validation_holds_the_delivery(run, tmp_path):
    def bad_zip(rows):
        for i, r in enumerate(rows):
            if i % 10 < 3:                     # a format change: 30% of zips are now ZIP+4 with a space
                r["zip"] = "1121 1"
        return rows
    _rewrite(run / LAST, bad_zip)
    wh = tmp_path / "w.duckdb"
    ingest(run, wh, CONTRACTS)
    status, note, _ = _status(wh, LAST)
    assert status == "held" and "failed validation against a usual" in note


def test_a_small_change_file_is_compared_with_change_files_not_with_the_full_extract():
    assert gate.family("sources/crm/crm_changes_2025-12-30.csv") == gate.family("sources/crm/crm_changes_2025-12-31.csv")
    assert gate.family("sources/crm/crm_changes_2025-12-30.csv") != gate.family("sources/crm/crm_full_2025-12-29.csv")
    assert gate.family("sources/email/email_events_2025-12-24_v3.csv") == gate.family("sources/email/email_events_2025-12-25_v1.csv")


def test_the_first_deliveries_set_the_baseline_instead_of_being_judged():
    p = gate.profile(10, 0, {"email": 0})
    g = gate.config({})
    assert gate.check(p, [{"rows_in": 1000, "quarantine_share": 0, "empty_share": {"email": 0}}] * 2, g) == []
    assert gate.check(p, [{"rows_in": 1000, "quarantine_share": 0, "empty_share": {"email": 0}}] * 3, g)


@pytest.mark.parametrize("bad", ["gate: {rows: [1.2, 2]}", "gate: {sometimes: 1}", "gate: {baseline: 0}"])
def test_a_contract_with_impossible_gate_settings_is_refused(tmp_path, bad):
    text = (ROOT / "sources" / "vendor.yaml").read_text()
    text = text[: text.index("gate:")] + bad + "\n"
    (tmp_path / "v.yaml").write_text(text)
    with pytest.raises(ContractError, match="gate|unknown gate"):
        load_contract(tmp_path / "v.yaml")
