"""Score the golden person and its history against the answer key, next to simple alternatives."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from ..identity.standardize import clean_name, standardize_street

END = "2025-12-31"


def _addr(street, z):
    h, s, u = standardize_street(street)
    if not (h and s):
        return None
    return f"{h} {s}" + (f" APT {u}" if u else "") + "|" + (z or "")


def _truth_at(hist: pd.DataFrame, date: str) -> pd.DataFrame:
    h = hist[hist.valid_from <= date].sort_values("valid_from")
    return h.groupby("person_id").tail(1).set_index("person_id")


def _owner(con, run: Path) -> pd.Series:
    """person_key -> the true person most of its records belong to."""
    links = pd.concat([pd.read_parquet(run / "truth" / "record_links.parquet"),
                       pd.read_parquet(run / "store" / "record_links.parquet") if (run / "store" / "record_links.parquet").exists() else None])
    m = con.execute("SELECT source, record_id, person_key FROM identity_map WHERE source IN ('crm','vendor','store_order')").df()
    m["source"] = m["source"].replace({"store_order": "store"})
    m = m.merge(links, on=["source", "record_id"])
    return m.groupby("person_key")["person_id"].agg(lambda s: s.mode().iat[0])


def score_products(con, run_dir: str | Path) -> dict:
    run = Path(run_dir)
    owner = _owner(con, run)
    ppl = pd.read_parquet(run / "truth" / "people.parquet").set_index("person_id")
    hist = pd.read_parquet(run / "truth" / "address_history.parquet")
    hist["addr"] = [_addr(s, z) for s, z in zip(hist.street, hist.zipcode)]
    jobs = pd.read_parquet(run / "truth" / "job_history.parquet")
    job_now = jobs[jobs.valid_from <= END].sort_values("valid_from").groupby("person_id").tail(1).set_index("person_id")

    g = con.execute("SELECT * FROM products.dim_person").df()
    g["pid"] = g.person_key.map(owner)
    g = g.dropna(subset=["pid"])
    t = ppl.loc[g.pid]
    now = _truth_at(hist, END)

    def acc(pred, true):
        has = pd.notna(pred).to_numpy()
        ok = (pd.Series(pred).to_numpy() == pd.Series(true).to_numpy()) & has
        return {"filled": round(float(has.mean()), 4), "correct_when_filled": round(float(ok.sum() / max(has.sum(), 1)), 4)}

    golden_addr = (g.address_line + "|" + g.zip.fillna("")).where(g.address_line.notna())
    out = {"persons": len(g), "attributes": {
        "first_name": acc(g.first_name, t.first_name.map(clean_name)),
        "last_name": acc(g.last_name, t.last_name.map(clean_name)),
        "personal_email": acc(g.personal_email, t.personal_email),
        "phone": acc(g.phone, t.phone),
        "current_address": acc(golden_addr, now.addr.reindex(g.pid)),
        "dob": acc(g.dob, t.dob.astype(str)),
        "work_email": acc(g.work_email, job_now.work_email.reindex(g.pid)),
        "job_title": acc(g.job_title, job_now.title.reindex(g.pid)),
    }}

    # address survivorship against the two obvious alternatives
    v = con.execute("""SELECT person_key, value, first_observed_at, last_observed_at, best_trust_rank
                       FROM products.int_attribute_values WHERE attr = 'address'""").df()
    v["pid"] = v.person_key.map(owner)
    v = v.dropna(subset=["pid"])
    truth_now = now.addr.reindex(v.pid).to_numpy()
    v["right"] = v.value.to_numpy() == truth_now

    def rule(order_cols, asc):
        w = v.sort_values(order_cols, ascending=asc).groupby("person_key").head(1)
        return round(float(w.right.mean()), 4)
    reachable = v.groupby("person_key").right.any()
    out["address_ceiling"] = round(float(reachable.mean()), 4)   # true address seen by some source at all
    out["address_rules"] = {
        "plumbline_newest_address_still_confirmed": out["attributes"]["current_address"]["correct_when_filled"],
        "latest_mention_wins": rule(["last_observed_at"], [False]),
        "most_trusted_source_wins": rule(["best_trust_rank", "last_observed_at"], [True, False]),
    }

    # point in time: the address on a past date, from the history table
    h = con.execute("SELECT person_key, address_line, zip, valid_from, valid_to FROM products.dim_person_address_history").df()
    h["pid"] = h.person_key.map(owner)
    h = h.dropna(subset=["pid"])
    h["value"] = h.address_line + "|" + h.zip.fillna("")
    pit = {}
    for d in ("2024-06-30", "2025-03-31", "2025-12-31"):
        ts = pd.Timestamp(d) + pd.Timedelta(days=1)
        a = h[(h.valid_from < ts) & (h.valid_to.isna() | (h.valid_to >= ts))]
        tr = _truth_at(hist, d).addr
        pit[d] = {"persons_known": int(a.person_key.nunique()),
                  "correct": round(float((a.value.to_numpy() == tr.reindex(a.pid).to_numpy()).mean()), 4)}
    out["address_as_of"] = pit

    # life events: detected moves against true moves
    ev = pd.read_parquet(run / "truth" / "events.parquet")
    moves = ev[ev.event_type == "move"].assign(event_date=lambda d: pd.to_datetime(d.event_date))
    det = con.execute("SELECT person_key, detected_at, confidence FROM products.fct_life_events").df()
    det["pid"] = det.person_key.map(owner)
    det = det.dropna(subset=["pid"]).merge(moves[["person_id", "event_date"]], left_on="pid", right_on="person_id", how="left")
    det["lag"] = (det.detected_at - det.event_date).dt.days
    det["hit"] = det.lag.between(-1, 365)
    best = det.sort_values("lag").groupby(["person_key", "detected_at", "confidence"]).agg(
        hit=("hit", "any"), lag=("lag", lambda s: s[s.between(-1, 365)].min())).reset_index()
    covered = moves[moves.person_id.isin(set(g.pid))]
    # the most any detector could find: moves whose new address some source ever reported
    seen = con.execute("SELECT DISTINCT person_key, value FROM products.int_attribute_values WHERE attr = 'address'").df()
    seen["pid"] = seen.person_key.map(owner)
    seen_pairs = set(zip(seen.pid, seen.value))
    hs = hist.sort_values(["person_id", "valid_from"]).assign(prev=lambda d: d.groupby("person_id").addr.shift())
    after = hs.merge(covered[["person_id", "event_date"]].assign(valid_from=lambda d: d.event_date.dt.strftime("%Y-%m-%d")),
                     on=["person_id", "valid_from"])
    # a move is detectable only if some source reported both the address before and the one after
    observable = sum((p, a) in seen_pairs and (p, b) in seen_pairs for p, a, b in zip(after.person_id, after.addr, after.prev))

    def summarize(b, d):
        found = d[d.hit].drop_duplicates(["person_id", "event_date"])
        return {"detected": int(len(b)), "precision": round(float(b.hit.mean()), 4) if len(b) else None,
                "recall": round(len(found) / max(len(covered), 1), 4),
                "recall_of_detectable": round(len(found) / max(observable, 1), 4),
                "median_days_late": float(b.lag.median()) if b.hit.any() else None}
    used = best.confidence.isin(["high", "medium"])
    out["moves"] = {"used_for_segments": summarize(best[used], det[det.confidence.isin(["high", "medium"])]),
                    "all_including_vendor_only": summarize(best, det),
                    "true_moves": int(len(covered)), "observable_by_any_source": int(observable)}

    # households: pairwise against true households
    hh = g[["person_key", "household_key", "pid"]].dropna()
    hh["true_hh"] = hh.pid.map(ppl.household_id)
    from ..identity.evaluate import pairwise_scores
    s = pairwise_scores(hh.set_index("person_key").household_key, hh.set_index("person_key").true_hh)
    out["households"] = {k: s[k] for k in ("precision", "recall", "f1")}

    # B2B2C: both identities of one person linked, both right
    has_work = g.pid.map(job_now.work_email).notna()
    both = (g.personal_email == t.personal_email.to_numpy()) & (g.work_email == g.pid.map(job_now.work_email))
    obs_email = con.execute("SELECT DISTINCT person_key, value FROM products.int_attribute_values WHERE attr = 'work_email'").df()
    seen_work = set(zip(obs_email.person_key, obs_email.value))
    seen_pers = set(map(tuple, con.execute("SELECT DISTINCT person_key, value FROM products.int_attribute_values WHERE attr = 'personal_email'").df().values))
    true_work, true_pers = g.pid.map(job_now.work_email), t.personal_email.to_numpy()
    linkable = pd.Series([(k, w) in seen_work and (k, p) in seen_pers for k, w, p in zip(g.person_key, true_work, true_pers)], index=g.index)
    out["b2b2c"] = {"persons_with_a_true_work_email": int(has_work.sum()),
                    "linkable_both_emails_seen_by_some_source": int(linkable.sum()),
                    "linked_and_both_correct_of_linkable": round(float(both[linkable].mean()), 4) if linkable.any() else None,
                    "linked_and_both_correct_of_all": round(float(both[has_work].mean()), 4)}

    a = con.execute("""SELECT count(*) n, sum(email_ok::int) email, sum(work_email_ok::int) work_email,
                              sum(phone_ok::int) phone, sum(mail_ok::int) mail, sum(recent_mover::int) recent_movers,
                              sum(high_value::int) high_value, sum(lapsed::int) lapsed
                       FROM products.mart_activation_audience""").df().iloc[0]
    out["audience"] = {k: int(v) for k, v in a.items()}
    return out
