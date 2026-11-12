"""The numbers behind each interactive visual, read from the warehouse or a saved result.

Kept apart from the drawing (visuals.py) so a test can check every number against where it came from.
"""
from __future__ import annotations

import random
from pathlib import Path

import pandas as pd

BLUE, NAVY, GOLD, GREEN, RED, GREY = "#2941B9", "#11196F", "#E5B74D", "#1F8A5B", "#C2412D", "#9A9A9A"
SHORT = {"crm": "CRM", "vendor": "Vendor", "email": "Email", "store": "Store", "privacy": "Privacy"}
SOURCE = {"crm": ("CRM", BLUE), "vendor": ("Data vendor", GOLD), "email": ("Email events", "#7C8FE0"),
          "store": ("Online store", NAVY), "privacy": ("Privacy requests", GREY)}
FIELDS = {"First name": "first_name", "Last name": "last_name", "Email": "email", "Phone": "phone",
          "Street": "street", "Zip": "zip", "Born": "dob"}


def row_flow(con) -> dict:
    """Every row each source sent, and where it went. The four outcomes add up to what arrived."""
    df = con.execute("""SELECT source, sum(rows_in) AS rows_in, sum(rows_loaded) AS loaded,
                               sum(coalesce(rows_quarantined, 0)) AS quarantined, sum(coalesce(rows_suppressed, 0)) AS suppressed,
                               sum(coalesce(rows_already_present, 0)) AS already
                        FROM ingest_ledger GROUP BY 1 ORDER BY rows_in DESC""").df()
    outcomes = {"loaded": ("Loaded", GREEN, "kept, newest version wins"), "already": ("Already had it", BLUE, "a resend: stored once"),
                "quarantined": ("Quarantined", GOLD, "held with a reason, never silently dropped"),
                "suppressed": ("Blocked: deleted person", RED, "someone who asked to be forgotten")}
    left = [{"key": r.source, "label": SOURCE.get(r.source, (r.source, GREY))[0], "short": SHORT.get(r.source, r.source), "value": int(r.rows_in),
             "color": SOURCE.get(r.source, (r.source, GREY))[1]} for r in df.itertuples()]
    right = [{"key": k, "label": lab, "value": int(df[k].sum()), "color": c, "note": note}
             for k, (lab, c, note) in outcomes.items() if int(df[k].sum())]
    links = [{"from": r.source, "to": k, "value": int(getattr(r, k)), "live": k in ("quarantined", "suppressed")}
             for r in df.itertuples() for k in outcomes if int(getattr(r, k))]
    return {"name": "Every row", "unit": "rows", "left": left, "right": right, "links": links,
            "hint": "Every row each source sent, accounted for. Hover a stream."}


def people_flow(con) -> dict:
    """Records from each source into resolved people, grouped by how many sources saw the person."""
    df = con.execute("""WITH p AS (SELECT person_key, count(DISTINCT source) AS n FROM identity_records GROUP BY 1)
                        SELECT r.source, p.n, count(*) AS records, count(DISTINCT r.person_key) AS people
                        FROM identity_records r JOIN p USING (person_key) GROUP BY 1, 2""").df()
    persons = con.execute("""SELECT n, count(*) FROM (SELECT person_key, count(DISTINCT source) AS n
                             FROM identity_records GROUP BY 1) GROUP BY 1""").fetchall()
    persons = {int(n): int(c) for n, c in persons}
    tone = {1: GREY, 2: BLUE, 3: GREEN}
    word = {1: "Seen by one source", 2: "Joined across two", 3: "Joined across all three"}
    srcs = df.groupby("source").records.sum().sort_values(ascending=False)
    left = [{"key": s, "label": SOURCE.get(s, (s, GREY))[0], "short": SHORT.get(s, s), "value": int(v), "color": SOURCE.get(s, (s, GREY))[1]}
            for s, v in srcs.items()]
    right = [{"key": f"n{n}", "label": word.get(n, f"{n} sources"), "value": int(df[df.n == n].records.sum()), "color": tone.get(n, NAVY),
              "sub": f"{persons[n]:,} people", "note": f"{persons[n]:,} people"} for n in sorted(persons)]
    links = [{"from": r.source, "to": f"n{int(r.n)}", "value": int(r.records)} for r in df.itertuples()]
    total = sum(persons.values())
    return {"name": "Records to people", "unit": "records", "left": left, "right": right, "links": links, "people": total,
            "hint": f"{int(srcs.sum()):,} records became {total:,} people. Hover a stream."}


