"""The generator's contract. Each test names the promise it holds the generator to."""
from __future__ import annotations

import contextlib
import hashlib
import json
import re
from pathlib import Path

import pandas as pd
import pytest

from plumbline.synth.cli import main
from plumbline.synth.config import SynthConfig
from plumbline.synth.generate import generate
from plumbline.synth.reference import ReferenceDataError, load_reference

FIXTURE = Path(__file__).parent / "fixtures" / "reference"
N = 3000


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    d = tmp_path_factory.mktemp("run") / "a"
    m = generate(load_reference(FIXTURE), SynthConfig(seed=11, n_people=N), d)
    return d, m


def _truth(d, name):
    return pd.read_parquet(d / "truth" / f"{name}.parquet")


def _sources(d, source):
    deliveries = json.loads((d / "deliveries.json").read_text())
    return {x["path"]: pd.read_csv(d / x["path"], dtype=str, keep_default_na=False)
            for x in deliveries if x["source"] == source}


def test_same_seed_same_fingerprint(tmp_path):
    ref = load_reference(FIXTURE)
    a = generate(ref, SynthConfig(seed=5, n_people=1500), tmp_path / "a")
    b = generate(ref, SynthConfig(seed=5, n_people=1500), tmp_path / "b")
    assert a["fingerprint"] == b["fingerprint"]
    assert a["hashes"] == b["hashes"]


def test_different_seed_different_fingerprint(tmp_path):
    ref = load_reference(FIXTURE)
    a = generate(ref, SynthConfig(seed=5, n_people=1500), tmp_path / "a")
    b = generate(ref, SynthConfig(seed=6, n_people=1500), tmp_path / "b")
    assert a["fingerprint"] != b["fingerprint"]


def test_files_on_disk_match_recorded_hashes(run):
    d, m = run
    for x in json.loads((d / "deliveries.json").read_text()):
        assert hashlib.sha256((d / x["path"]).read_bytes()).hexdigest() == x["sha256"], x["path"]


def test_people_count_and_unique_ids(run):
    d, _ = run
    people = _truth(d, "people")
    assert len(people) == N
    assert people["person_id"].is_unique


def test_no_deliverable_contact_data(run):
    """Emails only on reserved .test/.example domains; phones only in the fictional 555 area code."""
    d, _ = run
    people = _truth(d, "people")
    assert people["personal_email"].str.match(r"^[^@]+@[a-z0-9.]+\.test$").all()
    assert people["work_email"].dropna().str.endswith(".example").all()
    assert people["phone"].str.match(r"^555\d{7}$").all()
    for source in ("crm", "vendor", "email"):
        for path, df in _sources(d, source).items():
            emails = df["email"][df["email"] != ""].str.lower()
            assert emails.str.endswith((".test", ".example")).all(), path
            if "phone" in df:
                digits = df["phone"][df["phone"] != ""].str.replace(r"\D", "", regex=True).str[-10:]
                assert digits.str.startswith("555").all(), path


def test_owner_columns_are_refused(tmp_path):
    ref = tmp_path / "ref"
    ref.mkdir()
    for f in FIXTURE.iterdir():
        (ref / f.name).write_bytes(f.read_bytes())
    lots = pd.read_csv(ref / "pluto_lots.csv", dtype=str)
    lots["OwnerName"] = "SOMEONE REAL"
    lots.to_csv(ref / "pluto_lots.csv", index=False)
    with pytest.raises(ReferenceDataError, match="owner"):
        load_reference(ref)


def test_cli_refuses_fixture_without_flag(tmp_path, capsys):
    assert main(["--ref", str(FIXTURE), "--out", str(tmp_path), "--n-people", "200"]) == 2
    assert "not 'real'" in capsys.readouterr().err
    assert main(["--ref", str(FIXTURE), "--out", str(tmp_path), "--n-people", "200", "--allow-fixture"]) == 0
    assert (tmp_path / "FIXTURE_seed7_n200" / "manifest.json").exists()
    assert main(["--ref", str(FIXTURE), "--out", str(tmp_path), "--n-people", "200", "--allow-fixture"]) == 2


def test_every_record_links_to_a_real_person(run):
    d, _ = run
    links = _truth(d, "record_links")
    people = set(_truth(d, "people")["person_id"])
    assert set(links["person_id"]) <= people
    assert not links.duplicated(["source", "record_id"]).any()
    linked = {s: set(g["record_id"]) for s, g in links.groupby("source")}
    for source, key in (("crm", "crm_id"), ("vendor", "vendor_record_id"), ("email", "event_id")):
        delivered = set().union(*[set(df[key]) for df in _sources(d, source).values()])
        assert delivered <= linked[source], source


def test_events_inside_window_and_purchase_is_a_move(run):
    d, _ = run
    cfg = SynthConfig()
    ev = _truth(d, "events")
    dates = pd.to_datetime(ev["event_date"])
    assert dates.min() >= pd.Timestamp(cfg.window_start) and dates.max() <= pd.Timestamp(cfg.window_end)
    buys = ev[ev["event_type"] == "home_purchase"][["person_id", "event_date"]]
    moves = ev[ev["event_type"] == "move"][["person_id", "event_date"]]
    assert len(buys) > 0
    assert len(buys.merge(moves, on=["person_id", "event_date"])) == len(buys)
    hist = _truth(d, "address_history")
    after = hist.merge(buys, left_on=["person_id", "valid_from"], right_on=["person_id", "event_date"])
    assert len(after) == len(buys) and after["owner"].all()


