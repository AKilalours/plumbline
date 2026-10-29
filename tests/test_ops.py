"""The app's operations do real work, and each one checks against the answer key or the data itself."""
from __future__ import annotations

import pytest

from plumbline.app import demo as D
from plumbline.app import ops as O
from plumbline.app import queries as Q


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    return D.build(tmp_path_factory.mktemp("ops"), n_people=2500, seed=11)


def test_every_foundation_check_reports_a_count_its_denominator_and_the_records_behind_it(world):
    with Q.connect(world) as con:
        checks = O.foundation_checks(con, world.run, world.summary)
    assert [c["id"] for c in checks] == ["clean", "stale", "conflicts", "identity", "consent", "deletion"]
    for c in checks:
        assert c["status"] in ("pass", "warn", "fail") and c["unit"] and c["evidence"] is not None
        if c["of"] is not None:
            assert 0 <= c["value"] <= c["of"]
    by = {c["id"]: c for c in checks}
    assert by["consent"]["status"] == "pass" and by["consent"]["value"] == 0
    stale = by["stale"]
    assert stale["value"] == len(stale["evidence"])                 # the number is the list, not a separate claim


def test_match_evidence_explains_every_scored_pair_inside_a_person(world):
    with Q.connect(world) as con:
        o = Q.identity_outcomes(con, world.run)
        keys = o[o.records >= 3].iloc[0]["keys"]
        ev = O.match_evidence(con, keys)
    assert len(ev) and set(ev.columns) >= {"Probability", "Email", "Phone", "Street"}
    assert set(ev[list(O.FIELDS)].stack()) <= {"agree", "differ", "missing"}


def test_split_merge_and_undo_are_rescored_against_the_answer_key(world):
    with Q.connect(world) as con:
        base = O.identity_scores(con, world.run)
        o = Q.identity_outcomes(con, world.run)
        merged = o[o.verdict == "merged"]
        if merged.empty:
            pytest.skip("no wrong merge in this small city")
        pid = merged.person_id.iloc[0]
        stranger = Q.strangers_in(con, world.run, merged.iloc[0]["keys"], pid).iloc[0]
        uid = f"{'store' if stranger.source == 'store' else stranger.source}:{stranger.record_id}"
        e = O.IdentityEdits()
        e.split(uid)                                                # take the stranger out: precision must rise
        assert O.identity_scores(con, world.run, e.overrides)["precision"] > base["precision"]
        keys = list(o.set_index("person_id").loc[pid, "keys"])
        e.merge(con, keys[0], "PKsplit_" + uid)                    # put it back by hand
        assert O.identity_scores(con, world.run, e.overrides)["precision"] == base["precision"]
        assert e.undo() and e.undo() and not e.undo()
        assert O.identity_scores(con, world.run, e.overrides) == base


def test_a_sync_that_is_throttled_errors_drops_and_crashes_still_gets_every_order_once(world):
    r = O.failure_lab(world, p_429=0.25, p_503=0.15, p_drop=0.1, crash_after_page=2)
    assert r["resumed"] and r["retries"] > 0 and r["throttled"] > 0
    assert r["missing"] == 0 and r["received"] == r["expected"] and r["copies"] == r["received"]


@pytest.mark.parametrize("text,flags,channel", [
    ("lapsed high-value customers we can email", {"is_customer": True, "lapsed": True, "high_value": True}, "email_ok"),
    ("people who never bought, by mail", {"is_customer": False}, "mail_ok"),
    ("affluent renters", {"homeowner": False}, None),
])
def test_a_sentence_becomes_rules_that_are_shown_before_they_run(text, flags, channel):
    rules, understood = O.parse_audience(text)
    assert rules["flags"] == flags and rules["channel"] == channel and understood


def test_an_audience_never_reaches_someone_who_opted_out_and_says_why_others_were_left_out(world):
    with Q.connect(world) as con:
        a = O.audience(con, {"flags": {"is_customer": True}, "income_min": None, "channel": "email_ok"})
        assert a["reachable"] + len(a["excluded"]) == a["segment"] <= a["population"]
        assert set(a["excluded"].reason) <= {"no email on file", "unsubscribed or asked to be deleted"}
        ok = set(con.execute("SELECT person_key FROM products.mart_activation_audience WHERE email_ok").df().person_key)
        assert set(a["members"].person_key) <= ok
        unknown = con.execute("SELECT count(*) FROM products.mart_activation_audience WHERE homeowner IS NULL").fetchone()[0]
        owners = O.audience(con, {"flags": {"homeowner": True}})["segment"]
        renters = O.audience(con, {"flags": {"homeowner": False}})["segment"]
        assert owners + renters + unknown == a["population"]           # unknown is neither
        assert len(O.provenance(con, a["members"].person_key.iloc[0])) > 0
        with pytest.raises(ValueError):
            O.audience(con, {"flags": {"person_key": True}})


def test_replaying_every_original_file_after_a_deletion_brings_nobody_back(world):
    with Q.connect(world) as con:
        c = Q.deletion_candidates(con, world.run, Q.identity_outcomes(con, world.run))
    r = O.replay_after_deletion(world, c[0])
    assert r["deleted"] > 0 and r["files_redelivered"] > 0 and r["came_back"] == 0


def test_the_activity_log_keeps_every_action_newest_first(tmp_path):
    log = O.ActivityLog(tmp_path / "a.jsonl")
    log.add("visitor", "split", {"precision": 0.99})
    log.add("visitor", "undo", "ok")
    r = log.recent()
    assert list(r.action) == ["undo", "split"] and set(r.columns) == {"at", "who", "action", "result"}


def test_the_activity_log_stays_bounded_and_survives_a_torn_line(tmp_path):
    """A public page appends forever; the first version never trimmed and one half-written line
    (a crash mid-write) made recent() raise for every later visitor."""
    log = O.ActivityLog(tmp_path / "a.jsonl", keep=5)
    for i in range(12):
        log.add("v", f"a{i}", {})
    assert len((tmp_path / "a.jsonl").read_text().splitlines()) <= 10
    with open(tmp_path / "a.jsonl", "a") as f:
        f.write('{"at": "2026-01-01", "who": "v", "act')
    r = log.recent()
    assert r.action.iloc[0] == "a11"