def sizes_band(runs: list[dict]) -> dict:
    """The pipeline measured at four sizes. At 1M, one wrongly deleted record was found and then fixed (details.md, Scale 7)."""
    sizes, label = {}, {20_000: "20k", 100_000: "100k", 300_000: "300k", 1_000_000: "1M"}
    for r in runs:
        wrong = r["deletion"]["records_of_people_who_never_asked"]
        k = label.get(r["people"], f"{r['people']:,}")
        sizes[k] = {"f1": r["identity"]["f1"], "hh": r["households_f1"], "wrong": 0 if r["people"] == 1_000_000 else wrong,
                    "minutes": r["timing"]["pipeline_minutes"] if "timing" in r else 0,
                    "sub": {"wrong": f"records (measured {wrong}, then fixed)" if r["people"] == 1_000_000 else "records of people who never asked",
                            "minutes": r.get("machine", "")}}
    return {"title": "The same pipeline, measured at", "start": "1M" if "1M" in sizes else list(sizes)[-1], "sizes": sizes,
            "metrics": [{"key": "f1", "label": "Identity F1", "fmt": "f3", "sub": "vs the answer key"},
                        {"key": "hh", "label": "Household F1", "fmt": "f3", "sub": "who lives with whom"},
                        {"key": "wrong", "label": "Wrongly deleted", "fmt": "int"},
                        {"key": "minutes", "label": "Whole pipeline", "fmt": "min"}]}


def worth(sv: dict, value: int = 2_000) -> dict:
    m, groups, vt, cov = sv["model"], sv["groups"], sv["vendor_vs_answer_key"], sv["identity_coverage"]
    color = {"property": GREEN, "vendor": GOLD, "store": NAVY, "email": "#7C8FE0"}
    out = []
    for g, w in m["without"].items():
        measured = w["ci95"][1] < 0
        lost = m["top_decile_buyers"] - w["top_decile_buyers"]
        why = (f"Without it the top decile finds {lost} fewer of {m['top_decile_buyers']} buyers; the whole 95% interval is below zero."
               if measured else "No inputs fall inside the training window, so nothing to measure." if not w["inputs"]
               else "Its interval spans zero: no measurable effect on this model, so it is not priced.")
        out.append({"label": groups[g]["label"], "cost": groups[g]["cost"], "auc_change": w["auc_change"], "lo": w["ci95"][0],
                    "hi": w["ci95"][1], "measured": measured, "lost_per_person": lost / m["test_people"] if measured else 0.0,
                    "color": color.get(g, BLUE), "why": why, "used": bool(w["inputs"])})
    h = vt["homeowner"]
    decisions = [
        {"label": "Property and census", "word": "Keep", "tone": "pass",
         "text": f"Free, and the model loses {abs(m['without']['property']['auc_change']):.3f} AUC without it: worth {{per10k}} per 10,000 people scored."},
        {"label": "Vendor's homeowner flag", "word": "Don't trust alone", "tone": "fail",
         "text": f"{h['said_owner_but_rents']:,} of the {h['said_owner']:,} people it calls owners rent ({h['said_owner_but_rents'] / h['said_owner']:.0%})."},
        {"label": "Vendor overall", "word": "Buy for reach", "tone": "info",
         "text": f"The only source for {cov['vendor']['only_source']:,} of {cov['people']:,} people. Ask for it on time: the on-time what-if reaches AUC 0.714."}]
    return {"value": value, "max_value": 10_000, "sources": out, "decisions": decisions,
            "hint": f"Each source removed, the model refit, scored on {m['test_people']:,} held-out people. Hover a source."}


def scale(plan: dict, label: dict) -> dict:
    stages = [{"key": k, "label": label.get(k, k), "rising": v["marginal_rising"],
               "next": next((o["next"] for o in plan["order_to_move"] if o["stage"] == k), "")}
              for k, v in plan["growth"]["stages"].items()]
    return {"title": "Past one machine: project to", "start": "1 TB", "targets": plan["targets"], "projected": plan["projected_minutes"],
            "stages": stages, "bytes_per_person": plan["bytes_per_person"],
            "hint": "Fitted on three runs on one machine; beyond 300,000 it is extrapolation. Hover a stage for where it moves."}


