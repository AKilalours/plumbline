"""The data behind the row flow and the proof wall: sums that must hold, and misses that must stay misses."""
import json
from datetime import datetime, timezone
from pathlib import Path

from plumbline.app import ops_visuals as OV
from test_visuals import _nyc_runs

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 11, 13, 16, tzinfo=timezone.utc)


def test_every_row_nyc_sent_is_in_exactly_one_stream():
    runs = _nyc_runs()
    f = OV.row_flow(runs)
    sent, checked = ({s["key"]: s["n"] for s in src["streams"]} for src in f["sources"])
    assert sum(sent.values()) == sum(int(r.get("rows") or 0) + int(r.get("reread") or 0) for r in runs if "rows_loaded" in r)
    assert sent["quarantined"] == 2 and sum(checked.values()) == 4000 and checked["changed"] == 8
    assert f["skipped"]["rows"] == 803457


def test_a_missed_objective_is_shown_as_missed_and_the_cards_cite_their_evidence():
    cards = OV.proof_cards(_nyc_runs(), json.loads((ROOT / "docs/results/postgres_cdc.json").read_text()),
                           json.loads((ROOT / "docs/results/onboarding_engineers.json").read_text()), NOW)
    by = {c["key"]: c for c in cards}
    assert by["runs"]["visual"]["miss"] is True and "missed" in by["runs"]["visual"]["note"]
    assert by["cdc"]["visual"]["left"] == by["cdc"]["visual"]["right"] and by["cdc"]["visual"]["same"] is True
    assert (by["reload"]["visual"]["left"], by["reload"]["visual"]["right"]) == (817, 42)          # (692 + 942) / 2 stuck vs fixed
    assert [g["ok"] for g in by["onboard"]["visual"]["groups"]] == [4, 4]
    assert all(c["href"].startswith("https://github.com/AKilalours/plumbline/blob/main/") for c in cards)
    for c in cards:
        assert (ROOT / c["href"].split("/blob/main/")[1]).exists(), c["href"]


def test_the_onboarding_record_agrees_with_the_written_result():
    rec = json.loads((ROOT / "docs/results/onboarding_engineers.json").read_text())
    text = (ROOT / "docs/results/onboarding_second_engineer.md").read_text()
    for e in rec["engineers"]:
        assert e["source"].split()[0] in text and f"{e['minutes']} min" in text.replace("minutes", "min")