def test_purchase_signal_is_planted_as_documented(run):
    """Buyers should skew renting and toward a recent job change, as the data card says."""
    d, _ = run
    hh = _truth(d, "households")
    assert hh["purchase_probability"].between(0, 0.5).all()
    ev = _truth(d, "events")
    people = _truth(d, "people")
    heads = people[people["household_position"] == 0]
    job = set(ev[ev["event_type"] == "job_change"]["person_id"])
    buy_hh = set(ev[ev["event_type"] == "home_purchase"]["household_id"])
    heads = heads.assign(job=heads["person_id"].isin(job), buy=heads["household_id"].isin(buy_hh))
    rate = heads.groupby("job")["buy"].mean()
    assert rate[True] > rate[False]


def test_vendor_schema_drifts_at_configured_week(run):
    d, _ = run
    cfg = SynthConfig()
    for path, df in _sources(d, "vendor").items():
        week = int(re.search(r"week_(\d+)", path).group(1))
        if week >= cfg.vendor_rename_week:
            assert "occupation" in df and "job_title" not in df and "source_confidence" in df, path
        else:
            assert "job_title" in df and "occupation" not in df, path


def test_vendor_repeats_are_exact_copies(run):
    d, _ = run
    frames = _sources(d, "vendor")
    firsts = [df for p, df in sorted(frames.items()) if not p.endswith("_v2.csv")]
    seen: dict[str, str] = {}
    repeats = 0
    for df in firsts:
        for rid, name in zip(df["vendor_record_id"], df["full_name"]):
            if rid in seen:
                repeats += 1
                assert seen[rid] == name
            seen[rid] = name
    assert repeats > 0


def test_corrections_only_move_values_toward_truth(run):
    """CRM change files and the re-sent vendor week may only fix damage, never add new errors."""
    d, _ = run
    crm = _sources(d, "crm")
    full = next(df for p, df in crm.items() if "full" in p).set_index("crm_id")
    links = _truth(d, "record_links").set_index("record_id")
    people = _truth(d, "people").set_index("person_id")
    changes = pd.concat([df for p, df in crm.items() if "changes" in p])
    assert len(changes) > 0
    for r in changes.itertuples(index=False):
        truth_last = people.at[links.at[r.crm_id, "person_id"], "last_name"]
        if full.at[r.crm_id, "last_name"] != r.last_name:
            assert r.last_name == truth_last
    vendor = _sources(d, "vendor")
    v1 = next(df for p, df in vendor.items() if "week_06" in p and not p.endswith("_v2.csv"))
    v2 = next(df for p, df in vendor.items() if p.endswith("_v2.csv"))
    diff = v1["full_name"] != v2["full_name"]
    assert diff.any() and (v1["vendor_record_id"] == v2["vendor_record_id"]).all()


def test_corrected_email_day_has_three_versions(run):
    d, _ = run
    cfg = SynthConfig()
    em = _sources(d, "email")
    vers = sorted(p for p in em if re.search(r"_v[23]\.csv$", p))
    assert len(vers) == 2
    day = re.search(r"(\d{4}-\d{2}-\d{2})_v2", vers[0]).group(1)
    v1, v2, v3 = (em[f"sources/email/email_events_{day}_v{i}.csv"] for i in (1, 2, 3))
    assert v1["event_id"].duplicated().any()
    assert not v2["event_id"].duplicated().any() and not v3["event_id"].duplicated().any()
    assert set(v2["event_id"]) < set(v3["event_id"]) and len(v3) == len(v2) + 10


def test_deletion_requests_are_hashed_and_resolvable(run):
    d, _ = run
    req = next(iter(_sources(d, "privacy").values()))
    assert not req.apply(lambda c: c.str.contains("@")).any().any()
    truth = _truth(d, "deletion_requests").merge(req, on="request_id")
    people = _truth(d, "people").set_index("person_id")
    for r in truth.itertuples(index=False):
        e = people.at[r.person_id, "personal_email"]
        assert hashlib.sha256(e.strip().lower().encode()).hexdigest() == r.email_sha256


def test_damage_rates_near_config(run):
    d, _ = run
    cfg = SynthConfig()
    dmg = _truth(d, "damage_log")
    crm = next(df for p, df in _sources(d, "crm").items() if "full" in p)
    rate = ((dmg["source"] == "crm") & (dmg["field"] == "last_name") & (dmg["kind"] == "typo")).sum() / len(crm)
    assert abs(rate - cfg.typo_rate) < 0.03


def test_works_with_pandas3_string_columns(tmp_path):
    """Regression: pandas 3 stores text as StringDtype, which crashed the date-column check.
    On pandas 2 the same behavior is switched on with future.infer_string."""
    pd_major = int(pd.__version__.split(".")[0])
    ctx = pd.option_context("future.infer_string", True) if pd_major < 3 else contextlib.nullcontext()
    with ctx:
        m = generate(load_reference(FIXTURE), SynthConfig(seed=3, n_people=500), tmp_path / "r")
    assert m["counts"]["people"] == 500