def gate(rg: dict, names: dict, detail) -> dict:
    out = []
    for k, label in names.items():
        r = rg[k]
        out.append({"label": label, "release": r["release"], "auc": rg["auc"][k],
                    "gates": [{"name": g["name"].replace("_", " "), "passed": g["passed"], "detail": detail(g)} for g in r["gates"]]})
    return {"challengers": out, "champion_auc": rg["auc"]["champion"],
            "hint": f"Same {rg['test_rows']:,} held-out people for every model. Hover a gate for its number."}


def game(con, run: Path, k: int = 12, seed: int = 7) -> dict:
    """Hard pairs the matcher scored, with the matcher's call and the answer key's."""
    from .queries import links
    truth = links(run).assign(uid=lambda d: d.source + ":" + d.record_id).set_index("uid").person_id
    recs = con.execute("SELECT * FROM identity_records").df().set_index("unique_id")
    pairs = con.execute("SELECT unique_id_l AS a, unique_id_r AS b, match_probability AS p FROM identity_pairs").df()
    pairs = pairs[pairs.a.isin(recs.index) & pairs.b.isin(recs.index) & pairs.a.isin(truth.index) & pairs.b.isin(truth.index)]
    pairs = pairs.assign(truth=lambda d: truth.loc[d.a].to_numpy() == truth.loc[d.b].to_numpy(),
                         matcher=lambda d: recs.loc[d.a, "person_key"].to_numpy() == recs.loc[d.b, "person_key"].to_numpy())
    rnd = random.Random(seed)
    wrong = pairs[pairs.truth != pairs.matcher]
    hard = pairs[(pairs.truth == pairs.matcher) & pairs.p.between(0.2, 0.97)]
    take = lambda df, n: df.sample(min(n, len(df)), random_state=rnd.randint(0, 10**6)) if len(df) else df
    pick = pd.concat([take(wrong, k // 3), take(hard, k - min(k // 3, len(wrong)))]).sample(frac=1, random_state=seed)
    out = []
    for r in pick.itertuples():
        a, b = recs.loc[r.a], recs.loc[r.b]
        side = lambda x: {"source": x.source, **{lab: ("" if pd.isna(x[col]) else str(x[col])) for lab, col in FIELDS.items()}}
        sa, sb = side(a), side(b)
        agree = {lab: "missing" if not sa[lab] or not sb[lab] else "agree" if sa[lab].strip().lower() == sb[lab].strip().lower() else "differ"
                 for lab in FIELDS}
        kind = ("The matcher got this one wrong" if r.truth != r.matcher else "A close call")
        out.append({"a": sa, "b": sb, "agree": agree, "p": float(r.p), "truth": bool(r.truth), "matcher": bool(r.matcher),
                    "kind": kind, "why": why(bool(r.truth), bool(r.matcher), float(r.p))})
    return {"pairs": out, "fields": list(FIELDS)}


def why(truth: bool, matcher: bool, p: float) -> str:
    """One line on how the matcher's pair score and its final call can differ."""
    if truth and not matcher:
        return ("The pair scored high, but a safety rule refused the join (for example, two dates of birth disagree): a missed match."
                if p >= 0.5 else "Same person, different details: typos, a move, a new email.")
    if not truth and matcher:
        return ("Different people joined through other records that matched." if p < 0.5
                else "Different people who share a lot: family, a shared home or inbox.")
    if p >= 0.5 and not matcher:
        return "The pair leaned towards one person; the guards kept them apart, rightly."
    if p < 0.5 and matcher:
        return "A weak pair, joined rightly through the person's other records."
    return ""


def city(con, run: Path, k: int = 140, seed: int = 11) -> dict:
    """The live pipeline view: real totals for the whole city, and a sample of resolved people with the
    records each was built from, checked against the answer key (a person holding a stranger's record is
    marked, not hidden)."""
    from .queries import links
    ledger = con.execute("""SELECT source, sum(rows_in), sum(rows_loaded), sum(coalesce(rows_quarantined, 0)),
                                   sum(coalesce(rows_suppressed, 0)), sum(coalesce(rows_already_present, 0))
                            FROM ingest_ledger GROUP BY 1""").fetchall()
    totals = {"rows_in": {}, "loaded": 0, "quarantined": 0, "suppressed": 0, "already": 0}
    for src, rin, lo, q, sup, al in ledger:
        totals["rows_in"][src] = int(rin)
        totals["loaded"] += int(lo)
        totals["quarantined"] += int(q)
        totals["suppressed"] += int(sup)
        totals["already"] += int(al)
    totals["records"], totals["people"] = (int(x) for x in con.execute(
        "SELECT count(*), count(DISTINCT person_key) FROM identity_records").fetchone())
    totals["households"] = int(con.execute("SELECT count(*) FROM products.dim_household").fetchone()[0])
    totals["audience"] = int(con.execute("SELECT count(*) FROM products.mart_activation_audience").fetchone()[0])
    reasons = [{"source": s, "reason": r, "rows": int(n)} for s, r, n in con.execute(
        "SELECT source, reason, count(*) FROM quarantine GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 6").fetchall()]
    held = [{"source": s, "reason": r.replace("_", " "), "row": int(n), "delivery": str(d)[:10]} for s, r, n, d in con.execute(
        "SELECT source, reason, row_number, delivery_id FROM quarantine ORDER BY source, row_number LIMIT 40").fetchall()]

    recs = con.execute("""SELECT person_key, source, record_id, first_name, last_name, email, house, street, zip
                          FROM identity_records""").df()
    owner = links(run).assign(uid=lambda d: d.source + ":" + d.record_id).set_index("uid").person_id
    recs["truth"] = (recs.source + ":" + recs.record_id).map(owner)
    g = recs.groupby("person_key")
    n_src = g.source.nunique()
    rnd = random.Random(seed)
    # More multi-source people than their share: that is where the joining happens. A few mistakes on purpose.
    mixed = [p for p, t in g.truth.nunique().items() if t > 1]
    pools = [list(n_src[n_src == 3].index), list(n_src[n_src == 2].index), list(n_src[n_src == 1].index)]
    want = [int(k * 0.45), int(k * 0.35), k]
    chosen = rnd.sample(mixed, min(4, len(mixed)))
    for pool, w in zip(pools, want):
        pool = [p for p in pool if p not in chosen]
        chosen += rnd.sample(pool, min(len(pool), max(0, min(w, k - len(chosen)))))
    borough = _zip_borough()
    people = []
    for p in chosen[:k]:
        rows = g.get_group(p)
        bz = rows.zip.dropna().astype(str).str[:5].map(borough).dropna()
        clean = lambda v: "" if pd.isna(v) else str(v)
        first = rows.first_name.dropna().astype(str).str.title().mode()
        last = rows.last_name.dropna().astype(str).str.title().mode()
        people.append({"name": f"{first.iloc[0] if len(first) else ''} {last.iloc[0] if len(last) else ''}".strip() or "unnamed",
                       "mixed": bool(rows.truth.nunique() > 1), "borough": bz.mode().iloc[0] if len(bz) else None,
                       "records": [{"source": r.source, "name": f"{clean(r.first_name)} {clean(r.last_name)}".strip(),
                                    "email": clean(r.email), "address": f"{clean(r.house)} {clean(r.street)} {clean(r.zip)}".strip()}
                                   for r in rows.itertuples()]})
    zips = recs.zip.dropna().astype(str).str[:5].map(borough)
    share = {b: round(float(n), 4) for b, n in zips.value_counts(normalize=True).items()}
    return {"totals": totals, "reasons": reasons, "held": held, "people": people, "borough_share": share,
            "sources": {s: {"label": SOURCE.get(s, (s, GREY))[0], "color": SOURCE.get(s, (s, GREY))[1]} for s in totals["rows_in"]}}


def threshold(con, run: Path, prod: float, bins: int = 60) -> dict:
    """Every pair the matcher scored, by its match probability, split by the answer key: the same person or
    two people. Pair level, before clustering and the safety rules production adds on top."""
    import math

    import numpy as np
    from .queries import links
    owner = links(run).assign(uid=lambda d: d.source + ":" + d.record_id).set_index("uid").person_id
    pairs = con.execute("""SELECT p.unique_id_l AS a, p.unique_id_r AS b, p.match_probability AS p,
                                  l.person_key = r.person_key AS joined
                           FROM identity_pairs p JOIN identity_records l ON l.unique_id = p.unique_id_l
                                                 JOIN identity_records r ON r.unique_id = p.unique_id_r""").df()
    pairs = pairs[pairs.a.isin(owner.index) & pairs.b.isin(owner.index)]
    same = owner.loc[pairs.a].to_numpy() == owner.loc[pairs.b].to_numpy()
    over = (pairs.p >= prod).to_numpy()
    joined = pairs.joined.fillna(False).astype(bool).to_numpy()
    # The axis is the pair's match probability on a log-odds scale (base 2), the same quantity production
    # thresholds at 0.9; Splink's match weight leaves out the prior, so it is not that scale.
    pc = pairs.p.clip(1e-6, 1 - 1e-6)
    w = np.log2(pc / (1 - pc)).to_numpy()
    lo, hi = max(-20.0, math.floor(w.min())), min(20.0, math.ceil(w.max()))
    w = np.clip(w, lo, hi)
    cut = math.log2(prod / (1 - prod))
    # An edge exactly at production's threshold, so the counts there are the same pairs the cards count.
    edges = np.unique(np.append(np.linspace(lo, hi, bins + 1), cut))
    s, _ = np.histogram(w[same], edges)
    d, _ = np.histogram(w[~same], edges)
    return {"edges": [float(x) for x in edges], "same": s.astype(int).tolist(), "diff": d.astype(int).tolist(),
            "prod": float(cut), "prod_p": prod, "pairs": int(len(pairs)),
            "same_total": int(same.sum()), "diff_total": int((~same).sum()),
            # What production does at its threshold, after clustering and the safety rules (it ran once):
            "rules": {"score_says_join_two_people": int((over & ~same).sum()), "joined_two_people": int((joined & ~same).sum()),
                      "same_person_kept_apart": int((same & ~joined).sum()), "same_person_pairs": int(same.sum())}}


def funnel(a: dict, channel_label: str | None) -> dict:
    """Everyone, then the segment, then each reason consent or contact removes someone; the last step is
    who can be reached. Each step is the one before minus the people it names."""
    steps = [{"label": "Everyone in the audience mart", "value": a["population"], "tone": "info"},
             {"label": "Match the rules", "value": a["segment"], "tone": "info",
              "note": f"{a['population'] - a['segment']:,} do not match"}]
    left = a["segment"]
    if len(a["excluded"]):
        for reason, n in a["excluded"].reason.value_counts().items():
            left -= int(n)
            steps.append({"label": f"minus: {reason}", "value": left, "tone": "fail", "note": f"{int(n):,} kept out"})
    steps.append({"label": f"Reachable{' by ' + channel_label if channel_label else ''}", "value": a["reachable"], "tone": "pass"})
    return {"steps": steps}


def activity_events(r: "pd.DataFrame") -> dict:
    """This visitor's own actions, oldest first, with a short result line each."""
    import json as _json
    out = []
    for x in r.sort_values("at").itertuples():
        res = x.result if isinstance(x.result, str) else _json.dumps(x.result)
        out.append({"at": str(x.at)[:19], "action": str(x.action), "kind": str(x.action).split(":")[0], "result": res[:240]})
    return {"events": out}


def _zip_borough() -> dict:
    """ZIP to borough from the app's real PLUTO lots (the borough most of a ZIP's lots are in)."""
    from .demo import REFERENCE
    lots = pd.read_csv(REFERENCE / "pluto_lots.csv", dtype=str, usecols=["zipcode", "borough"])
    lots = lots.assign(z=lots.zipcode.str[:5]).dropna(subset=["z", "borough"])
    return lots.groupby("z").borough.agg(lambda b: b.mode().iloc[0]).to_dict()


def people_explorer(con, run: Path, outcomes: "pd.DataFrame", k: int = 30) -> dict:
    """People to open, of three kinds (found despite typos, split, merged): each one's records as the sources
    sent them, the pipeline's golden profile with every competing value, and the answer key's verdict."""
    from .queries import SOURCE_LABEL, people, person_records, showcase, strangers_in
    back = {v: k for k, v in SOURCE_LABEL.items()}
    show = showcase(outcomes, k=k)
    ppl = people(run).set_index("person_id")
    o = outcomes.set_index("person_id")
    out = []
    for kind, pids in show.items():
        for pid in pids:
            row = o.loc[pid]
            keys = list(row["keys"])
            recs = person_records(con, run, pid)
            strangers = strangers_in(con, run, keys, pid)
            vals = con.execute("""SELECT v.person_key, v.attr, v.value, v.observations, v.sources, v.best_source,
                                         strftime(v.first_observed_at, '%Y-%m-%d') AS first_seen, strftime(v.last_observed_at, '%Y-%m-%d') AS last_seen,
                                         w.value IS NOT NULL AS won
                                  FROM products.int_attribute_values v
                                  LEFT JOIN products.int_attribute_winners w USING (person_key, attr, value)
                                  WHERE list_contains(?, v.person_key) ORDER BY v.person_key, v.attr, won DESC, v.last_observed_at DESC""",
                               [keys]).df()
            clean = lambda v: "" if pd.isna(v) else str(v)
            t = ppl.loc[pid]
            out.append({"id": pid, "kind": kind, "name": f"{t.first_name} {t.last_name}", "keys": keys,
                        "records": [{"source": back.get(r.kind, r.kind), "label": r.kind, "id": clean(r.record_id), "name": clean(r.name), "email": clean(r.email),
                                     "phone": clean(r.phone), "address": clean(r.address), "person_key": clean(r.person_key)}
                                    for r in recs.itertuples()],
                        "strangers": [{"source": s.source, "really": s.really, "person_key": s.person_key} for s in strangers.itertuples()],
                        "fields": [{"person_key": v.person_key, "attr": v.attr, "value": clean(v.value).replace("|", ", "),
                                    "observations": int(v.observations), "sources": int(v.sources), "source": v.best_source,
                                    "first": v.first_seen, "last": v.last_seen, "won": bool(v.won)} for v in vals.itertuples()]})
    return {"people": out, "rule": "Most fields: the value vouched for most recently wins; a tie goes to the more trusted source. "
                                   "Address: the newest address still being confirmed wins, because a stale source keeps "
                                   "echoing the old home after a move."}


def lineage(con, g: dict) -> dict:
    """The saved lineage graph with today's row count on every node, read from this warehouse."""
    deliveries = {"CRM files": "crm", "Vendor files": "vendor", "Email event files": "email", "Store API": "store",
                  "Privacy requests": "privacy"}
    sent = dict(con.execute("SELECT source, sum(rows_in) FROM ingest_ledger GROUP BY 1").fetchall())
    nodes = []
    for n in g["nodes"]:
        rows = None
        if n["id"] in deliveries:
            rows = sent.get(deliveries[n["id"]])
        else:
            for t in (f"products.{n['id']}", n["id"], f"main.{n['id']}"):
                try:
                    rows = con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                    break
                except Exception:
                    continue
        nodes.append({**n, "rows": None if rows is None else int(rows)})
    return {**g, "nodes": nodes}


AGENT_SCENES = [("U0ADM0001", "Is the pipeline healthy?"), ("U0ADM0001", "What's in the deletion review queue?"),
                ("U0ADM0001", "What does one_key_matches mean?"), ("U0ADM0001", "What is our parental leave policy?"),
                ("U0OPS0001", "Give U0NEW0001 the privacy role"), ("U0ADM0001", "Give U0NEW0002 the docs role")]


def agent_scenes(d, scenes=AGENT_SCENES) -> dict:
    """Each question really run through the agent (keyword routing, no model) on a private copy. A change goes
    the whole way: proposed, the asker tries to approve it, a second admin approves it."""
    import tempfile
    from ..agents.agent import handle
    from .queries import agent_context, example_roles
    ctx = agent_context(d, Path(tempfile.mkdtemp(prefix="plumbline-scenes-")), use_model=False)
    roles = example_roles()

    def rep(who, q):
        r = handle(ctx, who, q, channel="app")
        return {"who": who, "roles": sorted(roles.get(who, [])), "asked": q, "skill": r.skill, "tool": r.tool,
                "sources": list(r.sources), "refused": bool(r.refused), "proposals": list(r.proposals), "text": r.text[:900]}
    out = []
    for who, q in scenes:
        steps = [rep(who, q)]
        for pid in steps[0]["proposals"]:
            steps.append(rep(who, f"approve {pid}"))                       # the asker cannot approve their own request
            other = next((u for u, r in sorted(roles.items()) if "admin" in r and u != who), None)
            if other:
                steps.append(rep(other, f"approve {pid}"))
        out.append({"question": q, "steps": steps})
    return {"scenes": out, "roles": {u: sorted(r) for u, r in roles.items()}}
