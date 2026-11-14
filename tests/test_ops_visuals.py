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


def test_break_it_is_built_from_the_recorded_runs_and_says_where_it_broke():
    from datetime import datetime, timezone
    import json
    from pathlib import Path
    from plumbline.app import ops_visuals as OV
    res = Path(__file__).resolve().parents[1] / "docs/results"
    runs = [{"run_at": "2026-11-07T03:45:49Z", "ok": True, "rows": 10, "seconds": 5, "pages": 2},
            {"run_at": "2026-11-07T07:17:28Z", "ok": False, "error": "InvalidInputException: x", "seconds": 5},
            {"run_at": "2026-11-10T23:34:23Z", "ok": True, "rows": 58540, "pages": 60, "seconds": 691.65},
            {"run_at": "2026-11-12T04:36:36Z", "ok": True, "rows": 2438, "pages": 4, "seconds": 41.87,
             "reload_skipped": {"rows": 803457, "timestamp": "2026-11-10T19:43:03.479"}},
            {"run_at": "2026-11-12T13:35:00Z", "ok": True, "pages": 3, "seconds": 9,
             "reconcile": {"checked": 2000, "same": 1990, "changed": 4, "gone": 6, "fields": {"apt_condo_no_s": 4}}}]
    a = {x["key"]: x for x in OV.break_it(runs, json.loads((res / "postgres_cdc.json").read_text()),
                                          json.loads((res / "onboarding_engineers.json").read_text()),
                                          datetime(2026, 11, 13, tzinfo=timezone.utc))}
    assert a["cdc"]["held"] is True and a["cdc"]["read_again"] == 11 and len(a["cdc"]["changes"]) == 18
    assert a["reload"]["held"] == "fixed" and a["reload"]["block"] == 803457 and a["reload"]["after"]["seconds"] == 41.9
    assert a["quiet"]["slices"][0]["fields"] == {"apartment number": 4}
    assert a["fail"]["held"] is False and sum(not r["ok"] for r in a["fail"]["runs"]) == 1
    assert a["stale"]["held"] is False      # a 2.5-day gap between runs: hours over 26 are shown, not hidden
    assert a["stranger"]["held"] is True


def test_on_call_takes_the_refusal_from_the_real_approval_rule():
    import sys
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))
    from test_visuals import _fmt, _run
    from plumbline.app import ops_visuals as OV
    d = OV.oncall(_run(), "Future-dated feature", "Models", "risk", _fmt())
    assert d["headline"] == {"k": "values from the future", "v": 1347}
    assert d["refusal"].startswith("whoever proposed a fix cannot approve it")
